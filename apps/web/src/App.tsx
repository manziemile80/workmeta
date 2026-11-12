import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  CreditCard,
  Crown,
  Gauge,
  Loader2,
  LockKeyhole,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UserPlus,
  UsersRound,
} from "lucide-react";
import { BrandMark } from "./components/BrandMark";
import { ExtensionsPanel } from "./components/ExtensionsPanel";
import { SubmodelsPanel } from "./components/SubmodelsPanel";
import {
  ApiRequestError,
  cancelSubscription,
  checkout,
  confirmCheckout,
  createPlatformPlan,
  docsUrl,
  fetchBilling,
  fetchHealth,
  fetchInvoices,
  fetchMe,
  fetchPayments,
  fetchPlans,
  fetchPlatformOrganizations,
  fetchPlatformPayments,
  fetchPlatformPlans,
  fetchPlatformRevenue,
  fetchPlatformSubscriptions,
  fetchPlatformUsage,
  fetchReady,
  formatBytes,
  formatMoney,
  login,
  logout,
  refundPlatformPayment,
  registerAccount,
  renewSubscription,
  startTrial,
  switchOrganization,
  updatePlatformPlan,
  type LoginResponse,
  type Plan,
} from "./lib/api";
import { cn } from "./lib/cn";

type View =
  | "overview"
  | "extensions"
  | "submodels"
  | "pricing"
  | "checkout"
  | "payments"
  | "platform";

const LIMIT_ROWS: {
  key: string;
  label: string;
  format: (plan: Plan) => string;
}[] = [
  {
    key: "assistants",
    label: "Assistants",
    format: (plan) =>
      plan.max_assistants === null
        ? "Unlimited"
        : String(plan.max_assistants),
  },
  {
    key: "documents",
    label: "Documents",
    format: (plan) =>
      plan.max_documents === null ? "Unlimited" : String(plan.max_documents),
  },
  {
    key: "storage",
    label: "Storage",
    format: (plan) => formatBytes(plan.max_storage_bytes),
  },
  {
    key: "tokens",
    label: "Monthly AI tokens",
    format: (plan) =>
      plan.monthly_token_allowance === null
        ? "Unlimited"
        : plan.monthly_token_allowance.toLocaleString(),
  },
  {
    key: "requests",
    label: "Monthly AI requests",
    format: (plan) =>
      plan.monthly_request_allowance === null
        ? "Unlimited"
        : plan.monthly_request_allowance.toLocaleString(),
  },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong. Please try again.";
}

function QuotaBar({
  used,
  limit,
  label,
}: {
  used: number;
  limit: number | null;
  label: string;
}) {
  const pct =
    limit === null || limit === 0 ? 0 : Math.min(100, (used / limit) * 100);
  const over = limit !== null && used >= limit;
  return (
    <div className="rounded-xl bg-white p-4 ring-1 ring-mist-200 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink-900">{label}</p>
        <p className={cn("text-sm", over ? "font-semibold text-danger-500" : "text-ink-500")}>
          {used.toLocaleString()} /{" "}
          {limit === null ? "Unlimited" : limit.toLocaleString()}
        </p>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-mist-100">
        <div
          className={cn(
            "h-full rounded-full",
            over ? "bg-danger-500" : "bg-teal-500",
          )}
          style={{ width: `${Math.max(over ? 100 : pct, 2)}%` }}
        />
      </div>
    </div>
  );
}

