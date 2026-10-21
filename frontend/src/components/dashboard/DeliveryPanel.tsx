"use client";

import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api/client";
import {
  getDeliveryAttempts,
  getGmailStatus,
  rejectFollowUp,
  sendFollowUp,
  type DeliveryAttempt,
  type FollowUp,
  type GmailStatus,
} from "@/lib/api/followups";
import { isWorkerDown, WORKER_DOWN_MESSAGE } from "@/lib/api/jobs";
import { formatDateTime } from "@/lib/format";

import { FollowUpStatusBadge } from "./FollowUpBadges";
import type { Loadable } from "./types";
import { ErrorMessage, Spinner } from "./ui";

/** HTTP statuses the backend uses for "refused before anything was submitted". */
const DEFINITELY_NOT_SENT = new Set([400, 401, 403, 404, 409, 503]);

type SendError = { kind: "not_sent" | "uncertain"; message: string };

function Banner({ tone, title, children }: { tone: "teal" | "rose" | "amber" | "sky"; title: string; children?: ReactNode }) {
  const styles = {
    teal: "border-teal-200 bg-teal-50 text-teal-900",
    rose: "border-rose-200 bg-rose-50 text-rose-900",
    amber: "border-amber-300 bg-amber-50 text-amber-900",
    sky: "border-sky-200 bg-sky-50 text-sky-900",
  }[tone];
  return (
    <div role="status" className={`space-y-1.5 rounded-lg border px-4 py-3 text-sm ${styles}`}>
      <p className="font-semibold">{title}</p>
      {children}
    </div>
  );
}

function ReconcileHelp({ followUp, sender }: { followUp: FollowUp; sender: string | null }) {
  return (
    <>
      <p>
        Do <span className="font-semibold">not</span> send it again. Open the Sent folder of{" "}
        {sender ? <span className="font-medium">{sender}</span> : "the sender's Gmail account"} and look for this
        subject. Then record what you found:
      </p>
      <code className="block whitespace-pre-wrap break-all rounded bg-white/70 px-2 py-1.5 font-mono text-xs">
        {`python manage.py reconcile_delivery ${followUp.id} --sent    # found in Sent\npython manage.py reconcile_delivery ${followUp.id} --failed  # not in Sent`}
      </code>
    </>
  );
}

function StatusBanner({ followUp, attempts, sender }: { followUp: FollowUp; attempts: DeliveryAttempt[]; sender: string | null }) {
  const latest = attempts[0];
  switch (followUp.status) {
    case "SENT":
      return (
        <Banner tone="teal" title="Sent via Gmail">
          <p>
            Gmail accepted this email{followUp.sent_at ? ` on ${formatDateTime(followUp.sent_at)}` : ""} for{" "}
            <span className="font-medium">{followUp.recipient_email}</span>.
          </p>
          {latest?.gmail_message_id && (
            <p className="text-xs">
              Gmail message ID: <span className="font-mono">{latest.gmail_message_id}</span>
            </p>
          )}
        </Banner>
      );
    case "FAILED":
      return (
        <Banner tone="rose" title="Not sent">
          <p>Gmail did not accept this email, so it was not delivered.</p>
          {latest?.error_summary && <p className="text-xs">{latest.error_summary}</p>}
          <p className="text-xs">Fix the cause, then draft a new reminder for this invoice.</p>
        </Banner>
      );
    case "DELIVERY_UNKNOWN":
      return (
        <Banner tone="amber" title="Delivery outcome unknown">
          <p>We could not confirm whether Gmail accepted this email. It may have been sent.</p>
          {latest?.error_summary && <p className="text-xs">{latest.error_summary}</p>}
          <ReconcileHelp followUp={followUp} sender={sender} />
        </Banner>
      );
    case "SENDING":
      return (
        <Banner tone="sky" title="Sending in progress">
          <p>
            This reminder is reserved for delivery. If it stays in this state, the request was interrupted and the
            outcome is unknown.
          </p>
          <ReconcileHelp followUp={followUp} sender={sender} />
        </Banner>
      );
    default:
      return null;
  }
}

