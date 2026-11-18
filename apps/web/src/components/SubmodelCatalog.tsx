/** The published-submodel catalog for clients.
 *
 * A client sees only what the platform administrator has published: the
 * submodel's name, the task it learned and the answer it gives when asked.
 * Training material, draft workflows, tests and packages never appear here —
 * the backend refuses them with a 404 even if an id is guessed.
 *
 * The administrator's console lives in `SubmodelsPanel`; this is the other
 * side of the same feature: use, not administration.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Loader2,
  MessageSquare,
  Sparkles,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  askSubmodel,
  fetchPublishedSubmodels,
  type Submodel,
  type SubmodelGuidance,
} from "../lib/api";
import { cn } from "../lib/cn";

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function SubmodelCatalog({ orgId }: { orgId: string | null }) {
  const [selected, setSelected] = useState<string | null>(null);

  const catalog = useQuery({
    queryKey: ["published-submodels", orgId],
    queryFn: () => fetchPublishedSubmodels(orgId as string),
    enabled: !!orgId,
  });

  const current =
    catalog.data?.find((item) => item.id === selected) ?? null;

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight text-ink-900">
          Submodels
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-ink-500">
          Trained models the platform administrator published. Pick one and
          ask it how to do the task it learned.
        </p>
      </header>

      {catalog.isPending ? (
        <p className="flex items-center gap-2 text-sm text-ink-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading submodels…
        </p>
      ) : catalog.isError ? (
        <p className="rounded-xl bg-danger-500/10 px-4 py-3 text-sm text-danger-500">
          <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden="true" />
          {errorMessage(catalog.error)}
        </p>
      ) : (catalog.data?.length ?? 0) === 0 ? (
        <p className="rounded-2xl bg-white p-6 text-sm text-ink-500 shadow-card ring-1 ring-mist-200">
          No submodel has been published yet. When the administrator
          publishes one, it appears here.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {(catalog.data ?? []).map((submodel) => (
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
              <p className="text-base font-semibold text-ink-900">
                {submodel.name}
              </p>
              <p className="mt-0.5 text-sm text-ink-500">
                {submodel.system_name} · version {submodel.version}
              </p>
              <p className="mt-3 text-sm text-ink-700">{submodel.task}</p>
              <p className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-teal-500/10 px-2.5 py-0.5 text-xs font-semibold text-teal-700">
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                Published · {submodel.approved_workflows_count} workflow(s)
              </p>
            </button>
          ))}
        </div>
      )}

      {current && orgId ? <AskSubmodel key={current.id} orgId={orgId} submodel={current} /> : null}
    </div>
  );
}

function AskSubmodel({ orgId, submodel }: { orgId: string; submodel: Submodel }) {
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
    <section className="rounded-2xl bg-white p-6 shadow-card ring-1 ring-mist-200">
      <h3 className="text-lg font-semibold text-ink-900">{submodel.name}</h3>
      <p className="mt-1 text-sm text-ink-500">{submodel.task}</p>

      <h4 className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-ink-900">
        <MessageSquare className="h-4 w-4 text-teal-600" aria-hidden="true" />
        Ask it how to do something
      </h4>
      <p className="mt-1 text-xs text-ink-500">
        The answer comes from the workflows the submodel learned. Steps that
        would change something sensitive always ask you before anything
        happens.
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
          placeholder={`How do I ${submodel.task}?`}
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
    </section>
  );
}