export function App() {
  const [view, setView] = useState<View>("overview");
  const [interval, setInterval] = useState<"monthly" | "annual">("monthly");
  const [checkoutRef, setCheckoutRef] = useState<string | null>(null);

  // Checkout callback: provider redirects back with ?reference=...
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ref = params.get("reference");
    if (ref) {
      setCheckoutRef(ref);
      setView("checkout");
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const me = useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
    retry: false,
  });

  // Used only to decide whether the API documentation link is worth showing.
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    retry: false,
    staleTime: 60_000,
  });

  if (me.isPending) {
    return <Shell message="Loading your session…" />;
  }

  if (me.isError || !me.data) {
    return <AuthScreen />;
  }

  const session: LoginResponse = me.data;
  const orgId = session.active_organization?.id ?? null;
  const isPlatformAdmin = session.user.is_platform_admin;

  return (
    <div className="min-h-screen bg-mist-50">
      <header className="border-b border-mist-200 bg-white/80 sticky top-0 z-10 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-5 py-3">
          <div className="flex items-center gap-3">
            <BrandMark className="h-9 w-9" />
            <div>
              <p className="text-sm font-semibold tracking-tight text-ink-900">
                WorkMeta AI
              </p>
              <p className="text-[11px] uppercase tracking-wider text-teal-700">
                Billing &amp; subscriptions
              </p>
            </div>
          </div>

          <nav className="ml-2 flex flex-wrap items-center gap-1">
            {(
              [
                ["overview", "Overview"],
                ["extensions", "Extensions"],
                ["submodels", "Submodels"],
                ["pricing", "Pricing"],
                ["checkout", "Checkout"],
                ["payments", "Payments & usage"],
                isPlatformAdmin ? ["platform", "Platform admin"] : null,
              ].filter(Boolean) as [View, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setView(key)}
                className={cn(
                  "rounded-full px-3.5 py-1.5 text-sm font-medium transition",
                  view === key
                    ? "bg-navy-900 text-white"
                    : "text-ink-700 hover:bg-mist-100",
                )}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {session.memberships.length > 1 ? (
              <OrgSwitcher
                session={session}
              />
            ) : (
              <span className="text-sm text-ink-500">
                {session.active_organization?.name ?? "No organization"}
              </span>
            )}
            <span className="hidden sm:inline text-sm text-ink-500">
              {session.user.email}
            </span>
            <button
              type="button"
              onClick={async () => {
                try {
                  await logout();
                } finally {
                  window.location.reload();
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-full border border-mist-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:border-danger-500 hover:text-danger-500"
            >
              <LogOut className="h-4 w-4" aria-hidden="true" />
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-8">
        {!orgId ? (
          <EmptyOrg />
        ) : view === "overview" ? (
          <Overview orgId={orgId} />
        ) : view === "extensions" ? (
          <ExtensionsPanel orgId={orgId} />
        ) : view === "submodels" ? (
          <SubmodelsPanel orgId={orgId} />
        ) : view === "pricing" ? (
          <Pricing
            orgId={orgId}
            interval={interval}
            setInterval={setInterval}
            onSelect={() => setView("checkout")}
          />
        ) : view === "checkout" ? (
          <CheckoutPanel
            orgId={orgId}
            interval={interval}
            setInterval={setInterval}
            initialReference={checkoutRef}
            clearReference={() => setCheckoutRef(null)}
          />
        ) : view === "payments" ? (
          <PaymentsPanel orgId={orgId} />
        ) : (
          <PlatformPanel />
        )}

        <footer className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-mist-200 pt-5 text-xs text-ink-400">
          <p>© {new Date().getFullYear()} WorkMeta AI · Your Business. Your AI. Your Way.</p>
          {health.data?.docs_enabled ? (
            <a
              href={docsUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 font-semibold text-ink-700 hover:text-teal-700"
            >
              API docs <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
            </a>
          ) : (
            <span>API status: {health.data?.status ?? "checking"}</span>
          )}
        </footer>
      </main>
    </div>
  );
}

function Shell({ message }: { message: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-mist-50 text-sm text-ink-500">
      <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
      <span className="ml-2">{message}</span>
    </div>
  );
}

type AuthMode = "login" | "register";

/** Password policy enforced by the API for self-service registration. */
const REGISTER_PASSWORD_MIN_LENGTH = 10;

const COUNTRIES: { code: string; label: string }[] = [
  { code: "RW", label: "Rwanda" },
  { code: "BI", label: "Burundi" },
  { code: "CD", label: "DR Congo" },
  { code: "ET", label: "Ethiopia" },
  { code: "GH", label: "Ghana" },
  { code: "KE", label: "Kenya" },
  { code: "NG", label: "Nigeria" },
  { code: "TZ", label: "Tanzania" },
  { code: "UG", label: "Uganda" },
  { code: "ZA", label: "South Africa" },
];

function isRegisterPath(): boolean {
  return window.location.pathname.replace(/\/+$/, "") === "/register";
}

function AuthScreen() {
  const [mode, setMode] = useState<AuthMode>(() =>
    isRegisterPath() ? "register" : "login",
  );

  // The register page is linkable, so the browser history drives the mode.
  useEffect(() => {
    const onPopState = () => setMode(isRegisterPath() ? "register" : "login");
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const switchMode = (next: AuthMode) => {
    setMode(next);
    window.history.pushState({}, "", next === "register" ? "/register" : "/");
  };

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[420px_minmax(0,1fr)]">
      <AuthAside />
      <main className="flex items-center px-5 py-8 sm:px-8 lg:px-12">
        <div className="mx-auto w-full max-w-md">
          {mode === "login" ? (
            <LoginForm onRegister={() => switchMode("register")} />
          ) : (
            <RegisterForm onSignIn={() => switchMode("login")} />
          )}
        </div>
      </main>
    </div>
  );
}

function AuthAside() {
  return (
    <aside className="relative overflow-hidden bg-navy-950 px-7 py-10 text-white lg:px-11 lg:py-14">
      <div className="pointer-events-none absolute inset-0 bg-grid opacity-70" aria-hidden="true" />
      <div className="pointer-events-none absolute -right-28 top-16 h-72 w-72 rounded-full bg-teal-500/20 blur-3xl" aria-hidden="true" />
      <div className="relative flex h-full flex-col">
        <div className="flex items-center gap-3">
          <BrandMark />
          <div>
            <p className="text-lg font-semibold tracking-tight">WorkMeta AI</p>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-teal-300">
              Billing console
            </p>
          </div>
        </div>
        <h1 className="mt-12 max-w-sm text-balance text-4xl font-semibold leading-[1.08] tracking-tight">
          Your Business.
          <br />
          Your AI.
          <br />
          <span className="text-teal-400">Your Way.</span>
        </h1>
        <p className="mt-6 max-w-sm text-sm leading-relaxed text-mist-300/80">
          Manage your subscription, usage quotas and payment history.
          Plans are stored in the database and enforced on every AI request.
        </p>
        <p className="mt-auto hidden pt-10 text-xs text-mist-300/50 lg:block">
          <span className="inline-flex items-center gap-1.5">
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            Tenant-isolated billing · Card data never stored
          </span>
        </p>
      </div>
    </aside>
  );
}

function LoginForm({ onRegister }: { onRegister: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth });
  const ready = useQuery({ queryKey: ["ready"], queryFn: fetchReady });
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => login(email, password),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      window.location.reload();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    mutation.mutate();
  };

  return (
    <>
      <h2 className="text-2xl font-semibold tracking-tight text-ink-900">
        Sign in to continue
      </h2>
      <p className="mt-2 text-sm text-ink-500">
        API: {health.isSuccess ? health.data.service : "checking"} · DB:{" "}
        {ready.isSuccess
          ? ready.data.pgvector
            ? "ready"
            : "ready (pgvector missing)"
          : ready.isError
            ? "unavailable"
            : "checking"}
      </p>
      <form onSubmit={onSubmit} className="mt-6 space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-ink-700">Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            placeholder="you@company.rw"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink-700">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            placeholder="••••••••••"
          />
        </label>
        {error ? (
          <p className="flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={mutation.isPending}
          className="inline-flex w-full items-center justify-center gap-2 rounded-full bg-navy-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-navy-800 disabled:opacity-70"
        >
          {mutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <LockKeyhole className="h-4 w-4" aria-hidden="true" />
          )}
          Sign in
        </button>
      </form>
      <p className="mt-6 text-sm text-ink-500">
        No account yet?{" "}
        <button
          type="button"
          onClick={onRegister}
          className="font-semibold text-teal-700 hover:text-teal-600"
        >
          Create one
        </button>
      </p>
    </>
  );
}

function RegisterForm({ onSignIn }: { onSignIn: () => void }) {
  const [fullName, setFullName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [email, setEmail] = useState("");
  const [country, setCountry] = useState("RW");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      registerAccount({
        full_name: fullName.trim(),
        email: email.trim(),
        password,
        organization_name: organizationName.trim(),
        country,
      }),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      window.location.reload();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== confirmPassword) {
      setError("The two passwords do not match.");
      return;
    }
    if (password.length < REGISTER_PASSWORD_MIN_LENGTH) {
      setError(
        `The password must be at least ${REGISTER_PASSWORD_MIN_LENGTH} characters.`,
      );
      return;
    }
    mutation.mutate();
  };

  return (
    <>
      <h2 className="text-2xl font-semibold tracking-tight text-ink-900">
        Create your account
      </h2>
      <p className="mt-2 text-sm text-ink-500">
        You get an organization of your own on the Free plan — upgrade whenever
        you are ready.
      </p>
      <form onSubmit={onSubmit} className="mt-6 space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-ink-700">Full name</span>
          <input
            type="text"
            autoComplete="name"
            required
            minLength={2}
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            placeholder="Jane Doe"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink-700">Organization</span>
          <input
            type="text"
            autoComplete="organization"
            required
            minLength={2}
            value={organizationName}
            onChange={(e) => setOrganizationName(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            placeholder="Your company"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink-700">Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            placeholder="you@company.rw"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink-700">Country</span>
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
          >
            {COUNTRIES.map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink-700">Password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={REGISTER_PASSWORD_MIN_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            placeholder="••••••••••"
          />
          <span className="mt-1.5 block text-xs text-ink-400">
            At least {REGISTER_PASSWORD_MIN_LENGTH} characters, with a letter
            and a number.
          </span>
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink-700">
            Confirm password
          </span>
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={REGISTER_PASSWORD_MIN_LENGTH}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            placeholder="••••••••••"
          />
        </label>
        {error ? (
          <p className="flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={mutation.isPending}
          className="inline-flex w-full items-center justify-center gap-2 rounded-full bg-navy-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-navy-800 disabled:opacity-70"
        >
          {mutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <UserPlus className="h-4 w-4" aria-hidden="true" />
          )}
          Create account
        </button>
      </form>
      <p className="mt-6 text-sm text-ink-500">
        Already registered?{" "}
        <button
          type="button"
          onClick={onSignIn}
          className="font-semibold text-teal-700 hover:text-teal-600"
        >
          Sign in
        </button>
      </p>
    </>
  );
}

function OrgSwitcher({ session }: { session: LoginResponse }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  return (
    <label className="inline-flex items-center gap-2 text-sm text-ink-500">
      Org
      <select
        value={session.active_organization?.id ?? ""}
        disabled={busy}
        onChange={async (e) => {
          setBusy(true);
          try {
            await switchOrganization(e.target.value || null);
            await queryClient.invalidateQueries();
            window.location.reload();
          } finally {
            setBusy(false);
          }
        }}
        className="rounded-full border border-mist-300 bg-white px-3 py-1.5 text-sm text-ink-900"
      >
        {session.memberships.map((m) => (
          <option key={m.organization.id} value={m.organization.id}>
            {m.organization.name} ({m.role})
          </option>
        ))}
        {session.user.is_platform_admin ? <option value="">Platform</option> : null}
      </select>
    </label>
  );
}

function EmptyOrg() {
  return (
    <div className="rounded-2xl bg-white p-8 shadow-card ring-1 ring-mist-200">
      <UsersRound className="h-10 w-10 text-teal-600" aria-hidden="true" />
      <h2 className="mt-4 text-xl font-semibold text-ink-900">No organization selected</h2>
      <p className="mt-2 text-sm text-ink-500">
        Your account has no active organization. Ask the platform owner to invite you,
        or switch organization above.
      </p>
    </div>
  );
}

function Overview({ orgId }: { orgId: string }) {
  const billing = useQuery({
    queryKey: ["billing", orgId],
    queryFn: () => fetchBilling(orgId),
  });
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);

  const cancelMutation = useMutation({
    mutationFn: () => cancelSubscription(orgId),
    onSuccess: () => {
      setMessage("Cancellation scheduled for the end of the current period.");
      void queryClient.invalidateQueries({ queryKey: ["billing", orgId] });
    },
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  const renewMutation = useMutation({
    mutationFn: () => renewSubscription(orgId),
    onSuccess: (result) => {
      setMessage(
        result.immediate
          ? "Subscription renewed on the same plan."
          : "Renewal checkout created — complete payment on the checkout page.",
      );
      void queryClient.invalidateQueries({ queryKey: ["billing", orgId] });
    },
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  if (billing.isPending) return <Shell message="Loading billing…" />;
  if (billing.isError) {
    return (
      <ErrorPanel message={errorMessage(billing.error)} retry={() => void billing.refetch()} />
    );
  }

  const { subscription, usage } = billing.data;
  const plan = subscription.plan;

  return (
    <div className="space-y-6">
      <section className="rounded-2xl bg-navy-900 p-6 text-white shadow-lift">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-teal-300">
              <Crown className="h-4 w-4" aria-hidden="true" /> Current plan
            </p>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight">
              {plan.name}
              <span className="ml-3 align-middle text-sm font-medium text-mist-300">
                {subscription.status} · {subscription.billing_interval}
              </span>
            </h2>
            <p className="mt-2 text-sm text-mist-300/80">
              Period {new Date(subscription.current_period_start).toLocaleDateString()} –{" "}
              {new Date(subscription.current_period_end).toLocaleDateString()}
              {subscription.trial_ends_at
                ? ` · Trial ends ${new Date(subscription.trial_ends_at).toLocaleDateString()}`
                : ""}
              {subscription.cancel_at_period_end
                ? ` · Cancels ${new Date(subscription.current_period_end).toLocaleDateString()}`
                : ""}
            </p>
          </div>
          <div className="text-right">
            <p className="text-2xl font-semibold tracking-tight">
              {formatMoney(
                subscription.billing_interval === "annual"
                  ? plan.annual_price_minor
                  : plan.monthly_price_minor,
                plan.currency,
                plan.price_exponent,
              )}
            </p>
            <p className="text-xs text-mist-300/70">
              per {subscription.billing_interval === "annual" ? "year" : "month"}
            </p>
          </div>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => renewMutation.mutate()}
            disabled={renewMutation.isPending}
            className="inline-flex items-center gap-2 rounded-full bg-teal-400 px-4 py-2 text-sm font-semibold text-navy-950 transition hover:bg-teal-300 disabled:opacity-70"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" /> Renew
          </button>
          {plan.code !== "free" && !subscription.cancel_at_period_end ? (
            <button
              type="button"
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
              className="inline-flex items-center gap-2 rounded-full border border-white/20 px-4 py-2 text-sm font-semibold text-mist-100 transition hover:border-danger-500 hover:text-danger-500 disabled:opacity-70"
            >
              Cancel at period end
            </button>
          ) : null}
        </div>
        {message ? (
          <p className="mt-4 rounded-xl bg-white/10 px-4 py-3 text-sm text-mist-100">{message}</p>
        ) : null}
      </section>

      <section>
        <h3 className="inline-flex items-center gap-2 text-base font-semibold text-ink-900">
          <Gauge className="h-5 w-5 text-teal-600" aria-hidden="true" /> Usage &amp; quotas
        </h3>
        <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <QuotaBar used={usage.tokens_used} limit={usage.token_allowance} label="AI tokens" />
          <QuotaBar
            used={usage.requests_used}
            limit={usage.request_allowance}
            label="AI requests"
          />
          <QuotaBar
            used={usage.assistants_used}
            limit={usage.assistant_allowance}
            label="Assistants"
          />
          <QuotaBar
            used={usage.documents_used}
            limit={usage.document_allowance}
            label="Documents"
          />
          <QuotaBar
            used={usage.storage_bytes_used}
            limit={usage.storage_allowance}
            label="Storage (bytes)"
          />
        </div>
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h3 className="text-base font-semibold text-ink-900">Plan includes</h3>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {plan.features.map((feature) => (
            <li key={feature} className="flex items-center gap-2 text-sm text-ink-700">
              <CheckCircle2 className="h-4 w-4 text-teal-600" aria-hidden="true" />
              {String(feature).replaceAll("_", " ")}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Pricing({
  orgId,
  interval,
  setInterval: setBillingInterval,
  onSelect,
}: {
  orgId: string;
  interval: "monthly" | "annual";
  setInterval: (v: "monthly" | "annual") => void;
  onSelect: (plan: Plan) => void;
}) {
  const plans = useQuery({ queryKey: ["plans"], queryFn: fetchPlans });
  const billing = useQuery({
    queryKey: ["billing", orgId],
    queryFn: () => fetchBilling(orgId),
  });

  if (plans.isPending) return <Shell message="Loading plans…" />;
  if (plans.isError) return <ErrorPanel message={errorMessage(plans.error)} retry={() => void plans.refetch()} />;

  const currentCode = billing.data?.subscription.plan.code;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-ink-900">Choose your plan</h2>
          <p className="mt-1 text-sm text-ink-500">
            Subscription limits are read from the database on every AI request.
          </p>
        </div>
        <div className="flex rounded-full border border-mist-300 bg-white p-1">
          {(["monthly", "annual"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setBillingInterval(value)}
              className={cn(
                "rounded-full px-4 py-1.5 text-sm font-medium",
                interval === value ? "bg-navy-900 text-white" : "text-ink-700 hover:bg-mist-100",
              )}
            >
              {value === "monthly" ? "Monthly" : "Annual"}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {plans.data.map((plan) => {
          const price =
            interval === "annual" ? plan.annual_price_minor : plan.monthly_price_minor;
          const isCurrent = plan.code === currentCode;
          return (
            <article
              key={plan.id}
              className={cn(
                "flex flex-col rounded-2xl bg-white p-5 shadow-card ring-1",
                isCurrent ? "ring-teal-500" : "ring-mist-200",
              )}
            >
              <h3 className="text-base font-semibold text-ink-900">{plan.name}</h3>
              <p className="mt-1 min-h-10 text-sm text-ink-500">{plan.description}</p>
              <p className="mt-3 text-2xl font-semibold tracking-tight text-ink-900">
                {formatMoney(price, plan.currency, plan.price_exponent)}
                <span className="text-sm font-normal text-ink-500">
                  /{interval === "annual" ? "year" : "month"}
                </span>
              </p>
              <ul className="mt-4 flex-1 space-y-2">
                {LIMIT_ROWS.map((row) => (
                  <li key={row.key} className="flex justify-between gap-2 text-sm">
                    <span className="text-ink-500">{row.label}</span>
                    <span className="font-medium text-ink-900">{row.format(plan)}</span>
                  </li>
                ))}
              </ul>
              {plan.features.length ? (
                <p className="mt-3 text-xs text-ink-500">
                  Includes {plan.features.map((f) => String(f).replaceAll("_", " ")).join(", ")}
                </p>
              ) : null}
              <button
                type="button"
                disabled={isCurrent}
                onClick={() => onSelect(plan)}
                className={cn(
                  "mt-5 w-full rounded-full py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed",
                  isCurrent
                    ? "border border-teal-500 text-teal-700"
                    : "bg-navy-900 text-white hover:bg-navy-800",
                )}
              >
                {isCurrent ? "Current plan" : plan.code === "free" ? "Downgrade to Free" : "Select plan"}
              </button>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function CheckoutPanel({
  orgId,
  interval,
  setInterval: setBillingInterval,
  initialReference,
  clearReference,
}: {
  orgId: string;
  interval: "monthly" | "annual";
  setInterval: (v: "monthly" | "annual") => void;
  initialReference: string | null;
  clearReference: () => void;
}) {
  const queryClient = useQueryClient();
  const plans = useQuery({ queryKey: ["plans"], queryFn: fetchPlans });
  const [planCode, setPlanCode] = useState("starter");
  const [referenceInput, setReferenceInput] = useState(initialReference ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);

  useEffect(() => {
    if (initialReference) setReferenceInput(initialReference);
  }, [initialReference]);

  const checkoutMutation = useMutation({
    mutationFn: (code: string) => checkout(orgId, code, interval),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["billing", orgId] });
      if (result.immediate) {
        setMessage(`Switched to ${result.plan.name} immediately — no payment needed.`);
        setCheckoutUrl(null);
      } else {
        setMessage(
          "Checkout session created. You will be redirected to the provider, then return here to confirm.",
        );
        if (result.payment) setReferenceInput(result.payment.provider_reference);
        setCheckoutUrl(result.checkout_url);
      }
      clearReference();
    },
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  const trialMutation = useMutation({
    mutationFn: (code: string) => startTrial(orgId, code, interval),
    onSuccess: (result) => {
      setMessage(
        `Trial started on ${result.plan.name} until ${new Date(result.subscription.trial_ends_at ?? "").toLocaleDateString()}.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["billing", orgId] });
    },
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  const confirmMutation = useMutation({
    mutationFn: (ref: string) => confirmCheckout(orgId, ref),
    onSuccess: (result) => {
      setMessage(
        result.activated
          ? `Payment confirmed — your plan is now ${result.subscription.plan.name}.`
          : "Payment was not successful. No plan change was applied.",
      );
      void queryClient.invalidateQueries({ queryKey: ["billing", orgId] });
    },
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  if (plans.isPending) return <Shell message="Loading plans…" />;
  if (plans.isError) return <ErrorPanel message={errorMessage(plans.error)} retry={() => void plans.refetch()} />;

  const selected = plans.data.find((p) => p.code === planCode) ?? plans.data[0];
  const price =
    selected != null
      ? interval === "annual"
        ? selected.annual_price_minor
        : selected.monthly_price_minor
      : 0;

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h2 className="inline-flex items-center gap-2 text-xl font-semibold text-ink-900">
          <CreditCard className="h-5 w-5 text-teal-600" aria-hidden="true" /> Checkout
        </h2>
        <p className="mt-2 text-sm text-ink-500">
          Payment status is always verified with the provider — returning from the provider
          page never activates a plan on its own.
        </p>

        <div className="mt-5 space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-ink-700">Plan</span>
            <select
              value={planCode}
              onChange={(e) => setPlanCode(e.target.value)}
              className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900"
            >
              {plans.data.map((plan) => (
                <option key={plan.id} value={plan.code}>
                  {plan.name} — {formatMoney(plan.monthly_price_minor, plan.currency, plan.price_exponent)}/mo
                </option>
              ))}
            </select>
          </label>

          <div className="flex rounded-full border border-mist-300 bg-mist-50 p-1">
            {(["monthly", "annual"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setBillingInterval(value)}
                className={cn(
                  "flex-1 rounded-full px-4 py-1.5 text-sm font-medium",
                  interval === value ? "bg-navy-900 text-white" : "text-ink-700",
                )}
              >
                {value}
              </button>
            ))}
          </div>

          <div className="rounded-xl bg-mist-50 p-4">
            <div className="flex justify-between text-sm">
              <span className="text-ink-500">Total due today</span>
              <span className="font-semibold text-ink-900">
                {selected
                  ? formatMoney(price, selected.currency, selected.price_exponent)
                  : "—"}
              </span>
            </div>
            <p className="mt-1 text-xs text-ink-400">
              Card data never touches our server — checkout happens with the provider.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={checkoutMutation.isPending}
              onClick={() => checkoutMutation.mutate(planCode)}
              className="rounded-full bg-teal-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:opacity-70"
            >
              {checkoutMutation.isPending ? "Creating checkout…" : "Start checkout"}
            </button>
            {selected != null && selected.code !== "free" ? (
              <button
                type="button"
                disabled={trialMutation.isPending}
                onClick={() => trialMutation.mutate(planCode)}
                className="inline-flex items-center gap-2 rounded-full border border-mist-300 px-5 py-2.5 text-sm font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700 disabled:opacity-70"
              >
                <Sparkles className="h-4 w-4" aria-hidden="true" /> Start trial
              </button>
            ) : null}
          </div>

          {checkoutUrl ? (
            <a
              href={checkoutUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-full bg-navy-900 px-5 py-2.5 text-sm font-semibold text-white hover:bg-navy-800"
            >
              Continue to provider <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
            </a>
          ) : null}

          <div className="border-t border-mist-200 pt-4">
            <label className="block">
              <span className="text-sm font-medium text-ink-700">Payment reference</span>
              <input
                value={referenceInput}
                onChange={(e) => setReferenceInput(e.target.value)}
                placeholder="WM-…"
                className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm font-mono text-ink-900"
              />
            </label>
            <button
              type="button"
              disabled={!referenceInput || confirmMutation.isPending}
              onClick={() => confirmMutation.mutate(referenceInput.trim())}
              className="mt-2 rounded-full bg-navy-900 px-5 py-2.5 text-sm font-semibold text-white hover:bg-navy-800 disabled:opacity-70"
            >
              {confirmMutation.isPending ? "Verifying…" : "Verify & activate"}
            </button>
          </div>

          {message ? (
            <p className="rounded-xl bg-mist-100 px-4 py-3 text-sm text-ink-700">{message}</p>
          ) : null}
        </div>
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h3 className="text-base font-semibold text-ink-900">How recurring billing works</h3>
        <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-relaxed text-ink-700">
          <li>A pending payment is created for the selected plan and interval.</li>
          <li>You complete the provider checkout in a new tab.</li>
          <li>This page asks the provider to verify the reference and activates the plan only on success.</li>
          <li>Webhooks settle payments asynchronously and are idempotent — duplicates are ignored.</li>
          <li>Cancelling keeps the plan until the period ends, then downgrades to Free.</li>
          <li>Missed renewals get a grace period, then the subscription expires and AI mutations are blocked.</li>
        </ol>
        <p className="mt-4 flex items-start gap-2 text-sm text-ink-500">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-teal-600" aria-hidden="true" />
          Payments and usage are append-only. Refunds preserve the original payment history.
        </p>
      </section>
    </div>
  );
}

function PaymentsPanel({ orgId }: { orgId: string }) {
  const payments = useQuery({
    queryKey: ["payments", orgId],
    queryFn: () => fetchPayments(orgId),
  });
  const invoices = useQuery({
    queryKey: ["invoices", orgId],
    queryFn: () => fetchInvoices(orgId),
  });

  return (
    <div className="space-y-6">
      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h2 className="text-lg font-semibold text-ink-900">Payment history</h2>
        {payments.isPending ? (
          <p className="mt-3 text-sm text-ink-500">Loading…</p>
        ) : payments.isError ? (
          <ErrorPanel message={errorMessage(payments.error)} retry={() => void payments.refetch()} />
        ) : payments.data.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">No payments yet. Start a checkout to subscribe.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-mist-200 text-xs uppercase tracking-wider text-ink-400">
                  <th className="py-2 pr-4">Date</th>
                  <th className="py-2 pr-4">Reference</th>
                  <th className="py-2 pr-4">Amount</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2">Type</th>
                </tr>
              </thead>
              <tbody>
                {payments.data.map((row) => (
                  <tr key={row.id} className="border-b border-mist-100 text-ink-700">
                    <td className="py-2 pr-4">{new Date(row.created_at).toLocaleString()}</td>
                    <td className="py-2 pr-4 font-mono text-xs">{row.provider_reference}</td>
                    <td className="py-2 pr-4">{formatMoney(row.amount_minor, row.currency)}</td>
                    <td className="py-2 pr-4">{row.status}</td>
                    <td className="py-2">{row.kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h2 className="text-lg font-semibold text-ink-900">Invoices</h2>
        {invoices.isPending ? (
          <p className="mt-3 text-sm text-ink-500">Loading…</p>
        ) : invoices.isError ? (
          <ErrorPanel message={errorMessage(invoices.error)} retry={() => void invoices.refetch()} />
        ) : invoices.data.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">No succeeded payments yet.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-mist-200 text-xs uppercase tracking-wider text-ink-400">
                  <th className="py-2 pr-4">Invoice</th>
                  <th className="py-2 pr-4">Date</th>
                  <th className="py-2 pr-4">Amount</th>
                  <th className="py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {invoices.data.map((row) => (
                  <tr key={row.id} className="border-b border-mist-100 text-ink-700">
                    <td className="py-2 pr-4 font-mono text-xs">{row.invoice_number}</td>
                    <td className="py-2 pr-4">{new Date(row.created_at).toLocaleDateString()}</td>
                    <td className="py-2 pr-4">{formatMoney(row.amount_minor, row.currency)}</td>
                    <td className="py-2">{row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function PlatformPanel() {
  const queryClient = useQueryClient();
  const orgs = useQuery({
    queryKey: ["platform-orgs"],
    queryFn: fetchPlatformOrganizations,
  });
  const plans = useQuery({ queryKey: ["platform-plans"], queryFn: fetchPlatformPlans });
  const subs = useQuery({
    queryKey: ["platform-subs"],
    queryFn: fetchPlatformSubscriptions,
  });
  const payments = useQuery({
    queryKey: ["platform-payments"],
    queryFn: fetchPlatformPayments,
  });
  const revenue = useQuery({
    queryKey: ["platform-revenue"],
    queryFn: fetchPlatformRevenue,
  });
  const usage = useQuery({
    queryKey: ["platform-usage"],
    queryFn: fetchPlatformUsage,
  });
  const [message, setMessage] = useState<string | null>(null);
  const [newPlan, setNewPlan] = useState({ code: "", name: "" });

  const createMutation = useMutation({
    mutationFn: () =>
      createPlatformPlan({
        code: newPlan.code.trim(),
        name: newPlan.name.trim(),
        monthly_price_minor: 0,
        annual_price_minor: 0,
        features: [],
        is_active: true,
      }),
    onSuccess: () => {
      setMessage("Plan created.");
      setNewPlan({ code: "", name: "" });
      void queryClient.invalidateQueries({ queryKey: ["platform-plans"] });
    },
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      updatePlatformPlan(id, { is_active: active }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["platform-plans"] }),
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  const refundMutation = useMutation({
    mutationFn: (paymentId: string) => refundPlatformPayment(paymentId),
    onSuccess: () => {
      setMessage("Refund issued.");
      void queryClient.invalidateQueries({ queryKey: ["platform-payments"] });
      void queryClient.invalidateQueries({ queryKey: ["platform-revenue"] });
    },
    onError: (e: unknown) => setMessage(errorMessage(e)),
  });

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight text-ink-900">Platform administration</h2>
        <p className="mt-1 flex items-center gap-2 text-sm text-ink-500">
          <ShieldCheck className="h-4 w-4 text-teal-600" aria-hidden="true" />
          Billing metadata and aggregates only — no tenant documents or conversations.
        </p>
        {message ? (
          <p className="mt-3 rounded-xl bg-mist-100 px-4 py-3 text-sm text-ink-700">{message}</p>
        ) : null}
      </header>

      <section className="grid gap-4 sm:grid-cols-3">
        <Stat
          label="Organizations"
          value={orgs.data?.length ?? 0}
          loading={orgs.isPending}
        />
        <Stat
          label="Active subscriptions"
          value={subs.data?.filter((s) => s.status === "active").length ?? 0}
          loading={subs.isPending}
        />
        <Stat
          label="Net revenue"
          value={
            revenue.data
              ? formatMoney(revenue.data.net_minor, revenue.data.currency)
              : "—"
          }
          loading={revenue.isPending}
        />
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h3 className="text-base font-semibold text-ink-900">Revenue</h3>
        {revenue.data ? (
          <div className="mt-3 text-sm text-ink-700">
            <p>
              Gross {formatMoney(revenue.data.gross_minor, revenue.data.currency)} · Refunds{" "}
              {formatMoney(revenue.data.refunds_minor, revenue.data.currency)} · Payments{" "}
              {revenue.data.payments_count} · Refunds {revenue.data.refunds_count}
            </p>
            <ul className="mt-2 space-y-1">
              {revenue.data.by_plan.map((row) => (
                <li key={row.plan_code} className="flex justify-between">
                  <span>{row.plan_name}</span>
                  <span>
                    {formatMoney(row.gross_minor, revenue.data.currency)} ({row.payments_count})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="mt-3 text-sm text-ink-500">Loading revenue…</p>
        )}
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h3 className="text-base font-semibold text-ink-900">Organizations</h3>
        {orgs.data ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-mist-200 text-xs uppercase tracking-wider text-ink-400">
                  <th className="py-2 pr-4">Org</th>
                  <th className="py-2 pr-4">Plan</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2">Period end</th>
                </tr>
              </thead>
              <tbody>
                {orgs.data.map((row) => (
                  <tr key={row.id} className="border-b border-mist-100 text-ink-700">
                    <td className="py-2 pr-4">{row.name} <span className="text-ink-400">({row.slug})</span></td>
                    <td className="py-2 pr-4">{row.plan_name ?? "—"}</td>
                    <td className="py-2 pr-4">{row.subscription_status ?? "—"}</td>
                    <td className="py-2">
                      {row.current_period_end
                        ? new Date(row.current_period_end).toLocaleDateString()
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-3 text-sm text-ink-500">Loading organizations…</p>
        )}
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h3 className="text-base font-semibold text-ink-900">Plans</h3>
        {plans.data ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-mist-200 text-xs uppercase tracking-wider text-ink-400">
                  <th className="py-2 pr-4">Code</th>
                  <th className="py-2 pr-4">Name</th>
                  <th className="py-2 pr-4">Monthly</th>
                  <th className="py-2 pr-4">Active</th>
                  <th className="py-2">Action</th>
                </tr>
              </thead>
              <tbody>
                {plans.data.map((plan) => (
                  <tr key={plan.id} className="border-b border-mist-100 text-ink-700">
                    <td className="py-2 pr-4 font-mono text-xs">{plan.code}</td>
                    <td className="py-2 pr-4">{plan.name}</td>
                    <td className="py-2 pr-4">
                      {formatMoney(plan.monthly_price_minor, plan.currency, plan.price_exponent)}
                    </td>
                    <td className="py-2 pr-4">{plan.is_active ? "Yes" : "No"}</td>
                    <td className="py-2">
                      <button
                        type="button"
                        disabled={toggleMutation.isPending}
                        onClick={() => toggleMutation.mutate({ id: plan.id, active: !plan.is_active })}
                        className="rounded-full border border-mist-300 px-3 py-1 text-xs font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700"
                      >
                        {plan.is_active ? "Deactivate" : "Activate"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-3 text-sm text-ink-500">Loading plans…</p>
        )}
        <form
          className="mt-4 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
        >
          <label className="text-sm font-medium text-ink-700">
            Code
            <input
              value={newPlan.code}
              onChange={(e) => setNewPlan({ ...newPlan, code: e.target.value })}
              required
              className="mt-1 block rounded-xl border border-mist-300 bg-white px-3 py-2 text-sm"
              placeholder="growth"
            />
          </label>
          <label className="text-sm font-medium text-ink-700">
            Name
            <input
              value={newPlan.name}
              onChange={(e) => setNewPlan({ ...newPlan, name: e.target.value })}
              required
              className="mt-1 block rounded-xl border border-mist-300 bg-white px-3 py-2 text-sm"
              placeholder="Growth"
            />
          </label>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="rounded-full bg-navy-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-70"
          >
            Create plan
          </button>
        </form>
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h3 className="text-base font-semibold text-ink-900">Recent payments</h3>
        {payments.data ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-mist-200 text-xs uppercase tracking-wider text-ink-400">
                  <th className="py-2 pr-4">Reference</th>
                  <th className="py-2 pr-4">Amount</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2">Refund</th>
                </tr>
              </thead>
              <tbody>
                {payments.data.map((row) => (
                  <tr key={row.id} className="border-b border-mist-100 text-ink-700">
                    <td className="py-2 pr-4 font-mono text-xs">{row.provider_reference}</td>
                    <td className="py-2 pr-4">
                      {formatMoney(row.amount_minor, row.currency)}
                    </td>
                    <td className="py-2 pr-4">{row.status}</td>
                    <td className="py-2">
                      {row.kind === "payment" && row.status === "succeeded" ? (
                        <button
                          type="button"
                          disabled={refundMutation.isPending}
                          onClick={() => refundMutation.mutate(row.id)}
                          className="rounded-full border border-mist-300 px-3 py-1 text-xs font-semibold text-ink-700 hover:border-danger-500 hover:text-danger-500"
                        >
                          Refund
                        </button>
                      ) : (
                        <span className="text-ink-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-3 text-sm text-ink-500">Loading payments…</p>
        )}
      </section>

      <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
        <h3 className="text-base font-semibold text-ink-900">Usage (aggregates only)</h3>
        {usage.data ? (
          <div className="mt-3 text-sm text-ink-700">
            <p>
              {usage.data.total_requests.toLocaleString()} requests ·{" "}
              {usage.data.total_tokens.toLocaleString()} tokens in period
            </p>
            <ul className="mt-2 space-y-1">
              {usage.data.organizations.map((row) => (
                <li key={row.org_id} className="flex justify-between gap-2">
                  <span>{row.organization_name}</span>
                  <span>
                    {row.requests} requests · {(row.tokens_in + row.tokens_out).toLocaleString()} tokens
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="mt-3 text-sm text-ink-500">Loading usage…</p>
        )}
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  loading,
}: {
  label: string;
  value: string | number;
  loading: boolean;
}) {
  return (
    <div className="rounded-2xl bg-white p-5 shadow-card ring-1 ring-mist-200">
      <p className="text-xs font-semibold uppercase tracking-wider text-ink-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-ink-900">
        {loading ? "…" : String(value)}
      </p>
    </div>
  );
}

function ErrorPanel({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-danger-500/10 p-5 text-sm text-danger-500">
      <span className="inline-flex items-center gap-2">
        <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {message}
      </span>
      <button
        type="button"
        onClick={retry}
        className="rounded-full bg-white px-4 py-2 font-semibold text-ink-900 ring-1 ring-mist-200"
      >
        Retry
      </button>
    </div>
  );
}
