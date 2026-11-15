/** Typed API client for the WorkMeta AI backend. */

/**
 * Origin the API is served from.
 *
 * - `""` (the default) means "same origin as this page". The Vercel rewrite
 *   in `vercel.json` forwards `/api/*` to the backend, so requests stay
 *   first-party and the session cookie is never cross-site.
 * - An absolute origin (e.g. `https://workmeta-api.onrender.com`) calls the
 *   backend directly, which requires CORS and `SameSite=None; Secure` cookies.
 *
 * The request builder always appends `/api/v1`, so a redundant trailing
 * `/api` or `/api/v1` is stripped here.
 */
function normaliseBaseUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";

  // Path-style value (e.g. "/api"): a prefix on this same origin.
  if (trimmed.startsWith("/")) {
    const stripped = trimmed.replace(/\/+$/, "").replace(/\/api(\/v1)?$/, "");
    return stripped;
  }

  // Absolute URL: keep the origin, drop a redundant path suffix.
  try {
    const url = new URL(trimmed);
    url.pathname = url.pathname.replace(/\/+$/, "").replace(/\/api(\/v1)?$/, "");
    return url.toString().replace(/\/+$/, "");
  } catch {
    return trimmed.replace(/\/+$/, "");
  }
}

const API_BASE_URL: string = normaliseBaseUrl(
  import.meta.env.VITE_API_BASE_URL ?? "",
);

/** Versioned API root. The backend mounts every route under this prefix. */
const API_ROOT = "/api/v1";

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  uptime_seconds: number;
}

export interface ReadyResponse {
  status: "ready";
  database: "ok";
  pgvector: boolean;
  version: string;
  environment: string;
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    details?: Record<string, unknown>;
  };
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly details?: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId = "",
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

function readCsrfCookie(): string {
  if (typeof document === "undefined") return "";
  const parts = `; ${document.cookie}`.split("; wm_csrf=");
  if (parts.length < 2) return "";
  return decodeURIComponent(parts.pop() ?? "").split(";")[0] ?? "";
}

