import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Trash2, Upload, Play, Download, CheckCircle2, XCircle } from "lucide-react";
import {
  ApiRequestError,
  analyzeSubmodelVideo,
  createSubmodel,
  deleteSubmodel,
  deleteSubmodelVideo,
  downloadSubmodel,
  listAllSubmodels,
  listPublishedSubmodels,
  listSubmodelJobs,
  listSubmodelVideos,
  listSubmodelWorkflows,
  publishSubmodel,
  testSubmodel,
  trainSubmodel,
  unpublishSubmodel,
  uploadSubmodelVideo,
  useSubmodel,
  type Submodel,
  type SubmodelJob,
  type SubmodelTestResponse,
  type SubmodelVideo,
  type SubmodelWorkflow,
} from "../lib/api";
import { cn } from "../lib/cn";

function err(e: unknown): string {
  if (e instanceof ApiRequestError) return (e as ApiRequestError).message;
  if (e instanceof Error) return (e as Error).message;
  return "Something went wrong.";
}

export function SubmodelsPanel({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Submodel | null>(null);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [testInstruction, setTestInstruction] = useState("");
  const [testAnswer, setTestAnswer] = useState<string | null>(null);
  const [chatInstruction, setChatInstruction] = useState("");
  const [chatAnswer, setChatAnswer] = useState<string | null>(null);
  const [jobPoll, setJobPoll] = useState<string | null>(null);

  const published = useQuery({ queryKey: ["submodels", "published"], queryFn: listPublishedSubmodels });
  const all = useQuery({ queryKey: ["submodels", "all"], queryFn: listAllSubmodels, enabled: isAdmin });

  const createMut = useMutation({
    mutationFn: () => createSubmodel({ name: newName, description: newDesc || undefined }),
    onSuccess: () => {
      setNewName("");
      setNewDesc("");
      qc.invalidateQueries({ queryKey: ["submodels"] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteSubmodel(id),
    onSuccess: () => {
      setSelected(null);
      qc.invalidateQueries({ queryKey: ["submodels"] });
    },
  });

  const publishMut = useMutation({
    mutationFn: (id: string) => publishSubmodel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["submodels"] }),
  });

  const unpublishMut = useMutation({
    mutationFn: (id: string) => unpublishSubmodel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["submodels"] }),
  });

  const videos = useQuery({
    queryKey: ["submodel-videos", selected?.id],
    queryFn: () => listSubmodelVideos(selected!.id),
    enabled: !!selected && isAdmin,
  });

  const workflows = useQuery({
    queryKey: ["submodel-workflows", selected?.id],
    queryFn: () => listSubmodelWorkflows(selected!.id),
    enabled: !!selected && isAdmin,
  });

  const jobs = useQuery({
    queryKey: ["submodel-jobs", selected?.id],
    queryFn: () => listSubmodelJobs(selected!.id),
    enabled: !!selected && isAdmin,
    refetchInterval: jobPoll ? 2000 : false,
  });

  const uploadMut = useMutation({
    mutationFn: async (file: File) => {
      if (!selected) throw new Error("No submodel selected");
      return uploadSubmodelVideo(selected.id, file, file.name);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["submodel-videos", selected?.id] }),
  });

  const analyzeMut = useMutation({
    mutationFn: (videoId: string) => {
      if (!selected) throw new Error("No submodel selected");
      return analyzeSubmodelVideo(selected.id, videoId);
    },
    onSuccess: (job: SubmodelJob) => {
      setJobPoll(job.id);
      qc.invalidateQueries({ queryKey: ["submodel-videos", selected?.id] });
      qc.invalidateQueries({ queryKey: ["submodel-jobs", selected?.id] });
    },
  });

  const trainMut = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("No submodel selected");
      return trainSubmodel(selected.id);
    },
    onSuccess: (job: SubmodelJob) => {
      setJobPoll(job.id);
      qc.invalidateQueries({ queryKey: ["submodels"] });
      qc.invalidateQueries({ queryKey: ["submodel-jobs", selected?.id] });
      qc.invalidateQueries({ queryKey: ["submodel-workflows", selected?.id] });
    },
  });

  const testMut = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("No submodel selected");
      return testSubmodel(selected.id, testInstruction);
    },
    onSuccess: (res: SubmodelTestResponse) => setTestAnswer(res.answer),
  });

  const useMut = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("No submodel selected");
      return useSubmodel(selected.id, chatInstruction);
    },
    onSuccess: (res: SubmodelTestResponse) => setChatAnswer(res.answer),
  });

  const items = (isAdmin ? (all.data ?? []) : (published.data ?? [])) as Submodel[];
  const listLoading = isAdmin ? all.isLoading : published.isLoading;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-ink-900">AI Submodels</h2>
        {isAdmin && (
          <span className="rounded-full bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-700">
            Administrator
          </span>
        )}
      </div>

      {isAdmin && (
        <div className="rounded-2xl bg-white p-4 ring-1 ring-mist-200 shadow-card space-y-3">
          <p className="text-sm font-semibold text-ink-900">Create a new submodel</p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Name (e.g. EBM Assistant)"
              className="flex-1 rounded-xl border border-mist-200 px-3 py-2 text-sm"
            />
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Description (optional)"
              className="flex-1 rounded-xl border border-mist-200 px-3 py-2 text-sm"
            />
            <button
              onClick={() => createMut.mutate()}
              disabled={!newName.trim() || createMut.isPending}
              className="inline-flex items-center gap-2 rounded-xl bg-teal-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {createMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Create
            </button>
          </div>
          {createMut.isError && <p className="text-sm text-danger-500">{err(createMut.error)}</p>}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
            {isAdmin ? "All submodels" : "Available submodels"}
          </p>
          {listLoading && <Loader2 className="h-5 w-5 animate-spin text-ink-400" />}
          {items.map((sm) => (
            <button
              key={sm.id}
              onClick={() => setSelected(sm)}
              className={cn(
                "w-full rounded-xl border p-3 text-left transition",
                selected?.id === sm.id ? "border-teal-500 bg-teal-50" : "border-mist-200 bg-white hover:bg-mist-50",
              )}
            >
              <p className="text-sm font-semibold text-ink-900">{sm.name}</p>
              <p className="text-xs text-ink-500">
                {sm.status} · v{sm.version} · {sm.video_count} videos · {sm.workflow_count} workflows
              </p>
            </button>
          ))}
          {items.length === 0 && !listLoading && (
            <p className="text-sm text-ink-400">No submodels yet.</p>
          )}
        </div>

        <div className="lg:col-span-2 space-y-4">
          {!selected && (
            <div className="rounded-2xl bg-white p-6 ring-1 ring-mist-200 text-sm text-ink-500">
              Select a submodel to view details.
            </div>
          )}
          {selected && (
            <div className="space-y-4">
              <div className="rounded-2xl bg-white p-4 ring-1 ring-mist-200 shadow-card space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="text-lg font-semibold text-ink-900">{selected.name}</h3>
                    <p className="text-sm text-ink-500">{selected.description}</p>
                    <p className="text-xs text-ink-400 mt-1">Status: {selected.status} · Version {selected.version}</p>
                  </div>
                  {isAdmin && (
                    <div className="flex flex-wrap gap-2">
                      {selected.status !== "published" ? (
                        <button
                          onClick={() => publishMut.mutate(selected.id)}
                          disabled={selected.status !== "trained" || publishMut.isPending}
                          className="rounded-lg bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
                        >
                          Publish
                        </button>
                      ) : (
                        <button
                          onClick={() => unpublishMut.mutate(selected.id)}
                          className="rounded-lg bg-mist-200 px-3 py-1.5 text-xs font-semibold text-ink-700"
                        >
                          Unpublish
                        </button>
                      )}
                      <button
                        onClick={() => downloadSubmodel(selected.id)}
                        className="inline-flex items-center gap-1 rounded-lg bg-mist-200 px-3 py-1.5 text-xs font-semibold text-ink-700"
                      >
                        <Download className="h-3.5 w-3.5" /> Download
                      </button>
                      <button
                        onClick={() => {
                          if (confirm("Delete this submodel?")) deleteMut.mutate(selected.id);
                        }}
                        className="inline-flex items-center gap-1 rounded-lg bg-danger-500/10 px-3 py-1.5 text-xs font-semibold text-danger-500"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Delete
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {isAdmin && (
                <>
                  <div className="rounded-2xl bg-white p-4 ring-1 ring-mist-200 shadow-card space-y-3">
                    <p className="text-sm font-semibold text-ink-900">Training videos</p>
                    <label className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-dashed border-mist-300 px-4 py-2 text-sm text-ink-600 hover:bg-mist-50">
                      <Upload className="h-4 w-4" />
                      Upload video
                      <input
                        type="file"
                        accept="video/*"
                        className="hidden"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f) uploadMut.mutate(f);
                          e.target.value = "";
                        }}
                      />
                    </label>
                    {uploadMut.isPending && <Loader2 className="h-4 w-4 animate-spin text-teal-600" />}
                    {uploadMut.isError && <p className="text-sm text-danger-500">{err(uploadMut.error)}</p>}
                    <ul className="space-y-2">
                      {(videos.data ?? []).map((v: SubmodelVideo) => (
                        <li key={v.id} className="flex items-center justify-between rounded-lg border border-mist-200 px-3 py-2 text-sm">
                          <div>
                            <p className="font-medium text-ink-900">{v.title}</p>
                            <p className="text-xs text-ink-500">
                              {v.status} · {v.frame_count ?? 0} frames
                              {v.error_message ? ` · ${v.error_message.slice(0, 60)}` : ""}
                            </p>
                          </div>
                          <div className="flex gap-2">
                            <button
                              onClick={() => analyzeMut.mutate(v.id)}
                              disabled={analyzeMut.isPending}
                              className="rounded-lg bg-mist-200 px-2 py-1 text-xs font-semibold text-ink-700"
                            >
                              Analyze
                            </button>
                            <button
                              onClick={() => {
                                if (confirm("Delete video?")) deleteSubmodelVideo(v.id).then(() => qc.invalidateQueries({ queryKey: ["submodel-videos", selected?.id] }));
                              }}
                              className="rounded-lg bg-danger-500/10 px-2 py-1 text-xs font-semibold text-danger-500"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="rounded-2xl bg-white p-4 ring-1 ring-mist-200 shadow-card space-y-3">
                    <p className="text-sm font-semibold text-ink-900">Training & testing</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => trainMut.mutate()}
                        disabled={trainMut.isPending}
                        className="inline-flex items-center gap-2 rounded-xl bg-teal-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                      >
                        {trainMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        Train
                      </button>
                    </div>
                    {trainMut.isError && <p className="text-sm text-danger-500">{err(trainMut.error)}</p>}
                    {trainMut.isSuccess && !!trainMut.data && (
                      <p className="text-sm text-teal-700">
                        Training job: {(trainMut.data as SubmodelJob).status} ({(trainMut.data as SubmodelJob).progress}%)
                      </p>
                    )}
                    <div className="flex gap-2">
                      <input
                        value={testInstruction}
                        onChange={(e) => setTestInstruction(e.target.value)}
                        placeholder="Test instruction..."
                        className="flex-1 rounded-xl border border-mist-200 px-3 py-2 text-sm"
                      />
                      <button
                        onClick={() => testMut.mutate()}
                        disabled={!testInstruction.trim() || testMut.isPending}
                        className="rounded-xl bg-mist-200 px-4 py-2 text-sm font-semibold text-ink-700 disabled:opacity-50"
                      >
                        Test
                      </button>
                    </div>
                    {testMut.isError && <p className="text-sm text-danger-500">{err(testMut.error)}</p>}
                    {testAnswer && (
                      <div className="rounded-xl bg-mist-50 p-3 text-sm text-ink-800 whitespace-pre-wrap">{testAnswer}</div>
                    )}
                  </div>

                  <div className="rounded-2xl bg-white p-4 ring-1 ring-mist-200 shadow-card">
                    <p className="text-sm font-semibold text-ink-900 mb-2">Jobs</p>
                    <ul className="space-y-1 text-xs text-ink-600">
                      {(jobs.data ?? []).map((j: SubmodelJob) => (
                        <li key={j.id} className="flex items-center gap-2">
                          {j.status === "succeeded" ? <CheckCircle2 className="h-3.5 w-3.5 text-teal-600" /> : j.status === "failed" ? <XCircle className="h-3.5 w-3.5 text-danger-500" /> : <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                          <span>{j.kind}</span>
                          <span className="text-ink-400">{j.progress}%</span>
                          {j.error && <span className="text-danger-500 truncate">{j.error.slice(0, 60)}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="rounded-2xl bg-white p-4 ring-1 ring-mist-200 shadow-card">
                    <p className="text-sm font-semibold text-ink-900 mb-2">Workflows</p>
                    <ul className="space-y-1 text-xs text-ink-600">
                      {(workflows.data ?? []).map((w: SubmodelWorkflow) => (
                        <li key={w.id}>{w.name} ({w.steps.length} steps)</li>
                      ))}
                    </ul>
                  </div>
                </>
              )}

              {!isAdmin && selected.status === "published" && (
                <div className="rounded-2xl bg-white p-4 ring-1 ring-mist-200 shadow-card space-y-3">
                  <p className="text-sm font-semibold text-ink-900">Use this assistant</p>
                  <div className="flex gap-2">
                    <input
                      value={chatInstruction}
                      onChange={(e) => setChatInstruction(e.target.value)}
                      placeholder="Ask the assistant..."
                      className="flex-1 rounded-xl border border-mist-200 px-3 py-2 text-sm"
                    />
                    <button
                      onClick={() => useMut.mutate()}
                      disabled={!chatInstruction.trim() || useMut.isPending}
                      className="rounded-xl bg-teal-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                    >
                      Send
                    </button>
                  </div>
                  {useMut.isError && <p className="text-sm text-danger-500">{err(useMut.error)}</p>}
                  {chatAnswer && (
                    <div className="rounded-xl bg-mist-50 p-3 text-sm text-ink-800 whitespace-pre-wrap">{chatAnswer}</div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
