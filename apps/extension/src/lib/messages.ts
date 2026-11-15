/**
 * Messages exchanged inside the extension.
 *
 * The content script never talks to the API: it only reads the page and hands
 * the result to the background worker. That keeps the session token and the
 * API reachable from exactly one place, and it means a hostile page cannot
 * ask the extension to fetch an arbitrary URL.
 */

import type {
  ActionType,
  LoginResponse,
  ProcedureStep,
  SubmodelActionDecision,
  SubmodelActionPlan,
  SubmodelGuidanceResponse,
  SubmodelSummary,
} from "./types.ts";

/** Sent by the content script to the background worker. */
export type ContentMessage = {
  kind: "page-context";
  /** The page the user is on. The worker checks it against the client's domains. */
  url: string;
  title: string;
};

/** Sent by the side panel to the background worker. */
export type PanelMessage =
  | { kind: "sign-in"; email: string; password: string }
  | { kind: "sign-out" }
  | { kind: "session" }
  | { kind: "read-page" }
  | { kind: "submodels" }
  | { kind: "submodel-guidance"; submodelId: string; question: string }
  | { kind: "submodel-propose-action"; submodelId: string; actionType: ActionType; description: string; steps: ProcedureStep[] }
  | { kind: "submodel-confirm-action"; submodelId: string; planId: string; confirmed: boolean }
  | { kind: "submodel-complete-action"; submodelId: string; planId: string; succeeded: boolean; result: Record<string, unknown> };

export interface SessionState {
  signedIn: boolean;
  user: LoginResponse["user"] | null;
  organization: LoginResponse["active_organization"];
  memberships: LoginResponse["memberships"];
  /** The page the agent currently looks at, when it may read it. */
  page: { url: string; title: string } | null;
  /**
   * The trained models this organization can use.
   *
   * A submodel may have been trained here or installed from a package; either
   * way it carries its own workflows.
   */
  submodels: SubmodelSummary[];
  /** The last answer from a submodel. */
  submodelGuidance: SubmodelGuidanceResponse | null;
  /** The submodel action plan waiting for the user's confirmation. */
  pendingSubmodelAction: {
    decision: SubmodelActionDecision;
    submodelId: string;
  } | null;
  /** The last submodel action the agent reported. */
  lastSubmodelAction: SubmodelActionPlan | null;
  /** The client this build was packaged for. */
  clientName: string;
  /** Whether this build's own configuration was verified. */
  configChecked: boolean;
  /** Why the configuration was not verified, empty when it was. */
  configReason: string;
  error: string | null;
}

export interface WorkerResponse<T = unknown> {
  ok: boolean;
  data?: T;
  error?: string;
  code?: string;
}