async function request<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    credentials?: RequestCredentials;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const method = (options.method ?? "GET").toUpperCase();
  let payload: BodyInit | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(options.body);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = readCsrfCookie();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${API_ROOT}${path}`, {
      headers,
      method,
      body: payload,
      credentials: options.credentials ?? "include",
    });
  } catch {
    throw new ApiRequestError(
      0,
      "network_unreachable",
      "Cannot reach the API. Is the backend running?",
    );
  }

  const parsed = (await response.json().catch(() => null)) as
    | (T & ErrorEnvelope)
    | null;

  if (!response.ok) {
    const detail = parsed?.error;
    throw new ApiRequestError(
      response.status,
      detail?.code ?? "request_failed",
      detail?.message ?? `Request failed with status ${response.status}.`,
      detail?.request_id ?? "",
      detail?.details,
    );
  }

  if (parsed === null) {
    throw new ApiRequestError(
      response.status,
      "invalid_response",
      "The API returned an unreadable response.",
    );
  }

  return parsed;
}

export const fetchHealth = (): Promise<HealthResponse> =>
  request<HealthResponse>("/health");

export const fetchReady = (): Promise<ReadyResponse> =>
  request<ReadyResponse>("/ready");

/**
 * OpenAPI browser. The backend serves `/docs` at its root, not under
 * `/api/v1`, so this cannot be built from `API_ROOT`.
 */
export const docsUrl = `${API_BASE_URL}/docs`;

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

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

export interface LoginResponse {
  user: {
    id: string;
    email: string;
    full_name: string;
    is_platform_admin: boolean;
    status: string;
    last_login_at: string | null;
  };
  active_organization: OrganizationSummary | null;
  memberships: MembershipSummary[];
  session: { kind: string; expires_at: string };
  access_token: string | null;
}

export function login(
  email: string,
  password: string,
  orgId?: string | null,
): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/login", {
    method: "POST",
    body: {
      email,
      password,
      kind: "web",
      org_id: orgId ?? null,
    },
  });
}

export function fetchMe(): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/me");
}

export function logout(): Promise<{ status: string }> {
  return request<{ status: string }>("/auth/logout", { method: "POST" });
}

export function switchOrganization(
  orgId: string | null,
): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/switch-organization", {
    method: "POST",
    body: { org_id: orgId },
  });
}

// ---------------------------------------------------------------------------
// Billing
// ---------------------------------------------------------------------------

export interface Plan {
  id: string;
  code: string;
  name: string;
  description: string | null;
  monthly_price_minor: number;
  annual_price_minor: number;
  currency: string;
  price_exponent: number;
  max_assistants: number | null;
  max_documents: number | null;
  max_storage_bytes: number | null;
  monthly_token_allowance: number | null;
  monthly_request_allowance: number | null;
  features: string[];
  is_active: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface Subscription {
  id: string;
  org_id: string;
  plan: Plan;
  billing_interval: string;
  status: string;
  provider: string;
  started_at: string;
  current_period_start: string;
  current_period_end: string;
  trial_ends_at: string | null;
  canceled_at: string | null;
  cancel_at_period_end: boolean;
  past_due_since: string | null;
  created_at: string;
  updated_at: string;
}

export interface QuotaSnapshot {
  org_id: string;
  plan_code: string;
  plan_name: string;
  subscription_status: string;
  billing_interval: string;
  period_start: string;
  period_end: string;
  tokens_used: number;
  token_allowance: number | null;
  requests_used: number;
  request_allowance: number | null;
  assistants_used: number;
  assistant_allowance: number | null;
  documents_used: number;
  document_allowance: number | null;
  storage_bytes_used: number;
  storage_allowance: number | null;
}

export interface BillingOverview {
  subscription: Subscription;
  usage: QuotaSnapshot;
}

export interface Payment {
  id: string;
  org_id: string;
  provider: string;
  provider_reference: string;
  kind: string;
  status: string;
  amount_minor: number;
  currency: string;
  billing_interval: string | null;
  checkout_url: string | null;
  invoice_number: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface CheckoutResponse {
  immediate: boolean;
  subscription: Subscription;
  plan: Plan;
  payment: Payment | null;
  checkout_url: string | null;
  provider: string;
  amount_minor: number;
  currency: string;
}

export interface ConfirmationResponse {
  activated: boolean;
  payment: Payment;
  subscription: Subscription;
  usage: QuotaSnapshot;
}

export function fetchPlans(): Promise<Plan[]> {
  return request<Plan[]>("/billing/plans");
}

export function fetchBilling(orgId: string): Promise<BillingOverview> {
  return request<BillingOverview>(`/organizations/${orgId}/billing`);
}

export function fetchBillingUsage(orgId: string): Promise<QuotaSnapshot> {
  return request<QuotaSnapshot>(`/organizations/${orgId}/billing/usage`);
}

export function checkout(
  orgId: string,
  planCode: string,
  interval: "monthly" | "annual",
): Promise<CheckoutResponse> {
  return request<CheckoutResponse>(`/organizations/${orgId}/billing/checkout`, {
    method: "POST",
    body: { plan_code: planCode, interval },
  });
}

export function confirmCheckout(
  orgId: string,
  reference: string,
): Promise<ConfirmationResponse> {
  return request<ConfirmationResponse>(
    `/organizations/${orgId}/billing/checkout/confirm`,
    { method: "POST", body: { reference } },
  );
}

export function startTrial(
  orgId: string,
  planCode: string,
  interval: "monthly" | "annual",
): Promise<CheckoutResponse> {
  return request<CheckoutResponse>(`/organizations/${orgId}/billing/trial`, {
    method: "POST",
    body: { plan_code: planCode, interval },
  });
}

export function cancelSubscription(orgId: string): Promise<Subscription> {
  return request<Subscription>(`/organizations/${orgId}/billing/cancel`, {
    method: "POST",
  });
}

export function renewSubscription(orgId: string): Promise<CheckoutResponse> {
  return request<CheckoutResponse>(`/organizations/${orgId}/billing/renew`, {
    method: "POST",
  });
}

export function fetchPayments(orgId: string): Promise<Payment[]> {
  return request<Payment[]>(`/organizations/${orgId}/billing/payments`);
}

export function fetchInvoices(orgId: string): Promise<Payment[]> {
  return request<Payment[]>(`/organizations/${orgId}/billing/invoices`);
}

// ---------------------------------------------------------------------------
// Platform administration
// ---------------------------------------------------------------------------

export interface PlatformOrganization {
  id: string;
  name: string;
  slug: string;
  country: string;
  status: string;
  created_at: string;
  subscription_id: string | null;
  plan_code: string | null;
  plan_name: string | null;
  subscription_status: string | null;
  billing_interval: string | null;
  current_period_end: string | null;
}

export interface PlatformSubscription {
  id: string;
  org_id: string;
  organization_name: string;
  organization_slug: string;
  plan_code: string;
  plan_name: string;
  billing_interval: string;
  status: string;
  provider: string;
  current_period_start: string;
  current_period_end: string;
  trial_ends_at: string | null;
  cancel_at_period_end: boolean;
  past_due_since: string | null;
  created_at: string;
}

export interface RevenueSummary {
  currency: string;
  gross_minor: number;
  refunds_minor: number;
  net_minor: number;
  payments_count: number;
  refunds_count: number;
  by_plan: {
    plan_code: string;
    plan_name: string;
    gross_minor: number;
    payments_count: number;
  }[];
}

export interface PlatformUsageSummary {
  since: string;
  until: string;
  total_requests: number;
  total_tokens: number;
  total_cost_micros: number;
  organizations: {
    org_id: string;
    organization_name: string;
    organization_slug: string;
    requests: number;
    tokens_in: number;
    tokens_out: number;
    cost_micros: number;
  }[];
}

export function fetchPlatformOrganizations(): Promise<
  PlatformOrganization[]
> {
  return request<PlatformOrganization[]>("/platform/organizations");
}

export function fetchPlatformPlans(): Promise<Plan[]> {
  return request<Plan[]>("/platform/plans");
}

export function fetchPlatformSubscriptions(): Promise<PlatformSubscription[]> {
  return request<PlatformSubscription[]>("/platform/subscriptions");
}

export function fetchPlatformPayments(): Promise<Payment[]> {
  return request<Payment[]>("/platform/payments?limit=100");
}

export function fetchPlatformRevenue(): Promise<RevenueSummary> {
  return request<RevenueSummary>("/platform/revenue");
}

export function fetchPlatformUsage(): Promise<PlatformUsageSummary> {
  return request<PlatformUsageSummary>("/platform/usage");
}

export function createPlatformPlan(body: Record<string, unknown>): Promise<Plan> {
  return request<Plan>("/platform/plans", { method: "POST", body });
}

export function updatePlatformPlan(
  planId: string,
  body: Record<string, unknown>,
): Promise<Plan> {
  return request<Plan>(`/platform/plans/${planId}`, {
    method: "PATCH",
    body,
  });
}

export function refundPlatformPayment(paymentId: string): Promise<Payment> {
  return request<Payment>(`/platform/payments/${paymentId}/refund`, {
    method: "POST",
  });
}

export function formatMoney(
  amountMinor: number,
  currency: string,
  exponent = 0,
): string {
  const scale = 10 ** exponent;
  const value = amountMinor / scale;
  try {
    return new Intl.NumberFormat("en-RW", {
      style: "currency",
      currency,
      maximumFractionDigits: exponent,
    }).format(value);
  } catch {
    return `${value.toFixed(exponent)} ${currency}`;
  }
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "Unlimited";
  if (bytes === 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** i).toFixed(1)} ${units[i]}`;
}

