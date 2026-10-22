"use client";

import { useEffect, useId, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import { ApiError } from "@/lib/api/client";
import {
  getEnrollment,
  pauseEnrollment,
  resumeEnrollment,
  reviewEnrollment,
  type Enrollment,
  type StepState,
} from "@/lib/api/sequences";
import { formatDateTime } from "@/lib/format";

import { ErrorMessage, Spinner } from "./ui";

/** Why a sequence is not moving, in operator terms. */
export const BLOCKED_REASONS: Record<string, string> = {
  delivery_unknown: "The last reminder's delivery outcome is unknown. Reconcile it (reconcile_delivery); nothing is resent automatically.",
  delivery_in_progress: "A reminder is being sent.",
  delivery_failed: "The last reminder was not delivered. Draft the step again to retry.",
};

const STATE_STYLES: Record<StepState["state"], string> = {
  done: "border-emerald-200 bg-emerald-50 text-emerald-800",
  pending: "border-indigo-300 bg-indigo-50 text-indigo-800 ring-2 ring-indigo-200",
  upcoming: "border-slate-200 bg-white text-slate-500",
};
const STATE_LABELS: Record<StepState["state"], string> = { done: "Done", pending: "In progress", upcoming: "Upcoming" };

function stepLabel(step: StepState): string {
  return step.channel === "TASK" ? "Call task" : `${step.tone.charAt(0)}${step.tone.slice(1).toLowerCase()} email`;
}

export function SequencePanel({ invoiceId, refreshKey }: { invoiceId: string; refreshKey: number }) {
  const user = useCurrentUser();
  const fieldId = useId();
  const [enrollment, setEnrollment] = useState<Enrollment | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pauseReason, setPauseReason] = useState("");
  const [position, setPosition] = useState<number | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    getEnrollment(invoiceId, controller.signal)
      .then((data) => {
        setEnrollment(data);
        setPosition(data ? data.last_completed_step : null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof ApiError ? err.message : "Unexpected error.");
      });
    return () => controller.abort();
  }, [invoiceId, refreshKey]);

  if (enrollment === undefined) return error ? <ErrorMessage message={`Could not load the sequence: ${error}`} /> : null;
  if (enrollment === null) {
    return (
      <p className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-600">
        Not in a collection sequence yet. Overdue unpaid invoices are enrolled automatically after the next sync.
      </p>
    );
  }

  const act = async (action: () => Promise<Enrollment>) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await action();
      setEnrollment(updated);
      setPosition(updated.last_completed_step);
      setPauseReason("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unexpected error.");
    } finally {
      setBusy(false);
    }
  };

  const live = enrollment.status === "ACTIVE" || enrollment.status === "PAUSED";
  const inReview = enrollment.status === "PAUSED" && enrollment.pause_reason === "history_review";
  const canReview = user.role === "APPROVER" || user.role === "ADMIN";

  return (
    <section className="space-y-3 rounded-lg border border-slate-200 p-4" aria-labelledby={`${fieldId}-title`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`${fieldId}-title`} className="text-sm font-semibold text-slate-900">
          {enrollment.sequence.name} <span className="font-normal text-slate-500">v{enrollment.sequence.version}</span>
        </h3>
        <span className="text-xs font-medium text-slate-600">
          {enrollment.status === "ACTIVE" && "Active"}
          {enrollment.status === "PAUSED" && (inReview ? "Paused for review" : "Paused")}
          {enrollment.status === "COMPLETED" && "Completed"}
          {enrollment.status === "EXITED" && `Ended: ${enrollment.exit_reason.replace(/_/g, " ")}`}
        </span>
      </div>

      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {enrollment.steps.map((step) => (
          <li key={step.order} className={`rounded-lg border px-3 py-2 text-xs ${STATE_STYLES[step.state]}`}>
            <p className="font-semibold">
              Step {step.order} · day {step.offset_days}
            </p>
            <p>{stepLabel(step)}</p>
            <p className="mt-0.5 opacity-80">
              {STATE_LABELS[step.state]}
              {step.requires_approver ? " · approver" : ""}
            </p>
          </li>
        ))}
      </ol>

      {enrollment.status === "ACTIVE" && enrollment.pending_step === null && enrollment.next_action_at && (
        <p className="text-sm text-slate-600">
          Step {enrollment.next_step} is due {formatDateTime(enrollment.next_action_at)}. The draft is prepared then
          and waits for your approval.
        </p>
      )}
      {enrollment.blocked_reason && (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <span className="font-semibold">Held:</span> {BLOCKED_REASONS[enrollment.blocked_reason] ?? enrollment.blocked_reason}
        </p>
      )}
      {enrollment.status === "PAUSED" && !inReview && (
        <p className="text-sm text-slate-600">
          <span className="font-medium">Paused:</span> {enrollment.pause_reason}. No step is drafted until it is resumed.
        </p>
      )}

      {enrollment.review_required && (
        <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <p>
            <span className="font-semibold">History review{inReview ? " required" : " suggested"}:</span> {enrollment.review_reason}
          </p>
          {canReview ? (
            <div className="flex flex-wrap items-end gap-2">
              <label className="text-xs font-medium">
                Steps already done
                <select
                  value={position ?? 0}
                  onChange={(event) => setPosition(Number(event.target.value))}
                  disabled={busy}
                  className="mt-1 block rounded-md border border-amber-300 bg-white px-2 py-1 text-sm text-slate-900"
                >
                  {[0, ...enrollment.steps.map((step) => step.order)].map((order) => (
                    <option key={order} value={order}>
                      {order === 0 ? "None (start at step 1)" : `Through step ${order}`}
                    </option>
                  ))}
                </select>
              </label>
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Note (optional)"
                aria-label="Review note"
                disabled={busy}
                className="min-w-0 flex-1 rounded-md border border-amber-300 bg-white px-2 py-1 text-sm text-slate-900"
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => void act(() => reviewEnrollment(enrollment.id, position ?? 0, note))}
                className="inline-flex items-center gap-2 rounded-md bg-amber-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-amber-500 disabled:opacity-60"
              >
                {busy && <Spinner />}
                Confirm position
              </button>
            </div>
          ) : (
            <p className="text-xs">An approver must confirm where this invoice stands in the sequence.</p>
          )}
        </div>
      )}

      {live && !inReview && (
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          {enrollment.status === "ACTIVE" ? (
            <>
              <input
                value={pauseReason}
                onChange={(event) => setPauseReason(event.target.value)}
                placeholder="Reason for pausing"
                aria-label="Reason for pausing"
                disabled={busy}
                className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
              />
              <button
                type="button"
                disabled={busy || !pauseReason.trim()}
                title={!pauseReason.trim() ? "Enter a reason first" : undefined}
                onClick={() => void act(() => pauseEnrollment(enrollment.id, pauseReason))}
                className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              >
                Pause sequence
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => void act(() => resumeEnrollment(enrollment.id))}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-60"
            >
              Resume sequence
            </button>
          )}
        </div>
      )}
      {error && <ErrorMessage message={error} />}
    </section>
  );
}
