/** Training console for the client's own extensions.
 *
 * Every extension belongs to one client and one target system at one version.
 * The workflow is: pick the system, add the training material, let the model
 * extract the procedures, approve them, test the assistant and publish.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  Mic,
  MonitorPlay,
  Package,
  Plus,
  RotateCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  addMaterialNote,
  analyzeExtension,
  archiveMaterial,
  createExtension,
  extractMaterial,
  fetchAssistants,
  fetchExtensions,
  fetchMaterials,
  fetchPackage,
  fetchProcedures,
  fetchTests,
  publishExtension,
  retireExtension,
  runExtensionTest,
  updateProcedure,
  uploadMaterial,
  type Extension,
  type ExtensionProcedure,
  type ExtensionTest,
  type TrainingMaterial,
} from "../lib/api";
import { cn } from "../lib/cn";

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-mist-100 text-ink-600 ring-mist-200",
  training: "bg-teal-500/10 text-teal-700 ring-teal-500/30",
  ready: "bg-teal-500/10 text-teal-700 ring-teal-500/30",
  published: "bg-navy-900 text-white ring-navy-900",
  retired: "bg-mist-100 text-ink-500 ring-mist-200",
};

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Something went wrong. Please try again.";
}

function Pill({ value }: { value: string }) {
  return (
    <span
      className={cn(
        "rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ring-1",
        STATUS_STYLES[value] ?? "bg-mist-100 text-ink-600 ring-mist-200",
      )}
    >
      {value.replace("_", " ")}
    </span>
  );
}

export function ExtensionsPanel({ orgId }: { orgId: string }) {
  const [selected, setSelected] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const extensions = useQuery({
    queryKey: ["extensions", orgId],
    queryFn: () => fetchExtensions(orgId),
  });

  const current = extensions.data?.find((item) => item.id === selected) ?? null;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-ink-900">
            Trained extensions
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">
            Train one extension per system and version. The materials, the
            extracted procedures and the permissions stay in this organization
            and are never shared with another client.
          </p>
        </div>
      </header>

      <CreateExtension
        orgId={orgId}
        onCreated={(extension) => {
          void queryClient.invalidateQueries({ queryKey: ["extensions", orgId] });
          setSelected(extension.id);
        }}
      />

      {extensions.isPending ? (
        <p className="flex items-center gap-2 text-sm text-ink-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading extensions…
        </p>
      ) : extensions.isError ? (
        <p className="rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
          {errorMessage(extensions.error)}
        </p>
      ) : extensions.data.length === 0 ? (
        <p className="rounded-2xl bg-white p-6 text-sm text-ink-500 shadow-card ring-1 ring-mist-200">
          No extension yet. Create one for the system your staff work in.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {extensions.data.map((extension) => (
            <button
              key={extension.id}
              type="button"
              onClick={() => setSelected(extension.id)}
              className={cn(
                "rounded-2xl bg-white p-5 text-left shadow-card ring-1 transition",
                extension.id === selected
                  ? "ring-teal-500"
                  : "ring-mist-200 hover:ring-teal-500/50",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-base font-semibold text-ink-900">
                    {extension.name}
                  </p>
                  <p className="mt-0.5 text-sm text-ink-500">
                    {extension.system_name} · version {extension.system_version}
                  </p>
                </div>
                <Pill value={extension.status} />
              </div>
              <dl className="mt-4 grid grid-cols-3 gap-3 text-xs text-ink-500">
                <div>
                  <dt className="font-medium text-ink-700">Materials</dt>
                  <dd>{extension.materials_count}</dd>
                </div>
                <div>
                  <dt className="font-medium text-ink-700">Procedures</dt>
                  <dd>
                    {extension.approved_procedures_count}/{extension.procedures_count}
                  </dd>
                </div>
                <div>
                  <dt className="font-medium text-ink-700">Tests passed</dt>
                  <dd>
                    {extension.passed_tests_count}/{extension.tests_count}
                  </dd>
                </div>
              </dl>
              {extension.package_signature ? (
                <p className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-teal-700">
                  <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                  Signed package
                </p>
              ) : null}
            </button>
          ))}
        </div>
      )}

      {current ? (
        <ExtensionDetail
          key={current.id}
          orgId={orgId}
          extension={current}
          onChanged={() =>
            void queryClient.invalidateQueries({ queryKey: ["extensions", orgId] })
          }
        />
      ) : null}
    </div>
  );
}

function CreateExtension({
  orgId,
  onCreated,
}: {
  orgId: string;
  onCreated: (extension: Extension) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [systemCode, setSystemCode] = useState("");
  const [systemName, setSystemName] = useState("");
  const [systemVersion, setSystemVersion] = useState("");
  const [patterns, setPatterns] = useState("");
  const [error, setError] = useState<string | null>(null);

  const assistants = useQuery({
    queryKey: ["assistants", orgId],
    queryFn: () => fetchAssistants(orgId),
  });

  const mutation = useMutation({
    mutationFn: (assistantId: string) =>
      createExtension(orgId, {
        name: name.trim(),
        assistant_id: assistantId,
        system_code: systemCode.trim().toLowerCase(),
        system_name: systemName.trim(),
        system_version: systemVersion.trim(),
        target_kind: "web",
        url_patterns: patterns
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
      }),
    onSuccess: (extension) => {
      setError(null);
      setOpen(false);
      setName("");
      setSystemCode("");
      setSystemName("");
      setSystemVersion("");
      setPatterns("");
      onCreated(extension);
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const assistants_ = assistants.data ?? [];

  return (
    <section className="rounded-2xl bg-white p-5 shadow-card ring-1 ring-mist-200">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            Train a new extension
          </h3>
          <p className="mt-0.5 text-sm text-ink-500">
            Pick the system, the version and the pages it runs on.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="inline-flex items-center gap-2 rounded-full bg-navy-900 px-4 py-2 text-sm font-semibold text-white hover:bg-navy-800"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          {open ? "Close" : "New extension"}
        </button>
      </div>

      {open ? (
        <form
          className="mt-4 grid gap-4 sm:grid-cols-2"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            setError(null);
            const assistantId = assistants_[0]?.id;
            if (!assistantId) {
              setError("Create an assistant first: it holds the documents.");
              return;
            }
            mutation.mutate(assistantId);
          }}
        >
          <label className="block">
            <span className="text-sm font-medium text-ink-700">Extension name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              minLength={2}
              placeholder="EBM 3.2 — billing desk"
              className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-ink-700">System code</span>
            <input
              value={systemCode}
              onChange={(event) => setSystemCode(event.target.value)}
              required
              minLength={2}
              placeholder="ebm"
              className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-ink-700">System name</span>
            <input
              value={systemName}
              onChange={(event) => setSystemName(event.target.value)}
              required
              minLength={2}
              placeholder="EBM"
              className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-ink-700">Version</span>
            <input
              value={systemVersion}
              onChange={(event) => setSystemVersion(event.target.value)}
              required
              placeholder="3.2"
              className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            />
          </label>
          <label className="block sm:col-span-2">
            <span className="text-sm font-medium text-ink-700">
              Pages this extension applies to (one per line)
            </span>
            <textarea
              value={patterns}
              onChange={(event) => setPatterns(event.target.value)}
              rows={3}
              placeholder={"*://ebm.local/v3/*"}
              className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            />
          </label>
          {error ? (
            <p className="flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500 sm:col-span-2">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
            </p>
          ) : null}
          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={mutation.isPending}
              className="inline-flex items-center gap-2 rounded-full bg-teal-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-teal-700 disabled:opacity-70"
            >
              {mutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Sparkles className="h-4 w-4" aria-hidden="true" />
              )}
              Create extension
            </button>
            {assistants_.length === 0 ? (
              <p className="mt-2 text-xs text-ink-400">
                No assistant yet — the first assistant is created for you and
                holds the manuals and screenshots.
              </p>
            ) : null}
          </div>
        </form>
      ) : null}
    </section>
  );
}

function ExtensionDetail({
  orgId,
  extension,
  onChanged,
}: {
  orgId: string;
  extension: Extension;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pkg = useQuery({
    queryKey: ["extension-package", extension.id],
    queryFn: () => fetchPackage(orgId, extension.id),
    enabled: extension.status === "published",
    retry: false,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["extension-materials", extension.id] });
    void queryClient.invalidateQueries({ queryKey: ["extension-procedures", extension.id] });
    void queryClient.invalidateQueries({ queryKey: ["extension-tests", extension.id] });
    onChanged();
  };

  const analyze = useMutation({
    mutationFn: () => analyzeExtension(orgId, extension.id),
    onSuccess: (result) => {
      setError(null);
      setMessage(result.message);
      refresh();
    },
    onError: (e: unknown) => {
      setError(errorMessage(e));
      setMessage(null);
    },
  });

  const publish = useMutation({
    mutationFn: () => publishExtension(orgId, extension.id),
    onSuccess: (result) => {
      setError(null);
      setMessage(
        result.signature
          ? "Published. The signed package is ready for installation."
          : "Published. No signing key is configured, so the package is unsigned.",
      );
      refresh();
    },
    onError: (e: unknown) => {
      setError(errorMessage(e));
      setMessage(null);
    },
  });

  const retire = useMutation({
    mutationFn: () => retireExtension(orgId, extension.id),
    onSuccess: () => {
      setMessage("Extension retired.");
      refresh();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const canPublish =
    extension.training_status === "extracted" &&
    extension.approved_procedures_count > 0 &&
    extension.passed_tests_count > 0;

  return (
    <section className="space-y-6 rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-ink-900">
            {extension.name}
          </h3>
          <p className="mt-1 text-sm text-ink-500">
            {extension.system_name} {extension.system_version} ·{" "}
            {extension.url_patterns.length} page pattern
            {extension.url_patterns.length === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Pill value={extension.status} />
          <button
            type="button"
            onClick={() => analyze.mutate()}
            disabled={analyze.isPending || extension.status === "published"}
            className="inline-flex items-center gap-2 rounded-full border border-mist-300 px-4 py-2 text-sm font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700 disabled:opacity-60"
          >
            {analyze.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            )}
            Analyse material
          </button>
          <button
            type="button"
            onClick={() => publish.mutate()}
            disabled={publish.isPending || !canPublish}
            title={
              canPublish
                ? undefined
                : "Train the extension, approve a procedure and pass a test first"
            }
            className="inline-flex items-center gap-2 rounded-full bg-navy-900 px-4 py-2 text-sm font-semibold text-white hover:bg-navy-800 disabled:opacity-50"
          >
            {publish.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Package className="h-4 w-4" aria-hidden="true" />
            )}
            Publish
          </button>
          {extension.status === "published" ? (
            <button
              type="button"
              onClick={() => retire.mutate()}
              disabled={retire.isPending}
              className="inline-flex items-center gap-2 rounded-full border border-mist-300 px-4 py-2 text-sm font-semibold text-ink-700 hover:border-danger-500 hover:text-danger-500 disabled:opacity-60"
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Retire
            </button>
          ) : null}
        </div>
      </header>

      {message ? (
        <p className="rounded-xl bg-teal-500/10 px-4 py-3 text-sm text-teal-700">
          {message}
        </p>
      ) : null}
      {error ? (
        <p className="flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
        </p>
      ) : null}

      <Materials orgId={orgId} extension={extension} onChanged={refresh} />
      <Procedures orgId={orgId} extension={extension} onChanged={refresh} />
      <Tests orgId={orgId} extension={extension} onChanged={refresh} />

      {extension.status === "published" && pkg.data ? (
        <div className="rounded-xl bg-mist-50 p-4">
          <p className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
            <ShieldCheck className="h-4 w-4 text-teal-600" aria-hidden="true" />
            Package v{pkg.data.version}
            {pkg.data.signature ? " · signed" : " · unsigned"}
          </p>
          <p className="mt-1 text-xs text-ink-500">
            {pkg.data.signing_key_id ?? "no signing key"} ·{" "}
            {pkg.data.published_at
              ? new Date(pkg.data.published_at).toLocaleString()
              : ""}
          </p>
        </div>
      ) : null}
    </section>
  );
}

function Materials({
  orgId,
  extension,
  onChanged,
}: {
  orgId: string;
  extension: Extension;
  onChanged: () => void;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const materials = useQuery({
    queryKey: ["extension-materials", extension.id],
    queryFn: () => fetchMaterials(orgId, extension.id),
  });

  const addNote = useMutation({
    mutationFn: () =>
      addMaterialNote(orgId, extension.id, { title: title.trim(), text: text.trim() }),
    onSuccess: () => {
      setTitle("");
      setText("");
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const upload = useMutation({
    mutationFn: (file: File) =>
      uploadMaterial(orgId, extension.id, file, file.name),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const extract = useMutation({
    mutationFn: (materialId: string) =>
      extractMaterial(orgId, extension.id, materialId),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const archive = useMutation({
    mutationFn: (materialId: string) =>
      archiveMaterial(orgId, extension.id, materialId),
    onSuccess: onChanged,
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  return (
    <div>
      <h4 className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
        <FileText className="h-4 w-4 text-teal-600" aria-hidden="true" />
        Training material
      </h4>
      <p className="mt-1 text-xs text-ink-500">
        Manuals and screenshots go into the assistant's documents. Screen
        recordings and audio instructions are added here.
      </p>

      <div className="mt-3 space-y-2">
        {(materials.data ?? []).map((material) => (
          <MaterialRow
            key={material.id}
            material={material}
            busy={extract.isPending || archive.isPending}
            onExtract={() => extract.mutate(material.id)}
            onArchive={() => archive.mutate(material.id)}
          />
        ))}
        {materials.data?.length === 0 ? (
          <p className="text-sm text-ink-400">No material yet.</p>
        ) : null}
      </div>

      <form
        className="mt-4 space-y-2"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          setError(null);
          addNote.mutate();
        }}
      >
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          required
          minLength={2}
          placeholder="Written procedure title"
          className="w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
        />
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          required
          minLength={2}
          rows={3}
          placeholder="Describe the operation step by step."
          className="w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
        />
        {error ? (
          <p className="flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={addNote.isPending}
            className="inline-flex items-center gap-2 rounded-full bg-mist-100 px-4 py-2 text-sm font-semibold text-ink-700 hover:bg-mist-200 disabled:opacity-60"
          >
            {addNote.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Plus className="h-4 w-4" aria-hidden="true" />
            )}
            Add written procedure
          </button>
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-mist-300 px-4 py-2 text-sm font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700">
            <Upload className="h-4 w-4" aria-hidden="true" />
            Upload recording or audio
            <input
              type="file"
              className="hidden"
              accept="video/*,audio/*"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload.mutate(file);
              }}
            />
          </label>
        </div>
      </form>
    </div>
  );
}

function MaterialRow({
  material,
  busy,
  onExtract,
  onArchive,
}: {
  material: TrainingMaterial;
  busy: boolean;
  onExtract: () => void;
  onArchive: () => void;
}) {
  const Icon =
    material.kind === "video"
      ? MonitorPlay
      : material.kind === "audio"
        ? Mic
        : FileText;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-mist-200 px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <Icon className="h-4 w-4 shrink-0 text-teal-600" aria-hidden="true" />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink-900">
            {material.title}
          </p>
          <p className="truncate text-xs text-ink-500">
            {material.kind}
            {material.status === "failed" && material.error_message
              ? ` · ${material.error_message}`
              : ""}
            {material.extracted_units
              ? ` · ${material.extracted_units} characters read`
              : ""}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        {material.kind === "video" || material.kind === "audio" ? (
          <button
            type="button"
            onClick={onExtract}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-full border border-mist-300 px-3 py-1 text-xs font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700 disabled:opacity-60"
          >
            <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
            Read
          </button>
        ) : null}
        <button
          type="button"
          onClick={onArchive}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-full border border-mist-300 px-3 py-1 text-xs font-semibold text-ink-700 hover:border-danger-500 hover:text-danger-500 disabled:opacity-60"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          Remove
        </button>
      </div>
    </div>
  );
}

function Procedures({
  orgId,
  extension,
  onChanged,
}: {
  orgId: string;
  extension: Extension;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const procedures = useQuery({
    queryKey: ["extension-procedures", extension.id],
    queryFn: () => fetchProcedures(orgId, extension.id),
  });

  const decide = useMutation({
    mutationFn: ({
      procedureId,
      status,
    }: {
      procedureId: string;
      status: "approved" | "rejected";
    }) => updateProcedure(orgId, extension.id, procedureId, { status }),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  return (
    <div>
      <h4 className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
        <CheckCircle2 className="h-4 w-4 text-teal-600" aria-hidden="true" />
        Extracted procedures
      </h4>
      <p className="mt-1 text-xs text-ink-500">
        Review what the model understood. Only approved procedures are used by
        the extension.
      </p>
      {error ? (
        <p className="mt-2 flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
        </p>
      ) : null}
      <div className="mt-3 space-y-3">
        {(procedures.data ?? []).map((procedure) => (
          <ProcedureCard
            key={procedure.id}
            procedure={procedure}
            busy={decide.isPending}
            onApprove={() =>
              decide.mutate({ procedureId: procedure.id, status: "approved" })
            }
            onReject={() =>
              decide.mutate({ procedureId: procedure.id, status: "rejected" })
            }
          />
        ))}
        {procedures.data?.length === 0 ? (
          <p className="text-sm text-ink-400">
            Nothing extracted yet — add material and analyse it.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function ProcedureCard({
  procedure,
  busy,
  onApprove,
  onReject,
}: {
  procedure: ExtensionProcedure;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="rounded-xl border border-mist-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-ink-900">{procedure.title}</p>
          {procedure.summary ? (
            <p className="mt-0.5 text-xs text-ink-500">{procedure.summary}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-400">
            confidence {Math.round(procedure.confidence * 100)}%
          </span>
          <Pill value={procedure.status} />
        </div>
      </div>
      <ol className="mt-3 space-y-2">
        {procedure.steps.map((step) => (
          <li key={step.order} className="text-sm text-ink-700">
            <span className="font-semibold text-ink-900">{step.order}.</span>{" "}
            {step.instruction}
            {step.where ? (
              <span className="text-ink-500"> — {step.where}</span>
            ) : null}
            {step.value ? (
              <span className="text-ink-500"> — enter {step.value}</span>
            ) : null}
            {step.sensitive ? (
              <span className="ml-2 rounded-full bg-danger-500/10 px-2 py-0.5 text-xs font-semibold text-danger-500">
                needs confirmation
              </span>
            ) : null}
          </li>
        ))}
      </ol>
      {procedure.status !== "approved" ? (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={onApprove}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-700 disabled:opacity-60"
          >
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            Approve
          </button>
          <button
            type="button"
            onClick={onReject}
            disabled={busy}
            className="rounded-full border border-mist-300 px-3 py-1.5 text-xs font-semibold text-ink-700 hover:border-danger-500 hover:text-danger-500 disabled:opacity-60"
          >
            Reject
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Tests({
  orgId,
  extension,
  onChanged,
}: {
  orgId: string;
  extension: Extension;
  onChanged: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [points, setPoints] = useState("");
  const [error, setError] = useState<string | null>(null);
  const tests = useQuery({
    queryKey: ["extension-tests", extension.id],
    queryFn: () => fetchTests(orgId, extension.id),
  });

  const run = useMutation({
    mutationFn: () =>
      runExtensionTest(orgId, extension.id, {
        question: question.trim(),
        expected_points: points
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  return (
    <div>
      <h4 className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
        <ShieldCheck className="h-4 w-4 text-teal-600" aria-hidden="true" />
        Tests
      </h4>
      <p className="mt-1 text-xs text-ink-500">
        Ask the question a new user would ask and list the facts the answer has
        to contain. The extension is only published after a test passes.
      </p>
      <form
        className="mt-3 space-y-2"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          setError(null);
          run.mutate();
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          required
          minLength={4}
          placeholder="How do I issue a receipt for a walk-in customer?"
          className="w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
        />
        <textarea
          value={points}
          onChange={(event) => setPoints(event.target.value)}
          required
          rows={2}
          placeholder={"Open the sales screen\nSelect the walk-in customer"}
          className="w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
        />
        {error ? (
          <p className="flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={run.isPending}
          className="inline-flex items-center gap-2 rounded-full bg-mist-100 px-4 py-2 text-sm font-semibold text-ink-700 hover:bg-mist-200 disabled:opacity-60"
        >
          {run.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <RotateCw className="h-4 w-4" aria-hidden="true" />
          )}
          Run test
        </button>
      </form>
      <div className="mt-3 space-y-2">
        {(tests.data ?? []).map((test) => (
          <TestRow key={test.id} test={test} />
        ))}
      </div>
    </div>
  );
}

function TestRow({ test }: { test: ExtensionTest }) {
  return (
    <div className="rounded-xl border border-mist-200 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-ink-900">{test.question}</p>
        <span
          className={cn(
            "rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1",
            test.outcome === "passed"
              ? "bg-teal-500/10 text-teal-700 ring-teal-500/30"
              : "bg-danger-500/10 text-danger-500 ring-danger-500/30",
          )}
        >
          {test.outcome} · {Math.round(test.score * 100)}%
        </span>
      </div>
      {test.missed_points.length > 0 ? (
        <p className="mt-1 text-xs text-danger-500">
          Missing: {test.missed_points.join(", ")}
        </p>
      ) : null}
      {test.answer ? (
        <p className="mt-1 whitespace-pre-line text-xs text-ink-500">
          {test.answer}
        </p>
      ) : null}
    </div>
  );
}
