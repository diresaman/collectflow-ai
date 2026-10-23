"use client";

import { useEffect, useId, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import type { Loadable } from "@/components/dashboard/types";
import {
  ErrorMessage,
  Icon,
  Notice,
  PageHeader,
  Section,
  Skeleton,
  Spinner,
  StatusPill,
  buttonStyles,
  inputStyles,
} from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { createSequence, getSequences, type NewStep, type Sequence, type Tone } from "@/lib/api/sequences";

const TONES: Tone[] = ["FRIENDLY", "PROFESSIONAL", "FIRM"];
const DEFAULT_DRAFT: NewStep[] = [
  { offset_days: 1, channel: "EMAIL", tone: "FRIENDLY", requires_approver: false },
  { offset_days: 8, channel: "EMAIL", tone: "PROFESSIONAL", requires_approver: false },
  { offset_days: 15, channel: "EMAIL", tone: "FIRM", requires_approver: true },
  { offset_days: 30, channel: "TASK", requires_approver: false },
];

const TONE_DOT: Record<string, string> = { FRIENDLY: "bg-success-500", PROFESSIONAL: "bg-info-500", FIRM: "bg-warning-500" };

/** The steps as a vertical, connected sequence: when, what, who approves, and how many invoices are there now. */
function Timeline({ sequence }: { sequence: Sequence }) {
  return (
    <ol className="relative">
      {sequence.steps.map((step, index) => {
        const at = sequence.enrollment_counts.by_step[String(step.order)] ?? 0;
        const last = index === sequence.steps.length - 1;
        return (
          <li key={step.order} className="relative flex gap-4 pb-6 last:pb-0">
            {!last && <span className="absolute top-9 bottom-0 left-[17px] w-px bg-line" aria-hidden="true" />}
            <span
              className="relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-white text-sm font-semibold text-ink"
              aria-hidden="true"
            >
              {step.order}
            </span>
            <div className="min-w-0 flex-1 rounded-xl border border-line p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-ink">
                    Day {step.offset_days} after the due date ·{" "}
                    {step.channel === "TASK" ? "Call task" : `${step.tone.charAt(0)}${step.tone.slice(1).toLowerCase()} reminder`}
                  </p>
                  <p className="mt-0.5 flex items-center gap-2 text-sm text-ink-muted">
                    {step.channel === "TASK" ? (
                      <>
                        <Icon name="user" className="h-3.5 w-3.5" /> An operator calls the customer; nothing is emailed.
                      </>
                    ) : (
                      <>
                        <span className={`h-2 w-2 rounded-full ${TONE_DOT[step.tone] ?? "bg-slate-400"}`} aria-hidden="true" />
                        Email drafted by AI in a {step.tone.toLowerCase()} tone, then reviewed by a person.
                      </>
                    )}
                  </p>
                </div>
                <StatusPill
                  label={step.channel === "TASK" ? "No email to approve" : step.requires_approver ? "Approver required" : "Any operator can approve"}
                  tone={step.requires_approver ? "brand" : "neutral"}
                />
              </div>
              <p className="mt-2 text-xs text-ink-subtle">
                {at} invoice{at === 1 ? "" : "s"} at this step now
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function NewVersionForm({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
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

  const input = inputStyles.input;
  const select = inputStyles.select;
  return (
    <Section
      title="New version"
      description="Saving creates a new, separate version; existing versions never change. Invoices already enrolled keep the version they started with."
    >
      <div className="space-y-4">
      <label className="block max-w-md text-sm font-medium text-ink" htmlFor={`${id}-name`}>
        Name
        <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} className={`mt-1.5 ${input}`} />
      </label>
      <div className="space-y-2">
        {steps.map((step, index) => (
          <fieldset key={index} className="flex flex-wrap items-end gap-3 rounded-xl border border-line p-4">
            <legend className="px-1 text-sm font-semibold text-ink">Step {index + 1}</legend>
            <label className="text-sm font-medium text-ink">
              Days after due
              <input
                type="number"
                min={0}
                value={step.offset_days}
                onChange={(e) => update(index, { offset_days: Number(e.target.value) })}
                className={`mt-1.5 !w-28 ${input}`}
              />
            </label>
            <label className="text-sm font-medium text-ink">
              Channel
              <select
                value={step.channel}
                onChange={(e) => update(index, { channel: e.target.value as NewStep["channel"], tone: e.target.value === "EMAIL" ? "FRIENDLY" : undefined })}
                className={`mt-1.5 ${select}`}
              >
                <option value="EMAIL">Email</option>
                <option value="TASK">Call task</option>
              </select>
            </label>
            {step.channel === "EMAIL" && (
              <label className="text-sm font-medium text-ink">
                Tone
                <select value={step.tone} onChange={(e) => update(index, { tone: e.target.value as Tone })} className={`mt-1.5 ${select}`}>
                  {TONES.map((tone) => (
                    <option key={tone} value={tone}>
                      {tone.toLowerCase()}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="flex h-10 items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={step.requires_approver} onChange={(e) => update(index, { requires_approver: e.target.checked })} />
              Approver required
            </label>
            {steps.length > 2 && (
              <button type="button" onClick={() => setSteps((s) => s.filter((_, i) => i !== index))} className={`ml-auto ${buttonStyles.ghost} text-danger-700`}>
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
            className={buttonStyles.secondary}
          >
            Add step
          </button>
        )}
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} />
          Use for new enrollments
        </label>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy}
          className={`ml-auto ${buttonStyles.primary}`}
        >
          {busy && <Spinner />}
          Save version
        </button>
        <button type="button" onClick={onCancel} className={buttonStyles.ghost}>
          Cancel
        </button>
      </div>
      {error && <ErrorMessage message={error} />}
      </div>
    </Section>
  );
}

export function SequencesView() {
  const user = useCurrentUser();
  const [state, setState] = useState<Loadable<Sequence[]>>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [editing, setEditing] = useState(false);

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
      <PageHeader
        title="Reminder Schedule"
        description="Manage the timing and tone of payment follow-ups. Drafts always wait for a person; nothing is sent automatically."
        actions={
          user.role === "ADMIN" && !editing ? (
            <button type="button" onClick={() => setEditing(true)} className={buttonStyles.primary}>
              Create new version
            </button>
          ) : undefined
        }
      />
      {state.kind === "loading" && <Skeleton className="h-64 w-full rounded-xl" />}
      {state.kind === "error" && <ErrorMessage message={`Could not load the reminder schedules: ${state.message}`} />}
      {editing && (
        <NewVersionForm
          onCreated={() => {
            setEditing(false);
            setReload((n) => n + 1);
          }}
          onCancel={() => setEditing(false)}
        />
      )}
      {state.kind === "ready" &&
        state.data.map((sequence) => (
          <Section
            key={sequence.id}
            title={
              <span className="inline-flex flex-wrap items-center gap-2">
                {sequence.name} <span className="font-normal text-ink-subtle">version {sequence.version}</span>
                {sequence.is_default && <StatusPill label="Used for new invoices" tone="brand" />}
              </span>
            }
            description={
              Object.entries(sequence.enrollment_counts.by_status)
                .map(([status, count]) => `${count} ${status.toLowerCase()}`)
                .join(" · ") || "No invoices on this schedule yet"
            }
          >
            <Timeline sequence={sequence} />
          </Section>
        ))}
      {user.role !== "ADMIN" && (
        <Notice tone="neutral">Viewing only. Only admins can create new schedule versions; saved versions never change.</Notice>
      )}
    </div>
  );
}
