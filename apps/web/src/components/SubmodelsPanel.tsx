/** Training console for WorkMeta AI Submodels.
 *
 * A submodel is a trained model for **one task** rather than for one system.
 * The workflow is: describe the task, upload how the work is done, let the
 * model read the recording as a sequence of screens and actions, approve the
 * workflows it found, test it, and then download it as a signed package that
 * installs and works on another machine.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Eye,
  EyeOff,
  FileText,
  Loader2,
  MessageSquare,
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
  addSubmodelMaterialNote,
  archiveSubmodelMaterial,
  askSubmodel,
  createSubmodel,
  downloadSubmodelPackage,
  fetchSubmodelMaterials,
  fetchSubmodelPackage,
  fetchSubmodels,
  fetchSubmodelTests,
  fetchSubmodelWorkflows,
  installSubmodel,
  readSubmodelMaterial,
  retireSubmodel,
  runSubmodelTest,
  trainSubmodel,
  updateSubmodelWorkflow,
  uploadSubmodelMaterial,
  type Submodel,
  type SubmodelGuidance,
  type SubmodelMaterial,
  type SubmodelTest,
  type SubmodelWorkflow,
} from "../lib/api";
import { cn } from "../lib/cn";

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-mist-100 text-ink-600 ring-mist-200",
  training: "bg-teal-500/10 text-teal-700 ring-teal-500/30",
  trained: "bg-teal-500/10 text-teal-700 ring-teal-500/30",
  published: "bg-navy-900 text-white ring-navy-900",
  retired: "bg-mist-100 text-ink-500 ring-mist-200",
  approved: "bg-teal-500/10 text-teal-700 ring-teal-500/30",
  rejected: "bg-mist-100 text-ink-500 ring-mist-200",
  ready: "bg-teal-500/10 text-teal-700 ring-teal-500/30",
  failed: "bg-danger-500/10 text-danger-500 ring-danger-500/30",
  stored: "bg-mist-100 text-ink-600 ring-mist-200",
  extracting: "bg-teal-500/10 text-teal-700 ring-teal-500/30",
  archived: "bg-mist-100 text-ink-500 ring-mist-200",
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

export function SubmodelsPanel({ orgId }: { orgId: string }) {
  const [selected, setSelected] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const submodels = useQuery({
    queryKey: ["submodels", orgId],
    queryFn: () => fetchSubmodels(orgId),
  });

  const current = submodels.data?.find((item) => item.id === selected) ?? null;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-ink-900">
            WorkMeta AI Submodels
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">
            Record the task once, upload the demo, and the model works out the
            steps. Train it, check it, then download it as a package that works
            on any other computer.
          </p>
        </div>
      </header>

      <CreateSubmodel
        orgId={orgId}
        onCreated={(submodel) => {
          void queryClient.invalidateQueries({ queryKey: ["submodels", orgId] });
          setSelected(submodel.id);
        }}
      />

      <InstallSubmodel
        orgId={orgId}
        onInstalled={(submodel) => {
          void queryClient.invalidateQueries({ queryKey: ["submodels", orgId] });
          setSelected(submodel.id);
        }}
      />

      {submodels.isPending ? (
        <p className="flex items-center gap-2 text-sm text-ink-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading submodels…
        </p>
      ) : submodels.isError ? (
        <p className="rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
          {errorMessage(submodels.error)}
        </p>
      ) : submodels.data.length === 0 ? (
        <p className="rounded-2xl bg-white p-6 text-sm text-ink-500 shadow-card ring-1 ring-mist-200">
          No submodel yet. Describe a task you repeat every day and train one.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {submodels.data.map((submodel) => (
            <button
              key={submodel.id}
              type="button"
              onClick={() => setSelected(submodel.id)}
              className={cn(
                "rounded-2xl bg-white p-5 text-left shadow-card ring-1 transition",
                submodel.id === selected
                  ? "ring-teal-500"
                  : "ring-mist-200 hover:ring-teal-500/50",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-base font-semibold text-ink-900">
                    {submodel.name}
                  </p>
                  <p className="mt-0.5 text-sm text-ink-500">
                    {submodel.system_name} · version {submodel.system_version}
                  </p>
                </div>
                <div className="flex flex-wrap justify-end gap-1.5">
                  <Pill value={submodel.status} />
                  {submodel.origin === "imported" ? (
                    <span className="rounded-full bg-mist-100 px-2.5 py-0.5 text-xs font-semibold text-ink-600 ring-1 ring-mist-200">
                      imported
                    </span>
                  ) : null}
                </div>
              </div>
              <p className="mt-3 text-sm text-ink-700">{submodel.task}</p>
              <dl className="mt-4 grid grid-cols-3 gap-3 text-xs text-ink-500">
                <div>
                  <dt className="font-medium text-ink-700">Material</dt>
                  <dd>{submodel.materials_count}</dd>
                </div>
                <div>
                  <dt className="font-medium text-ink-700">Workflows</dt>
                  <dd>
                    {submodel.approved_workflows_count}/{submodel.workflows_count}
                  </dd>
                </div>
                <div>
                  <dt className="font-medium text-ink-700">Tests passed</dt>
                  <dd>
                    {submodel.passed_tests_count}/{submodel.tests_count}
                  </dd>
                </div>
              </dl>
              {submodel.source_label ? (
                <p className="mt-3 text-xs text-ink-400">
                  From: {submodel.source_label}
                </p>
              ) : null}
            </button>
          ))}
        </div>
      )}

      {current ? (
        <SubmodelDetail
          key={current.id}
          orgId={orgId}
          submodel={current}
          onChanged={() =>
            void queryClient.invalidateQueries({ queryKey: ["submodels", orgId] })
          }
        />
      ) : null}
    </div>
  );
}

function CreateSubmodel({
  orgId,
  onCreated,
}: {
  orgId: string;
  onCreated: (submodel: Submodel) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [task, setTask] = useState("");
  const [systemCode, setSystemCode] = useState("");
  const [systemName, setSystemName] = useState("");
  const [systemVersion, setSystemVersion] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      createSubmodel(orgId, {
        name: name.trim(),
        task: task.trim(),
        system_code: systemCode.trim().toLowerCase(),
        system_name: systemName.trim(),
        system_version: systemVersion.trim(),
        target_kind: "web",
      }),
    onSuccess: (submodel) => {
      setError(null);
      setOpen(false);
      setName("");
      setTask("");
      setSystemCode("");
      setSystemName("");
      setSystemVersion("");
      onCreated(submodel);
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  return (
    <section className="rounded-2xl bg-white p-5 shadow-card ring-1 ring-mist-200">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            Train a new submodel
          </h3>
          <p className="mt-0.5 text-sm text-ink-500">
            Say which task the model is for, then upload how the work is done.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="inline-flex items-center gap-2 rounded-full bg-navy-900 px-4 py-2 text-sm font-semibold text-white hover:bg-navy-800"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          {open ? "Close" : "New submodel"}
        </button>
      </div>

      {open ? (
        <form
          className="mt-4 grid gap-4 sm:grid-cols-2"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            setError(null);
            mutation.mutate();
          }}
        >
          <label className="block">
            <span className="text-sm font-medium text-ink-700">
              Submodel name
            </span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              minLength={2}
              placeholder="EBM receipts"
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
          <label className="block sm:col-span-2">
            <span className="text-sm font-medium text-ink-700">
              The task this model is for
            </span>
            <input
              value={task}
              onChange={(event) => setTask(event.target.value)}
              required
              minLength={4}
              placeholder="Issue a receipt for a walk-in customer"
              className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
            />
            <span className="mt-1 block text-xs text-ink-400">
              Write it the way your staff would ask for it. You will upload a
              video demo of exactly this task next.
            </span>
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
              Create submodel
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

function InstallSubmodel({
  orgId,
  onInstalled,
}: {
  orgId: string;
  onInstalled: (submodel: Submodel) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [label, setLabel] = useState("");

  const mutation = useMutation({
    mutationFn: (file: File) =>
      installSubmodel(orgId, file, label.trim() || undefined),
    onSuccess: (submodel) => {
      setError(null);
      setMessage(
        `Installed "${submodel.name}". It already knows ${submodel.approved_workflows_count} workflow(s) and needs no training here.`,
      );
      setLabel("");
      onInstalled(submodel);
    },
    onError: (e: unknown) => {
      setMessage(null);
      setError(errorMessage(e));
    },
  });

  return (
    <section className="rounded-2xl bg-white p-5 shadow-card ring-1 ring-mist-200">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            Install a submodel
          </h3>
          <p className="mt-0.5 text-sm text-ink-500">
            Install a package someone else trained. The workflows travel with
            it, so it works here without training again.
          </p>
        </div>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-mist-300 px-4 py-2 text-sm font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700">
          {mutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Package className="h-4 w-4" aria-hidden="true" />
          )}
          Choose package file
          <input
            type="file"
            className="hidden"
            accept=".json,application/json"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) mutation.mutate(file);
              event.target.value = "";
            }}
          />
        </label>
      </div>
      <label className="mt-3 block">
        <span className="text-sm font-medium text-ink-700">
          Where did this package come from? (optional)
        </span>
        <input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="Trained by Example Rwanda Ltd"
          className="mt-1.5 w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
        />
      </label>
      {message ? (
        <p className="mt-3 rounded-xl bg-teal-500/10 px-4 py-3 text-sm text-teal-700">
          {message}
        </p>
      ) : null}
      {error ? (
        <p className="mt-3 flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
        </p>
      ) : null}
    </section>
  );
}

function SubmodelDetail({
  orgId,
  submodel,
  onChanged,
}: {
  orgId: string;
  submodel: Submodel;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pkg = useQuery({
    queryKey: ["submodel-package", submodel.id],
    queryFn: () => fetchSubmodelPackage(orgId, submodel.id),
    retry: false,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ["submodel-materials", submodel.id],
    });
    void queryClient.invalidateQueries({
      queryKey: ["submodel-workflows", submodel.id],
    });
    void queryClient.invalidateQueries({ queryKey: ["submodel-tests", submodel.id] });
    void queryClient.invalidateQueries({
      queryKey: ["submodel-package", submodel.id],
    });
    onChanged();
  };

  const train = useMutation({
    mutationFn: () => trainSubmodel(orgId, submodel.id),
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

  const retire = useMutation({
    mutationFn: () => retireSubmodel(orgId, submodel.id),
    onSuccess: () => {
      setMessage("Submodel retired.");
      refresh();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const download = useMutation({
    mutationFn: () => downloadSubmodelPackage(orgId, submodel.id),
    onSuccess: ({ filename, content }) => {
      const blob = new Blob([content], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      setError(null);
      setMessage(`Downloaded ${filename}. Install it on the other computer.`);
    },
    onError: (e: unknown) => {
      setError(errorMessage(e));
      setMessage(null);
    },
  });

  return (
    <section className="space-y-6 rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-ink-900">
            {submodel.name}
          </h3>
          <p className="mt-1 text-sm text-ink-500">
            {submodel.system_name} {submodel.system_version} · {submodel.task}
          </p>
          {submodel.source_label ? (
            <p className="mt-1 text-xs text-ink-400">
              Installed from {submodel.source_label}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Pill value={submodel.status} />
          <button
            type="button"
            onClick={() => train.mutate()}
            disabled={train.isPending || submodel.status === "retired"}
            className="inline-flex items-center gap-2 rounded-full border border-mist-300 px-4 py-2 text-sm font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700 disabled:opacity-60"
          >
            {train.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            )}
            Train
          </button>
          <button
            type="button"
            onClick={() => download.mutate()}
            disabled={
              download.isPending || submodel.approved_workflows_count === 0
            }
            title={
              submodel.approved_workflows_count === 0
                ? "Approve at least one workflow first"
                : undefined
            }
            className="inline-flex items-center gap-2 rounded-full bg-navy-900 px-4 py-2 text-sm font-semibold text-white hover:bg-navy-800 disabled:opacity-50"
          >
            {download.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="h-4 w-4" aria-hidden="true" />
            )}
            Download package
          </button>
          {submodel.status !== "retired" ? (
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

      <p className="text-xs text-ink-400">
        Retiring takes a submodel out of use. It keeps its record, because it
        is part of what was published and of the audit trail.
      </p>

      <Materials orgId={orgId} submodel={submodel} onChanged={refresh} />
      <Workflows orgId={orgId} submodel={submodel} onChanged={refresh} />
      <Tests orgId={orgId} submodel={submodel} onChanged={refresh} />
      <Ask orgId={orgId} submodel={submodel} />

      {pkg.data ? (
        <div className="rounded-xl bg-mist-50 p-4">
          <p className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
            <ShieldCheck className="h-4 w-4 text-teal-600" aria-hidden="true" />
            Package {pkg.data.filename}
          </p>
          <p className="mt-1 text-xs text-ink-500">
            {pkg.data.workflow_count} workflow(s) ·{" "}
            {pkg.data.signing_key_id || "no signing key"} · digest{" "}
            {pkg.data.digest.slice(0, 12)}…
          </p>
          {pkg.data.reason ? (
            <p className="mt-1 text-xs text-ink-400">{pkg.data.reason}</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Materials({
  orgId,
  submodel,
  onChanged,
}: {
  orgId: string;
  submodel: Submodel;
  onChanged: () => void;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const materials = useQuery({
    queryKey: ["submodel-materials", submodel.id],
    queryFn: () => fetchSubmodelMaterials(orgId, submodel.id),
  });

  const addNote = useMutation({
    mutationFn: () =>
      addSubmodelMaterialNote(orgId, submodel.id, {
        title: title.trim(),
        text: text.trim(),
      }),
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
      uploadSubmodelMaterial(orgId, submodel.id, file, file.name),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });
  const read = useMutation({
    mutationFn: (materialId: string) =>
      readSubmodelMaterial(orgId, submodel.id, materialId),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const archive = useMutation({
    mutationFn: (materialId: string) =>
      archiveSubmodelMaterial(orgId, submodel.id, materialId),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errorMessage(e)),
  });

  const busy = read.isPending || archive.isPending;

  return (
    <div>
      <h4 className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
        <MonitorPlay className="h-4 w-4 text-teal-600" aria-hidden="true" />
        Video demo
      </h4>
      <p className="mt-1 text-xs text-ink-500">
        Record the task being done and upload it here. The model reads the
        recording as a sequence of screens and works out the action that was
        performed on each one.
      </p>

      <div className="mt-3 rounded-xl border border-dashed border-teal-500/40 bg-teal-500/5 p-4">
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-full bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700">
          {upload.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Upload className="h-4 w-4" aria-hidden="true" />
          )}
          {upload.isPending ? "Reading the demo…" : "Upload a video demo"}
          <input
            type="file"
            className="hidden"
            accept="video/*"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) upload.mutate(file);
              event.target.value = "";
            }}
          />
        </label>
        <p className="mt-2 text-xs text-ink-500">
          MP4, WebM or MOV. The demo is read as soon as it is uploaded, so you
          can check what the model saw before you train.
        </p>
      </div>

      <div className="mt-3 space-y-2">
        {(materials.data ?? []).map((material) => (
          <MaterialRow
            key={material.id}
            material={material}
            busy={busy}
            onRead={() => read.mutate(material.id)}
            onArchive={() => archive.mutate(material.id)}
          />
        ))}
        {materials.data?.length === 0 ? (
          <p className="text-sm text-ink-400">
            No demo yet. Upload a recording of the task.
          </p>
        ) : null}
      </div>

      <details className="mt-4">
        <summary className="cursor-pointer text-sm font-semibold text-ink-700">
          Written steps or an audio instruction
        </summary>
        <form
          className="mt-3 space-y-2"
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
              <Mic className="h-4 w-4" aria-hidden="true" />
              Upload an audio instruction
              <input
                type="file"
                className="hidden"
                accept="audio/*"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) upload.mutate(file);
                  event.target.value = "";
                }}
              />
            </label>
          </div>
        </form>
      </details>
    </div>
  );
}

function MaterialRow({
  material,
  busy,
  onRead,
  onArchive,
}: {
  material: SubmodelMaterial;
  busy: boolean;
  onRead: () => void;
  onArchive: () => void;
}) {
  const Icon =
    material.kind === "video"
      ? MonitorPlay
      : material.kind === "audio"
        ? Mic
        : FileText;
  const [showText, setShowText] = useState(false);
  const readable = material.extracted_text?.trim();
  return (
    <div className="rounded-xl border border-mist-200 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
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
                ? ` · ${material.extracted_units} screens read`
                : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Pill value={material.status} />
          {readable ? (
            <button
              type="button"
              onClick={() => setShowText((value) => !value)}
              className="inline-flex items-center gap-1.5 rounded-full border border-mist-300 px-3 py-1 text-xs font-semibold text-ink-700 hover:border-teal-500 hover:text-teal-700"
            >
              {showText ? (
                <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {showText ? "Hide" : "What was read"}
            </button>
          ) : null}
          {material.kind === "video" || material.kind === "audio" ? (
            <button
              type="button"
              onClick={onRead}
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
      {showText && readable ? (
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-lg bg-mist-50 p-3 text-xs text-ink-700">
          {readable}
        </pre>
      ) : null}
    </div>
  );
}

function Workflows({
  orgId,
  submodel,
  onChanged,
}: {
  orgId: string;
  submodel: Submodel;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const workflows = useQuery({
    queryKey: ["submodel-workflows", submodel.id],
    queryFn: () => fetchSubmodelWorkflows(orgId, submodel.id),
  });

  const decide = useMutation({
    mutationFn: ({
      workflowId,
      status,
    }: {
      workflowId: string;
      status: "approved" | "rejected";
    }) => updateSubmodelWorkflow(orgId, submodel.id, workflowId, { status }),
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
        Learned workflows
      </h4>
      <p className="mt-1 text-xs text-ink-500">
        What the model understood from the material. Only approved workflows
        are used and only approved workflows are exported.
      </p>
      {error ? (
        <p className="mt-2 flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
        </p>
      ) : null}
      <div className="mt-3 space-y-3">
        {(workflows.data ?? []).map((workflow) => (
          <WorkflowCard
            key={workflow.id}
            workflow={workflow}
            busy={decide.isPending}
            onApprove={() =>
              decide.mutate({ workflowId: workflow.id, status: "approved" })
            }
            onReject={() =>
              decide.mutate({ workflowId: workflow.id, status: "rejected" })
            }
          />
        ))}
        {workflows.data?.length === 0 ? (
          <p className="text-sm text-ink-400">
            Nothing learned yet — add material and train the submodel.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function WorkflowCard({
  workflow,
  busy,
  onApprove,
  onReject,
}: {
  workflow: SubmodelWorkflow;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="rounded-xl border border-mist-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-ink-900">{workflow.title}</p>
          {workflow.summary ? (
            <p className="mt-0.5 text-xs text-ink-500">{workflow.summary}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-400">
            confidence {Math.round(workflow.confidence * 100)}%
          </span>
          <Pill value={workflow.status} />
        </div>
      </div>
      <ol className="mt-3 space-y-2">
        {workflow.steps.map((step) => (
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
      {workflow.status !== "approved" ? (
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
  submodel,
  onChanged,
}: {
  orgId: string;
  submodel: Submodel;
  onChanged: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [points, setPoints] = useState("");
  const [error, setError] = useState<string | null>(null);

  const tests = useQuery({
    queryKey: ["submodel-tests", submodel.id],
    queryFn: () => fetchSubmodelTests(orgId, submodel.id),
  });

  const run = useMutation({
    mutationFn: () =>
      runSubmodelTest(orgId, submodel.id, {
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
        to contain.
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

function TestRow({ test }: { test: SubmodelTest }) {
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

function Ask({ orgId, submodel }: { orgId: string; submodel: Submodel }) {
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<SubmodelGuidance | null>(null);

  const ask = useMutation({
    mutationFn: () =>
      askSubmodel(orgId, submodel.id, { question: question.trim() }),
    onSuccess: (result) => {
      setError(null);
      setAnswer(result);
    },
    onError: (e: unknown) => {
      setAnswer(null);
      setError(errorMessage(e));
    },
  });

  return (
    <div>
      <h4 className="inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
        <MessageSquare className="h-4 w-4 text-teal-600" aria-hidden="true" />
        Try the submodel
      </h4>
      <p className="mt-1 text-xs text-ink-500">
        Ask the way a member of staff would. The answer comes from the approved
        workflows only — this works the same on a machine where the submodel
        was installed.
      </p>
      <form
        className="mt-3 space-y-2"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          setError(null);
          ask.mutate();
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          required
          minLength={2}
          placeholder="How do I issue a receipt for a walk-in customer?"
          className="w-full rounded-xl border border-mist-300 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none focus:border-teal-500"
        />
        {error ? (
          <p className="flex items-center gap-2 rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={ask.isPending}
          className="inline-flex items-center gap-2 rounded-full bg-mist-100 px-4 py-2 text-sm font-semibold text-ink-700 hover:bg-mist-200 disabled:opacity-60"
        >
          {ask.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <MessageSquare className="h-4 w-4" aria-hidden="true" />
          )}
          Ask
        </button>
      </form>
      {answer ? (
        <div className="mt-3 rounded-xl bg-mist-50 p-4">
          <p className="whitespace-pre-line text-sm text-ink-900">
            {answer.answer}
          </p>
          {answer.workflows_used.length > 0 ? (
            <p className="mt-2 text-xs text-ink-500">
              Used: {answer.workflows_used.join(", ")}
              {answer.grounded ? "" : " (not cited)"}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
