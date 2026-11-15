/**
 * The agent's client for the WorkMeta AI API.
 *
 * Every call is made from the background service worker, never from a page,
 * so the session token never reaches a web page. The extension uses a bearer
 * token from an `kind: "extension"` login; cookies are not used, because the
 * extension is not a browser session.
 */

import type {
  ActionType,
  LoginResponse,
  PageContext,
  ProcedureStep,
  SubmodelActionDecision,
  SubmodelActionPlan,
  SubmodelGuidanceResponse,
  SubmodelSummary,
} from "./types.ts";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId = "",
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
}

export interface AgentConfig {
  /** Root of the API, including the version segment. */
  apiBaseUrl: string;
  /** The organization the installed agent acts for. */
  orgId: string;
  /** Host patterns the agent may read, one per trained system. */
  allowedDomains: string[];
}

export class WorkMetaClient {
  private readonly baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl: string, token: string | null = null) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  setToken(token: string | null): void {
    this.token = token;
  }

  getToken(): string | null {
    return this.token;
  }

  private async request<T>(
    path: string,
    init: { method?: string; body?: unknown; auth?: boolean } = {},
  ): Promise<T> {
    const method = (init.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = { Accept: "application/json" };
    if (init.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (init.auth !== false && this.token) {
      headers.Authorization = `Bearer ${this.token}`;
    }

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
      });
    } catch {
      throw new ApiError(
        0,
        "network_unreachable",
        "Cannot reach the WorkMeta AI service.",
      );
    }

    const parsed = (await response.json().catch(() => null)) as
      | (T & ErrorEnvelope)
      | null;

    if (!response.ok) {
      const detail = parsed?.error;
      throw new ApiError(
        response.status,
        detail?.code ?? "request_failed",
        detail?.message ?? `Request failed with status ${response.status}.`,
        detail?.request_id ?? "",
      );
    }
    if (parsed === null) {
      throw new ApiError(
        response.status,
        "invalid_response",
        "The API returned an unreadable response.",
      );
    }
    return parsed;
  }

  /**
   * Sign in as an extension client.
   *
   * `kind: "extension"` returns a bearer token instead of a cookie, so the
   * agent never depends on the browser's cookie jar.
   */
  async login(
    email: string,
    password: string,
    orgId?: string | null,
  ): Promise<LoginResponse> {
    const response = await this.request<LoginResponse>("/auth/login", {
      method: "POST",
      auth: false,
      body: { email, password, kind: "extension", org_id: orgId ?? null },
    });
    this.token = response.access_token;
    return response;
  }

  me(): Promise<LoginResponse> {
    return this.request<LoginResponse>("/auth/me");
  }

  /**
   * Revoke this session on the server.
   *
   * Signing out removes the token from the browser, but the session itself
   * stays valid until it expires. Revoking it means a token that was copied
   * out of the profile no longer works.
   */
  async logout(): Promise<void> {
    try {
      await this.request<{ status: string }>("/auth/logout", { method: "POST" });
    } catch {
      // The session is removed locally either way. A server that cannot be
      // reached must not stop the user from signing out.
    }
  }

  switchOrganization(orgId: string | null): Promise<LoginResponse> {
    return this.request<LoginResponse>("/auth/switch-organization", {
      method: "POST",
      body: { org_id: orgId },
    });
  }

  // -------------------------------------------------------------------------
  // WorkMeta AI Submodels
  // -------------------------------------------------------------------------

  /**
   * The submodels this organization can use.
   *
   * A submodel is a trained model for one task. It may have been trained
   * here or installed from a package; either way it carries its own
   * workflows, so it works on this machine without training again.
   */
  submodels(orgId: string): Promise<SubmodelSummary[]> {
    return this.request<SubmodelSummary[]>(
      `/organizations/${orgId}/submodels`,
    );
  }

  /** Step-by-step guidance from one submodel's approved workflows. */
  submodelGuidance(
    orgId: string,
    submodelId: string,
    question: string,
    context?: PageContext,
  ): Promise<SubmodelGuidanceResponse> {
    return this.request<SubmodelGuidanceResponse>(
      `/organizations/${orgId}/submodels/${submodelId}/guidance`,
      { method: "POST", body: { question, context: context ?? {} } },
    );
  }

  /** Ask whether an action may be performed, under the submodel's policy. */
  submodelProposeAction(
    orgId: string,
    submodelId: string,
    actionType: ActionType,
    description: string,
    steps: ProcedureStep[] = [],
  ): Promise<SubmodelActionDecision> {
    return this.request<SubmodelActionDecision>(
      `/organizations/${orgId}/submodels/${submodelId}/actions`,
      {
        method: "POST",
        body: { action_type: actionType, description, steps },
      },
    );
  }

  /** Record the user's confirmation (or refusal) of a sensitive action. */
  submodelConfirmAction(
    orgId: string,
    submodelId: string,
    planId: string,
    confirmed: boolean,
  ): Promise<SubmodelActionPlan> {
    return this.request<SubmodelActionPlan>(
      `/organizations/${orgId}/submodels/${submodelId}/actions/${planId}/confirm`,
      { method: "POST", body: { confirmed } },
    );
  }

  /** Report that the action ran. */
  submodelCompleteAction(
    orgId: string,
    submodelId: string,
    planId: string,
    succeeded: boolean,
    result: Record<string, unknown> = {},
  ): Promise<SubmodelActionPlan> {
    return this.request<SubmodelActionPlan>(
      `/organizations/${orgId}/submodels/${submodelId}/actions/${planId}/complete`,
      { method: "POST", body: { succeeded, result } },
    );
  }
}
