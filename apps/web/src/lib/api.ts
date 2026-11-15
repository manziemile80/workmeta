/** Typed API client for the WorkMeta AI backend. */

/**
 * Root of the backend API, including the version segment.
 *
 * In production the frontend is served from the same origin as the API and
 * Vercel proxies `/api/*` to Render, so the default `/api/v1` works without
 * any configuration. `VITE_API_BASE_URL` can point somewhere else (for
 * example `http://127.0.0.1:8000/api/v1` during local development).
 */
const API_BASE_URL: string = (
  import.meta.env.VITE_API_BASE_URL ?? "/api/v1"
).replace(/\/+$/, "");

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  uptime_seconds: number;
  database_configured: boolean;
  docs_enabled: boolean;
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
    // FormData carries its own multipart boundary: the browser has to set the
    // content type itself.
    if (options.body instanceof FormData) {
      payload = options.body;
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(options.body);
    }
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = readCsrfCookie();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers,
      method,
      body: payload,
      credentials: options.credentials ?? "include",
    });
  } catch {
    throw new ApiRequestError(
      0,
      "network_unreachable",
      "Cannot reach the API. Is the backend running on port 8000?",
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
 * Location of the interactive API documentation.
 *
 * The docs are served from the backend root (not below `/api/v1`) and are
 * disabled in production, so the client only shows the link when
 * `/api/v1/health` reports `docs_enabled`.
 */
export const docsUrl: string = `${API_BASE_URL.replace(/\/api\/v1$/, "")}/docs`;

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

export interface RegisterPayload {
  full_name: string;
  email: string;
  password: string;
  organization_name: string;
  country: string;
}

/**
 * Create an account, its first organization and the Free subscription.
 *
 * The API signs the new person in, so the response carries the same session
 * payload as `login`.
 */
export function registerAccount(
  payload: RegisterPayload,
): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/register", {
    method: "POST",
    body: payload,
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

export interface AssistantSummary {
  id: string;
  name: string;
  slug: string;
  status: string;
}

export function fetchAssistants(orgId: string): Promise<AssistantSummary[]> {
  return request<AssistantSummary[]>(`/organizations/${orgId}/assistants`);
}

// ---------------------------------------------------------------------------
// Training steps, shared by every workflow on the platform
// ---------------------------------------------------------------------------

export interface ProcedureStep {
  order: number;
  instruction: string;
  where?: string | null;
  value?: string | null;
  expected_result?: string | null;
  sensitive?: boolean;
}

// ---------------------------------------------------------------------------
// WorkMeta AI Submodels
// ---------------------------------------------------------------------------

export interface Submodel {
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
  origin: "trained" | "imported";
  source_label: string | null;
  source_digest: string | null;
  permissions: Record<string, unknown>;
  capabilities: unknown[];
  version: number;
  package_signature: string | null;
  signing_key_id: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  materials_count: number;
  workflows_count: number;
  approved_workflows_count: number;
  tests_count: number;
  passed_tests_count: number;
}

export interface SubmodelCreate {
  name: string;
  task: string;
  system_code: string;
  system_name: string;
  system_version: string;
  description?: string | null;
  target_kind?: "web" | "desktop";
  permissions?: Record<string, unknown>;
}

export interface SubmodelMaterial {
  id: string;
  org_id: string;
  submodel_id: string;
  kind: "video" | "audio" | "document" | "note";
  title: string;
  filename: string | null;
  mime_type: string | null;
  size_bytes: number;
  status: string;
  extracted_text: string | null;
  extracted_units: number | null;
  error_message: string | null;
  created_at: string;
}

export interface SubmodelWorkflow {
  id: string;
  org_id: string;
  submodel_id: string;
  title: string;
  summary: string | null;
  steps: ProcedureStep[];
  source_material_ids: string[];
  confidence: number;
  status: "draft" | "approved" | "rejected";
  position: number;
  created_at: string;
  updated_at: string;
}

export interface SubmodelTest {
  id: string;
  org_id: string;
  submodel_id: string;
  question: string;
  expected_points: string[];
  answer: string | null;
  covered_points: string[];
  missed_points: string[];
  score: number;
  outcome: "passed" | "failed" | "error";
  created_at: string;
}

export interface SubmodelPackage {
  filename: string;
  digest: string;
  signing_key_id: string;
  verified: boolean;
  reason: string;
  workflow_count: number;
  payload: Record<string, unknown>;
}

export interface SubmodelTrainingResult {
  submodel_id: string;
  training_status: string;
  workflows: SubmodelWorkflow[];
  analysed_materials: number;
  analysed_characters: number;
  message: string;
}

export interface SubmodelGuidance {
  submodel_id: string;
  task: string;
  answer: string;
  steps: ProcedureStep[];
  grounded: boolean;
  workflows_used: string[];
  model: string | null;
  usage: Record<string, number>;
}

export function fetchSubmodels(orgId: string): Promise<Submodel[]> {
  return request<Submodel[]>(`/organizations/${orgId}/submodels`);
}

export function createSubmodel(
  orgId: string,
  payload: SubmodelCreate,
): Promise<Submodel> {
  return request<Submodel>(`/organizations/${orgId}/submodels`, {
    method: "POST",
    body: payload,
  });
}

export function updateSubmodel(
  orgId: string,
  submodelId: string,
  payload: Partial<SubmodelCreate>,
): Promise<Submodel> {
  return request<Submodel>(`/organizations/${orgId}/submodels/${submodelId}`, {
    method: "PATCH",
    body: payload,
  });
}

export function retireSubmodel(
  orgId: string,
  submodelId: string,
): Promise<Submodel> {
  return request<Submodel>(`/organizations/${orgId}/submodels/${submodelId}`, {
    method: "DELETE",
  });
}

export function fetchSubmodelMaterials(
  orgId: string,
  submodelId: string,
): Promise<SubmodelMaterial[]> {
  return request<SubmodelMaterial[]>(
    `/organizations/${orgId}/submodels/${submodelId}/materials`,
  );
}

export function addSubmodelMaterialNote(
  orgId: string,
  submodelId: string,
  payload: { title: string; text: string },
): Promise<SubmodelMaterial> {
  return request<SubmodelMaterial>(
    `/organizations/${orgId}/submodels/${submodelId}/materials/notes`,
    { method: "POST", body: payload },
  );
}

export function uploadSubmodelMaterial(
  orgId: string,
  submodelId: string,
  file: File,
  title: string,
): Promise<SubmodelMaterial> {
  const form = new FormData();
  form.append("file", file);
  form.append("title", title);
  return request<SubmodelMaterial>(
    `/organizations/${orgId}/submodels/${submodelId}/materials/upload`,
    { method: "POST", body: form },
  );
}

export function readSubmodelMaterial(
  orgId: string,
  submodelId: string,
  materialId: string,
): Promise<SubmodelMaterial> {
  return request<SubmodelMaterial>(
    `/organizations/${orgId}/submodels/${submodelId}/materials/${materialId}/read`,
    { method: "POST" },
  );
}

export function archiveSubmodelMaterial(
  orgId: string,
  submodelId: string,
  materialId: string,
): Promise<SubmodelMaterial> {
  return request<SubmodelMaterial>(
    `/organizations/${orgId}/submodels/${submodelId}/materials/${materialId}`,
    { method: "DELETE" },
  );
}

export function trainSubmodel(
  orgId: string,
  submodelId: string,
): Promise<SubmodelTrainingResult> {
  return request<SubmodelTrainingResult>(
    `/organizations/${orgId}/submodels/${submodelId}/train`,
    { method: "POST" },
  );
}

export function fetchSubmodelWorkflows(
  orgId: string,
  submodelId: string,
): Promise<SubmodelWorkflow[]> {
  return request<SubmodelWorkflow[]>(
    `/organizations/${orgId}/submodels/${submodelId}/workflows`,
  );
}

export function updateSubmodelWorkflow(
  orgId: string,
  submodelId: string,
  workflowId: string,
  payload: { status: "draft" | "approved" | "rejected" },
): Promise<SubmodelWorkflow> {
  return request<SubmodelWorkflow>(
    `/organizations/${orgId}/submodels/${submodelId}/workflows/${workflowId}`,
    { method: "PATCH", body: payload },
  );
}

export function fetchSubmodelTests(
  orgId: string,
  submodelId: string,
): Promise<SubmodelTest[]> {
  return request<SubmodelTest[]>(
    `/organizations/${orgId}/submodels/${submodelId}/tests`,
  );
}

export function runSubmodelTest(
  orgId: string,
  submodelId: string,
  payload: { question: string; expected_points: string[] },
): Promise<SubmodelTest> {
  return request<SubmodelTest>(
    `/organizations/${orgId}/submodels/${submodelId}/tests`,
    { method: "POST", body: payload },
  );
}

export function fetchSubmodelPackage(
  orgId: string,
  submodelId: string,
): Promise<SubmodelPackage> {
  return request<SubmodelPackage>(
    `/organizations/${orgId}/submodels/${submodelId}/package`,
  );
}

/**
 * The signed package as a file.
 *
 * The download is a plain JSON document, so it is fetched through the normal
 * request path and turned into a blob the browser can save.
 */
export async function downloadSubmodelPackage(
  orgId: string,
  submodelId: string,
): Promise<{ filename: string; content: string }> {
  const response = await fetch(
    `${API_BASE_URL}/organizations/${orgId}/submodels/${submodelId}/download`,
    { credentials: "include", headers: { Accept: "application/json" } },
  );
  if (!response.ok) {
    let code = "request_failed";
    let message = `Request failed with status ${response.status}.`;
    const parsed = (await response.json().catch(() => null)) as
      | ErrorEnvelope
      | null;
    if (parsed?.error) {
      code = parsed.error.code ?? code;
      message = parsed.error.message ?? message;
    }
    throw new ApiRequestError(response.status, code, message);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return {
    filename: match?.[1] ?? `submodel-${submodelId}.wmsubmodel.json`,
    content: await response.text(),
  };
}

export function installSubmodel(
  orgId: string,
  file: File,
  label?: string,
): Promise<Submodel> {
  const form = new FormData();
  form.append("file", file);
  if (label) {
    form.append("label", label);
  }
  return request<Submodel>(`/organizations/${orgId}/submodels/import`, {
    method: "POST",
    body: form,
  });
}

export function askSubmodel(
  orgId: string,
  submodelId: string,
  payload: { question: string; context?: Record<string, unknown> },
): Promise<SubmodelGuidance> {
  return request<SubmodelGuidance>(
    `/organizations/${orgId}/submodels/${submodelId}/guidance`,
    { method: "POST", body: payload },
  );
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
