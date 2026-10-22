"use client";

import { useEffect, useId, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import type { Loadable } from "@/components/dashboard/types";
import { Card, ErrorMessage, Skeleton, Spinner } from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { createSequence, getSequences, type NewStep, type Sequence, type Tone } from "@/lib/api/sequences";

const TONES: Tone[] = ["FRIENDLY", "PROFESSIONAL", "FIRM"];
const DEFAULT_DRAFT: NewStep[] = [
  { offset_days: 1, channel: "EMAIL", tone: "FRIENDLY", requires_approver: false },
  { offset_days: 8, channel: "EMAIL", tone: "PROFESSIONAL", requires_approver: false },
  { offset_days: 15, channel: "EMAIL", tone: "FIRM", requires_approver: true },
  { offset_days: 30, channel: "TASK", requires_approver: false },
];

function Timeline({ sequence }: { sequence: Sequence }) {
  const last = Math.max(...sequence.steps.map((step) => step.offset_days), 1);
  return (
    <div className="space-y-3">
      <div className="relative h-2 rounded-full bg-slate-100" aria-hidden="true">
        {sequence.steps.map((step) => (
          <span
            key={step.order}
            className={`absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white ${
              step.channel === "TASK" ? "bg-slate-500" : step.tone === "FIRM" ? "bg-rose-500" : step.tone === "PROFESSIONAL" ? "bg-amber-500" : "bg-emerald-500"
            }`}
            style={{ left: `${(step.offset_days / last) * 100}%` }}
          />
        ))}
      </div>
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {sequence.steps.map((step) => (
          <li key={step.order} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
            <p className="font-semibold text-slate-900">
              Step {step.order} · due + {step.offset_days} {step.offset_days === 1 ? "day" : "days"}
            </p>
            <p className="text-slate-600">
              {step.channel === "TASK" ? "Call task for an operator" : `${step.tone.toLowerCase()} email`}
              {step.requires_approver ? " · approver required" : ""}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {sequence.enrollment_counts.by_step[String(step.order)] ?? 0} invoice(s) at this step
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function NewVersionForm({ onCreated }: { onCreated: () => void }) {
  const id = useId();
  const [name, setName] = useState("Standard collection");
  const [steps, setSteps] = useState<NewStep[]>(DEFAULT_DRAFT);
  const [makeDefault, setMakeDefault] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = (index: number, change: Partial<NewStep>) =>
    setSteps((current) => current.map((step, i) => (i === index ? { ...step, ...change } : step)));

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await createSequence(name, steps.map((step) => (step.channel === "TASK" ? { ...step, tone: undefined } : step)), makeDefault);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unexpected error.");
    } finally {
      setBusy(false);
    }
  };

  const input = "rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900";
  return (
    <Card className="space-y-4 p-5">
      <div>
        <h2 className="text-base font-semibold text-slate-900">New version</h2>
        <p className="text-sm text-slate-500">
          Saving creates a new version. Invoices already enrolled keep the version they started with.
        </p>
      </div>
      <label className="block text-sm font-medium text-slate-700" htmlFor={`${id}-name`}>
        Name
        <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} className={`mt-1 block w-full ${input}`} />
      </label>
      <div className="space-y-2">
        {steps.map((step, index) => (
          <fieldset key={index} className="flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 p-3">
            <legend className="px-1 text-xs font-semibold text-slate-600">Step {index + 1}</legend>
            <label className="text-xs text-slate-600">
              Days after due
              <input
                type="number"
                min={0}
                value={step.offset_days}
                onChange={(e) => update(index, { offset_days: Number(e.target.value) })}
                className={`mt-1 block w-24 ${input}`}
              />
            </label>
            <label className="text-xs text-slate-600">
              Channel
              <select
                value={step.channel}
                onChange={(e) => update(index, { channel: e.target.value as NewStep["channel"], tone: e.target.value === "EMAIL" ? "FRIENDLY" : undefined })}
                className={`mt-1 block ${input}`}
              >
                <option value="EMAIL">Email</option>
                <option value="TASK">Call task</option>
              </select>
            </label>
            {step.channel === "EMAIL" && (
              <label className="text-xs text-slate-600">
                Tone
                <select value={step.tone} onChange={(e) => update(index, { tone: e.target.value as Tone })} className={`mt-1 block ${input}`}>
                  {TONES.map((tone) => (
                    <option key={tone} value={tone}>
                      {tone.toLowerCase()}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="flex items-center gap-1.5 text-xs text-slate-600">
              <input type="checkbox" checked={step.requires_approver} onChange={(e) => update(index, { requires_approver: e.target.checked })} />
              Approver required
            </label>
            {steps.length > 2 && (
              <button type="button" onClick={() => setSteps((s) => s.filter((_, i) => i !== index))} className="ml-auto text-xs text-rose-600 hover:underline">
                Remove
              </button>
            )}
          </fieldset>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {steps.length < 5 && (
          <button
            type="button"
            onClick={() =>
              setSteps((s) => [...s, { offset_days: (s[s.length - 1]?.offset_days ?? 0) + 7, channel: "EMAIL", tone: "FIRM", requires_approver: true }])
            }
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          >
            Add step
          </button>
        )}
        <label className="flex items-center gap-1.5 text-sm text-slate-700">
          <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} />
          Use for new enrollments
        </label>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-60"
        >
          {busy && <Spinner />}
          Save version
        </button>
      </div>
      {error && <ErrorMessage message={error} />}
    </Card>
  );
}

export function SequencesView() {
  const user = useCurrentUser();
  const [state, setState] = useState<Loadable<Sequence[]>>({ kind: "loading" });
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getSequences(controller.signal)
      .then((data) => setState({ kind: "ready", data }))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setState({ kind: "error", message: err instanceof ApiError ? err.message : "Unexpected error." });
      });
    return () => controller.abort();
  }, [reload]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Collection sequences</h1>
        <p className="mt-1 text-sm text-slate-500">
          When each reminder is drafted, in which tone, and which steps need an approver. Drafts always wait for a person;
          nothing is sent automatically.
        </p>
      </div>
      {state.kind === "loading" && <Skeleton className="h-40 w-full rounded-xl" />}
      {state.kind === "error" && <ErrorMessage message={`Could not load sequences: ${state.message}`} />}
      {state.kind === "ready" &&
        state.data.map((sequence) => (
          <Card key={sequence.id} className="space-y-4 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-slate-900">
                {sequence.name} <span className="font-normal text-slate-500">v{sequence.version}</span>
              </h2>
              {sequence.is_default && (
                <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-200">
                  Default
                </span>
              )}
              <span className="ml-auto text-xs text-slate-500">
                {Object.entries(sequence.enrollment_counts.by_status)
                  .map(([status, count]) => `${count} ${status.toLowerCase()}`)
                  .join(" · ") || "No enrollments yet"}
              </span>
            </div>
            <Timeline sequence={sequence} />
          </Card>
        ))}
      {user.role === "ADMIN" ? (
        <NewVersionForm onCreated={() => setReload((n) => n + 1)} />
      ) : (
        <p className="text-sm text-slate-500">Only admins can create new sequence versions.</p>
      )}
    </div>
  );
}
