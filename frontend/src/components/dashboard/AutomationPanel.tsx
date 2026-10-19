"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  getLatestAutomationRun,
  runAutomation,
  type AutomationItemOutcome,
  type AutomationRunDetail,
  type AutomationRunStatus,
} from "@/lib/api/automation";
import { formatDateTime } from "@/lib/format";

import type { Loadable } from "./types";
import { Card, ErrorMessage, Icon, Skeleton, Spinner } from "./ui";

const POLL_INTERVAL_MS = 3000;

const RUN_STATUS: Record<AutomationRunStatus, { label: string; style: string }> = {
  RUNNING: { label: "Running", style: "bg-sky-50 text-sky-700 ring-sky-600/20" },
  COMPLETED: { label: "Completed", style: "bg-emerald-50 text-emerald-700 ring-emerald-600/20" },
  PARTIAL: { label: "Partially completed", style: "bg-amber-50 text-amber-800 ring-amber-600/30" },
  FAILED: { label: "Failed", style: "bg-rose-50 text-rose-700 ring-rose-600/20" },
};

const OUTCOME_STYLE: Record<AutomationItemOutcome, { label: string; style: string }> = {
  GENERATED: { label: "Draft generated", style: "bg-indigo-50 text-indigo-700" },
  SKIPPED: { label: "Skipped", style: "bg-slate-100 text-slate-600" },
  FAILED: { label: "Failed", style: "bg-rose-50 text-rose-700" },
};

/** Human labels for the backend's machine-readable reason codes. */
const REASONS: Record<string, string> = {
  not_in_sheet: "No longer in Google Sheet",
  not_unpaid: "Paid",
  not_overdue: "Not overdue yet",
  invalid_recipient: "Invalid email",
  invalid_amount: "Invalid amount",
  draft_awaiting_review: "Draft awaiting review",
  approved_awaiting_send: "Approved, not sent yet",
  stale_draft: "Invoice changed — review draft",
  stale_approved: "Invoice changed — review approval",
  delivery_unresolved: "Delivery unresolved",
  delivery_failed: "Last delivery failed",
  cooldown: "Cooldown after recent reminder",
  recently_rejected: "Recently rejected",
  run_limit_reached: "Run limit reached",
  active_follow_up_exists: "Reminder already exists",
  not_eligible: "Not eligible",
  ai_unavailable: "Gemini unavailable",
  ai_bad_response: "Invalid AI response",
  stale_invoice: "Invoice changed during generation",
  unexpected_error: "Unexpected error",
};

function Count({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`text-lg font-semibold tabular-nums ${tone ?? "text-slate-900"}`}>{value}</dd>
    </div>
  );
}