// ---------------------------------------------------------------------------
// Submodels (admin-controlled AI submodels)
// ---------------------------------------------------------------------------

export interface Submodel {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  purpose: string | null;
  status: string;
  version: number;
  config: Record<string, unknown>;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  video_count: number;
  workflow_count: number;
}

export interface SubmodelVideo {
  id: string;
  submodel_id: string;
  title: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: string;
  error_message: string | null;
  duration_seconds: number | null;
  frame_count: number | null;
  transcript: string | null;
  analysis: Record<string, unknown>;
  created_at: string;
}

export interface SubmodelWorkflow {
  id: string;
  submodel_id: string;
  source_video_id: string | null;
  name: string;
  description: string | null;
  steps: Array<Record<string, unknown>>;
  version: number;
  created_at: string;
}

export interface SubmodelJob {
  id: string;
  submodel_id: string;
  video_id: string | null;
  kind: string;
  status: string;
  progress: number;
  message: string | null;
  error: string | null;
  result: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface SubmodelVersion {
  id: string;
  submodel_id: string;
  version: number;
  manifest: Record<string, unknown>;
  has_package: boolean;
  created_at: string;
}

export interface SubmodelTestResponse {
  submodel_id: string;
  answer: string;
  followed_workflows: string[];
  model: string;
}

export const listPublishedSubmodels = (): Promise<Submodel[]> =>
  request<Submodel[]>("/submodels");

export const listAllSubmodels = (): Promise<Submodel[]> =>
  request<Submodel[]>("/submodels/admin/all");

export const createSubmodel = (body: {
  name: string;
  slug?: string;
  description?: string;
  purpose?: string;
  config?: Record<string, unknown>;
}): Promise<Submodel> =>
  request<Submodel>("/submodels/admin", { method: "POST", body: JSON.stringify(body) });

export const updateSubmodel = (
  id: string,
  body: Record<string, unknown>,
): Promise<Submodel> =>
  request<Submodel>(`/submodels/admin/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const deleteSubmodel = (id: string): Promise<void> =>
  request<void>(`/submodels/admin/${id}`, { method: "DELETE" });

export const publishSubmodel = (id: string): Promise<Submodel> =>
  request<Submodel>(`/submodels/admin/${id}/publish`, { method: "POST" });

export const unpublishSubmodel = (id: string): Promise<Submodel> =>
  request<Submodel>(`/submodels/admin/${id}/unpublish`, { method: "POST" });

export const listSubmodelVideos = (id: string): Promise<SubmodelVideo[]> =>
  request<SubmodelVideo[]>(`/submodels/admin/${id}/videos`);

export const uploadSubmodelVideo = async (
  id: string,
  file: File,
  title?: string,
): Promise<SubmodelVideo> => {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  const response = await fetch(`${API_BASE_URL}${API_ROOT}/submodels/admin/${id}/videos`, {
    method: "POST",
    body: form,
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiRequestError(response.status, "upload_failed", await response.text());
  }
  return (await response.json()) as SubmodelVideo;
};

export const deleteSubmodelVideo = (videoId: string): Promise<void> =>
  request<void>(`/submodels/admin/videos/${videoId}`, { method: "DELETE" });

export const analyzeSubmodelVideo = (
  submodelId: string,
  videoId: string,
): Promise<SubmodelJob> =>
  request<SubmodelJob>(`/submodels/admin/${submodelId}/videos/${videoId}/analyze`, {
    method: "POST",
  });

export const trainSubmodel = (id: string): Promise<SubmodelJob> =>
  request<SubmodelJob>(`/submodels/admin/${id}/train`, { method: "POST" });

export const testSubmodel = (
  id: string,
  instruction: string,
): Promise<SubmodelTestResponse> =>
  request<SubmodelTestResponse>(`/submodels/admin/${id}/test`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });

export const listSubmodelJobs = (id: string): Promise<SubmodelJob[]> =>
  request<SubmodelJob[]>(`/submodels/admin/${id}/jobs`);

export const getSubmodelJob = (jobId: string): Promise<SubmodelJob> =>
  request<SubmodelJob>(`/submodels/admin/jobs/${jobId}`);

export const listSubmodelWorkflows = (id: string): Promise<SubmodelWorkflow[]> =>
  request<SubmodelWorkflow[]>(`/submodels/admin/${id}/workflows`);

export const listSubmodelVersions = (id: string): Promise<SubmodelVersion[]> =>
  request<SubmodelVersion[]>(`/submodels/admin/${id}/versions`);

export const downloadSubmodel = async (id: string): Promise<void> => {
  const response = await fetch(`${API_BASE_URL}${API_ROOT}/submodels/admin/${id}/download`, {
    credentials: "include",
  });
  if (!response.ok) throw new ApiRequestError(response.status, "download_failed", await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "submodel.zip";
  a.click();
  URL.revokeObjectURL(url);
};

export const installSubmodel = async (file: File): Promise<Submodel> => {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE_URL}${API_ROOT}/submodels/admin/install`, {
    method: "POST",
    body: form,
    credentials: "include",
  });
  if (!response.ok) throw new ApiRequestError(response.status, "install_failed", await response.text());
  return (await response.json()) as Submodel;
};

export const useSubmodel = (
  id: string,
  instruction: string,
): Promise<SubmodelTestResponse> =>
  request<SubmodelTestResponse>(`/submodels/${id}/chat`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
