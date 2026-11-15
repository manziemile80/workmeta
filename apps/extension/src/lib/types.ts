/**
 * Contract between the installed agent and the WorkMeta AI API.
 *
 * These mirror the server-side schemas in `apps/api/app/schemas/`. The agent
 * never invents a value: everything it shows comes from one of these calls.
 */

export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
  country: string;
  status: string;
  created_at: string;
}

export interface MembershipSummary {
  organization: OrganizationSummary;
  role: string;
  status: string;
}

export interface UserSummary {
  id: string;
  email: string;
  full_name: string;
  is_platform_admin: boolean;
  status: string;
  last_login_at: string | null;
}

export interface LoginResponse {
  user: UserSummary;
  active_organization: OrganizationSummary | null;
  memberships: MembershipSummary[];
  session: { kind: string; expires_at: string };
  /** Only returned for `kind: "extension"` sessions. */
  access_token: string | null;
}

/** The untrusted description of the page the user is looking at. */
export interface PageContext {
  url?: string;
  title?: string;
  /** Short excerpt of visible text, never sent unless the user asks. */
  text?: string;
}