function AttemptHistory({ state }: { state: Loadable<DeliveryAttempt[]> }) {
  if (state.kind === "loading") return <p className="text-xs text-slate-400">Loading delivery attempts…</p>;
  if (state.kind === "error") return <p className="text-xs text-rose-600">Could not load delivery attempts.</p>;
  if (state.data.length === 0) return null;
  return (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold text-slate-900">Delivery attempts</h4>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {state.data.map((attempt) => (
          <li key={attempt.id} className="space-y-1 px-3 py-2.5 text-xs text-slate-600">
            <div className="flex flex-wrap items-center gap-2">
              <FollowUpStatusBadge status={attempt.status} />
              <span>Started {formatDateTime(attempt.started_at)}</span>
              {attempt.completed_at && <span>· finished {formatDateTime(attempt.completed_at)}</span>}
            </div>
            <p>
              To <span className="font-medium text-slate-700">{attempt.recipient_email}</span>
              {attempt.gmail_message_id && (
                <>
                  {" "}
                  · message <span className="font-mono">{attempt.gmail_message_id}</span>
                </>
              )}
            </p>
            {attempt.error_summary && <p className="text-slate-500">{attempt.error_summary}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ConfirmDialog({
  followUp,
  sender,
  onCancel,
  onConfirm,
}: {
  followUp: FollowUp;
  sender: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="alertdialog" aria-modal="true" aria-labelledby="confirm-send-title">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onCancel} aria-hidden="true" />
      <div className="relative w-full max-w-md space-y-4 rounded-xl bg-white p-6 shadow-2xl">
        <h3 id="confirm-send-title" className="text-base font-semibold text-slate-900">
          Send this email now?
        </h3>
        <dl className="space-y-1.5 text-sm">
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-500">From</dt>
            <dd className="font-medium text-slate-900">{sender}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-500">To</dt>
            <dd className="font-medium text-slate-900">{followUp.recipient_email}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-16 shrink-0 text-slate-500">Subject</dt>
            <dd className="text-slate-900">{followUp.subject}</dd>
          </div>
        </dl>
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
          A real email will be sent through Gmail. This cannot be undone.
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            autoFocus
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500"
          >
            Send email
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Delivery section for a non-draft follow-up. Parent re-mounts it (via `key`) whenever the
 * follow-up's status changes, which resets local state and reloads attempts.
 */
export function DeliveryPanel({
  followUp,
  onChanged,
  secondaryActions,
}: {
  followUp: FollowUp;
  onChanged: () => void;
  /** Extra buttons shown next to Send Email (e.g. "Leave unsent" in the automation workspace). */
  secondaryActions?: ReactNode;
}) {
  const canSend = followUp.status === "APPROVED";
  const [gmail, setGmail] = useState<Loadable<GmailStatus>>({ kind: "loading" });
  const [attempts, setAttempts] = useState<Loadable<DeliveryAttempt[]>>({ kind: "loading" });
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<SendError | null>(null);
  const [withdrawing, setWithdrawing] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const fail = (error: unknown) =>
      error instanceof ApiError ? error.message : "Unexpected error.";
    getDeliveryAttempts(followUp.id, controller.signal)
      .then((data) => setAttempts({ kind: "ready", data }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setAttempts({ kind: "error", message: fail(error) });
      });
    getGmailStatus(controller.signal)
      .then((data) => setGmail({ kind: "ready", data }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setGmail({ kind: "error", message: fail(error) });
      });
    return () => controller.abort();
  }, [followUp.id]);

  const sender = gmail.kind === "ready" ? gmail.data.sender_email || null : null;
  const gmailReady = gmail.kind === "ready" && gmail.data.ready;

  const send = async () => {
    setConfirming(false);
    setSending(true);
    setSendError(null);
    try {
      await sendFollowUp(followUp.id);
    } catch (error) {
      if (error instanceof ApiError && error.status !== null && DEFINITELY_NOT_SENT.has(error.status)) {
        setSendError({ kind: "not_sent", message: error.message });
      } else if (isWorkerDown(error)) {
        setSendError({
          kind: "uncertain",
          message: `${WORKER_DOWN_MESSAGE}. The reminder is reserved and will be sent once the worker runs; do not send it again.`,
        });
      } else {
        // Network failure or timeout: the request may still have reached Gmail. Never retry.
        setSendError({
          kind: "uncertain",
          message:
            "The response was lost, so the outcome is unknown. Do not send again; the status below is being refreshed.",
        });
      }
    } finally {
      setSending(false);
      onChanged();
    }
  };

  const withdraw = async () => {
    setWithdrawing(true);
    setSendError(null);
    try {
      await rejectFollowUp(followUp.id);
    } catch (error) {
      setSendError({ kind: "not_sent", message: error instanceof ApiError ? error.message : "Unexpected error." });
    } finally {
      setWithdrawing(false);
      onChanged();
    }
  };

  return (
    <div className="space-y-4">
      <StatusBanner followUp={followUp} attempts={attempts.kind === "ready" ? attempts.data : []} sender={sender} />

      {canSend && (
        <section className="space-y-3 rounded-lg border border-slate-200 p-4">
          <h4 className="text-sm font-semibold text-slate-900">Send via Gmail</h4>
          <dl className="space-y-1 text-sm">
            <div className="flex gap-2">
              <dt className="w-14 shrink-0 text-slate-500">From</dt>
              <dd className="font-medium text-slate-900">
                {gmail.kind === "loading" ? "Checking Gmail…" : sender || "Not configured"}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-14 shrink-0 text-slate-500">To</dt>
              <dd className="font-medium text-slate-900">{followUp.recipient_email}</dd>
            </div>
          </dl>

          {gmail.kind === "error" && <ErrorMessage message={`Could not check Gmail: ${gmail.message}`} />}
          {gmail.kind === "ready" && !gmail.data.ready && (
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
              <span className="font-medium">Gmail is not ready:</span> {gmail.data.detail}
            </p>
          )}

          {sendError?.kind === "not_sent" && <ErrorMessage message={`Not sent: ${sendError.message}`} />}
          {sendError?.kind === "uncertain" && (
            <Banner tone="amber" title="Outcome unknown">
              <p>{sendError.message}</p>
            </Banner>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={!gmailReady || sending || withdrawing || sendError?.kind === "uncertain" || followUp.invoice_changed_fields.length > 0}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sending && <Spinner />}
              {sending ? "Sending…" : "Send Email"}
            </button>
            {secondaryActions}
            <button
              type="button"
              onClick={() => void withdraw()}
              disabled={sending || withdrawing}
              title="Cancel the approval. The reminder is not sent and becomes Rejected."
              className="ml-auto inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {withdrawing && <Spinner />}
              Withdraw approval
            </button>
          </div>
        </section>
      )}

      <AttemptHistory state={attempts} />

      {confirming && sender && (
        <ConfirmDialog followUp={followUp} sender={sender} onCancel={() => setConfirming(false)} onConfirm={() => void send()} />
      )}
    </div>
  );
}
