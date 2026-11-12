/**
 * The agent's client for the WorkMeta AI API.
 *
 * Every call is made from the background service worker, never from a page,
 * so the session token never reaches a web page. The extension uses a bearer
 * token from an `kind: "extension"` login; cookies are not used, because the
 * extension is not a browser session.
 */

import type {
  ActionDecision,
  ActionPlan,
  ActionType,
  ExtensionSummary,
  GuidanceResponse,
  LoginResponse,
  PageContext,
  PackageResponse,
  ProcedureStep,
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

  /** Which published extension recognises the page the user is on. */
  recognize(orgId: string, url: string): Promise<ExtensionSummary[]> {
    return this.request<ExtensionSummary[]>(
      `/organizations/${orgId}/extensions/recognize?url=${encodeURIComponent(url)}`,
    );
  }

  /** Step-by-step guidance for one trained system. */
  guidance(
    orgId: string,
    extensionId: string,
    question: string,
    context?: PageContext,
  ): Promise<GuidanceResponse> {
    return this.request<GuidanceResponse>(
      `/organizations/${orgId}/extensions/${extensionId}/guidance`,
      { method: "POST", body: { question, context: context ?? {} } },
    );
  }

  /** Ask whether an action may be performed. */
  proposeAction(
    orgId: string,
    extensionId: string,
    actionType: ActionType,
    description: string,
    steps: ProcedureStep[] = [],
  ): Promise<ActionDecision> {
    return this.request<ActionDecision>(
      `/organizations/${orgId}/extensions/${extensionId}/actions`,
      {
        method: "POST",
        body: { action_type: actionType, description, steps },
      },
    );
  }

  /** Record the user's confirmation (or refusal) of a sensitive action. */
  confirmAction(
    orgId: string,
    extensionId: string,
    planId: string,
    confirmed: boolean,
  ): Promise<ActionPlan> {
    return this.request<ActionPlan>(
      `/organizations/${orgId}/extensions/${extensionId}/actions/${planId}/confirm`,
      { method: "POST", body: { confirmed } },
    );
  }

  /** Report that the action ran. */
  completeAction(
    orgId: string,
    extensionId: string,
    planId: string,
    succeeded: boolean,
    result: Record<string, unknown> = {},
  ): Promise<ActionPlan> {
    return this.request<ActionPlan>(
      `/organizations/${orgId}/extensions/${extensionId}/actions/${planId}/complete`,
      { method: "POST", body: { succeeded, result } },
    );
  }

  /** The signed package, with the exact bytes the signature covers. */
  package(orgId: string, extensionId: string): Promise<PackageResponse> {
    return this.request<PackageResponse>(
      `/organizations/${orgId}/extensions/${extensionId}/package`,
    );
  }
}
