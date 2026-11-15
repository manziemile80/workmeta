/**
 * Messages exchanged inside the extension.
 *
 * The content script never talks to the API: it only reads the page and hands
 * the result to the background worker. That keeps the session token and the
 * API reachable from exactly one place, and it means a hostile page cannot
 * ask the extension to fetch an arbitrary URL.
 */

import type { LoginResponse } from "./types.ts";

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
  | { kind: "read-page" };

export interface SessionState {
  signedIn: boolean;
  user: LoginResponse["user"] | null;
  organization: LoginResponse["active_organization"];
  memberships: LoginResponse["memberships"];
  /** The page the agent currently looks at, when it may read it. */
  page: { url: string; title: string } | null;
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