function RunSummary({ run, onOpenInvoice }: { run: AutomationRunDetail; onOpenInvoice: (invoiceId: string) => void }) {
  const status = RUN_STATUS[run.status];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${status.style}`}>
          {status.label}
        </span>
        <span className="text-slate-500">
          {run.trigger === "MANUAL" ? "Manual" : "Scheduled"} run · {formatDateTime(run.completed_at ?? run.started_at)}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Count label="Evaluated" value={run.invoices_evaluated} />
        <Count label="Drafts generated" value={run.drafts_generated} tone="text-indigo-700" />
        <Count label="Skipped" value={run.invoices_skipped} />
        <Count label="Failed" value={run.generation_failures} tone={run.generation_failures ? "text-rose-600" : undefined} />
      </dl>

      {run.drafts_generated > 0 && (
        <p className="flex items-center gap-2 rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-900">
          <Icon name="sparkles" className="h-4 w-4 shrink-0" />
          <span>
            <span className="font-semibold">Drafts generated — awaiting approval.</span> No email has been sent.
          </span>
        </p>
      )}
      {run.error_summary && run.status !== "COMPLETED" && (
        <p className="rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">{run.error_summary}</p>
      )}

      {run.items.length > 0 && (
        <details className="group rounded-lg border border-slate-200">
          <summary className="cursor-pointer list-none px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50">
            Per-invoice results ({run.items.length})
          </summary>
          <ul className="divide-y divide-slate-100 border-t border-slate-200">
            {run.items.map((item) => {
              const outcome = OUTCOME_STYLE[item.outcome];
              const clickable = item.invoice_id !== null && item.outcome !== "SKIPPED";
              const content = (
                <>
                  <span className="w-24 shrink-0 font-mono text-xs text-slate-600">{item.invoice_external_id}</span>
                  <span className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-medium ${outcome.style}`}>{outcome.label}</span>
                  {item.reason_code && (
                    <span className="text-xs text-slate-600">{REASONS[item.reason_code] ?? item.reason_code}</span>
                  )}
                  {item.outcome !== "GENERATED" && item.detail && (
                    <span className="w-full text-xs text-slate-400 sm:w-auto sm:flex-1">{item.detail}</span>
                  )}
                </>
              );
              return (
                <li key={item.id}>
                  {clickable || item.reason_code.startsWith("stale_") ? (
                    <button
                      type="button"
                      onClick={() => item.invoice_id && onOpenInvoice(item.invoice_id)}
                      className="flex w-full flex-wrap items-center gap-2 px-4 py-2 text-left hover:bg-slate-50"
                    >
                      {content}
                    </button>
                  ) : (
                    <div className="flex flex-wrap items-center gap-2 px-4 py-2">{content}</div>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </div>
  );
}

/**
 * Runs the proactive automation (sync + Gemini drafts). The backend lease is the source of
 * truth for "one run at a time"; the button is only disabled for convenience.
 */
export function AutomationPanel({
  onRunFinished,
  onOpenInvoice,
}: {
  onRunFinished: () => void;
  onOpenInvoice: (invoiceId: string) => void;
}) {
  const [latest, setLatest] = useState<Loadable<AutomationRunDetail | null>>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remoteRunning = latest.kind === "ready" && latest.data?.status === "RUNNING";
  const polling = submitting || remoteRunning;

  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      getLatestAutomationRun(controller.signal)
        .then((data) => setLatest({ kind: "ready", data }))
        .catch((err: unknown) => {
          if (!controller.signal.aborted) {
            setLatest({ kind: "error", message: err instanceof ApiError ? err.message : "Unexpected error." });
          }
        });
    void load();
    // While a run is active (ours or a scheduled one), refresh to show live progress.
    const timer = polling ? window.setInterval(() => void load(), POLL_INTERVAL_MS) : undefined;
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [polling]);

  const start = async () => {
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const run = await runAutomation();
      setLatest({ kind: "ready", data: run });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setNotice("Another automation run is already in progress. Its progress is shown below.");
      } else if (err instanceof ApiError && err.status !== null && err.status < 500) {
        setError(err.message);
      } else {
        setNotice("Lost contact with the server; the run may still be in progress. Its status is refreshed below.");
      }
    } finally {
      setSubmitting(false);
      onRunFinished();
    }
  };

  const busy = submitting || remoteRunning;

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">Proactive automation</h2>
          <p className="mt-0.5 text-sm text-slate-500">
            Syncs the sheet and drafts AI reminders for eligible overdue invoices. It never sends email.
          </p>
        </div>
        <div className="shrink-0">
          <button
            type="button"
            onClick={() => void start()}
            disabled={busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
          >
            {busy ? <Spinner /> : <Icon name="sparkles" className="h-4 w-4" />}
            {busy ? "Running…" : "Run Automation"}
          </button>
        </div>
      </div>

      <div className="space-y-4 p-5">
        {notice && <p className="rounded-lg bg-sky-50 px-4 py-3 text-sm text-sky-800">{notice}</p>}
        {error && <ErrorMessage message={error} />}
        {latest.kind === "loading" && <Skeleton className="h-24 w-full rounded-lg" />}
        {latest.kind === "error" && <ErrorMessage message={`Could not load automation runs: ${latest.message}`} />}
        {latest.kind === "ready" && latest.data === null && !busy && (
          <p className="text-sm text-slate-500">
            No automation run yet. Click <span className="font-medium text-slate-700">Run Automation</span>, or schedule{" "}
            <code className="font-mono text-xs">manage.py run_automation</code> with cron.
          </p>
        )}
        {latest.kind === "ready" && latest.data && <RunSummary run={latest.data} onOpenInvoice={onOpenInvoice} />}
      </div>
    </Card>
  );
}
