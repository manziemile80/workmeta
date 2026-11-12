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

export interface ExtensionSummary {
  id: string;
  org_id: string;
  assistant_id: string;
  slug: string;
  name: string;
  system_code: string;
  system_name: string;
  system_version: string;
  target_kind: "web" | "desktop";
  url_patterns: string[];
  app_hints: Record<string, unknown>;
  status: string;
  training_status: string;
  permissions: {
    auto_actions: string[];
    confirm_actions: string[];
    denied_actions: string[];
  };
  version: number;
  published_at: string | null;
  package_signature: string | null;
  signing_key_id: string | null;
}

export interface ProcedureStep {
  order: number;
  instruction: string;
  where: string | null;
  value: string | null;
  expected_result: string | null;
  sensitive: boolean;
}

export interface GuidanceResponse {
  extension_id: string;
  system_name: string;
  system_version: string;
  answer: string;
  steps: Array<ProcedureStep & { procedure?: string }>;
  grounded: boolean;
  procedures_used: string[];
  model: string | null;
  usage: Record<string, number>;
}

export type ActionType =
  | "navigate"
  | "read"
  | "fill_form"
  | "download"
  | "submit_application"
  | "create_invoice"
  | "payment"
  | "delete_record"
  | "send"
  | "other";

export interface ActionPlan {
  id: string;
  org_id: string;
  extension_id: string;
  action_type: string;
  description: string;
  steps: ProcedureStep[];
  status: "proposed" | "confirmed" | "executed" | "rejected";
  requires_confirmation: boolean;
  confirmed_by: string | null;
  confirmed_at: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ActionDecision {
  plan: ActionPlan;
  allowed: boolean;
  requires_confirmation: boolean;
  reason: string;
}

/**
 * A WorkMeta AI Submodel: a trained model for one task.
 *
 * A submodel is portable — it carries its own workflows, so it works on a
 * machine it was never trained on.
 */
export interface SubmodelSummary {
  id: string;
  org_id: string;
  slug: string;
  name: string;
  description: string | null;
  task: string;
  system_code: string;
  system_name: string;
  system_version: string;
  target_kind: "web" | "desktop";
  status: string;
  training_status: string;
  /** `trained` here, `imported` when it came from a package. */
  origin: "trained" | "imported";
  permissions: {
    auto_actions: string[];
    confirm_actions: string[];
    denied_actions: string[];
  };
  version: number;
  approved_workflows_count: number;
}

export interface SubmodelGuidanceResponse {
  submodel_id: string;
  task: string;
  answer: string;
  steps: Array<ProcedureStep & { workflow?: string }>;
  grounded: boolean;
  workflows_used: string[];
  model: string | null;
  usage: Record<string, number>;
}

export interface SubmodelActionPlan {
  id: string;
  org_id: string;
  submodel_id: string;
  action_type: string;
  description: string;
  steps: ProcedureStep[];
  status: "proposed" | "confirmed" | "executed" | "rejected";
  requires_confirmation: boolean;
  confirmed_by: string | null;
  confirmed_at: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface SubmodelActionDecision {
  plan: SubmodelActionPlan;
  allowed: boolean;
  requires_confirmation: boolean;
  reason: string;
}

/** The signed manifest the client installs. */
export interface PackageManifest {
  schema: string;
  extension_id: string;
  slug: string;
  name: string;
  version: number;
  issued_at: string;
  system: {
    code: string;
    name: string;
    version: string;
    target_kind: "web" | "desktop";
    url_patterns: string[];
    app_hints: Record<string, unknown>;
  };
  permissions: {
    auto_actions: string[];
    confirm_actions: string[];
    denied_actions: string[];
  };
  assistant: { id: string; name: string };
  procedures: Array<{
    id: string;
    title: string;
    summary: string | null;
    steps: ProcedureStep[];
    confidence: number;
  }>;
}

export interface PackageResponse {
  extension_id: string;
  slug: string;
  version: number;
  manifest: PackageManifest;
  signature: string | null;
  signing_key_id: string | null;
  public_key: string | null;
  published_at: string | null;
  /** The exact bytes the signature covers, so the agent never re-serialises. */
  canonical_manifest: string | null;
}

/** The untrusted description of the page the user is looking at. */
export interface PageContext {
  url?: string;
  title?: string;
  /** Short excerpt of visible text, never sent unless the user asks. */
  text?: string;
}
