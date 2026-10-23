"use client";

import { useCallback, useEffect, useState } from "react";

import { runAutomation } from "@/lib/api/automation";
import { ApiError } from "@/lib/api/client";
import { analyzeInvoice } from "@/lib/api/followups";
import { isWorkerDown, WORKER_DOWN_MESSAGE } from "@/lib/api/jobs";
import { finalizeWorkflow, getCurrentWorkflow, leaveUnsent, type Workflow, type WorkflowState } from "@/lib/api/workflows";
import { formatDateTime } from "@/lib/format";

import type { Loadable } from "../dashboard/types";
import { Card, ErrorMessage, Icon, Skeleton, Spinner } from "../dashboard/ui";
import { AttentionList, DeliveryItem, ReviewItem, Section, SendItem, SummaryGrid } from "./Sections";
import { StepTracker } from "./StepTracker";

const POLL_INTERVAL_MS = 1500;

const STATE_META: Record<WorkflowState, { label: string; style: string }> = {
  PROCESSING: { label: "Processing", style: "bg-sky-50 text-sky-700 ring-sky-600/20" },
  WAITING_FOR_APPROVAL: { label: "Waiting for your approval", style: "bg-indigo-50 text-indigo-700 ring-indigo-600/20" },
  WAITING_FOR_SEND_CONFIRMATION: { label: "Waiting for send confirmation", style: "bg-indigo-50 text-indigo-700 ring-indigo-600/20" },
  SENDING: { label: "Sending", style: "bg-sky-50 text-sky-700 ring-sky-600/20" },
  READY_TO_FINALIZE: { label: "Ready to finalize", style: "bg-emerald-50 text-emerald-700 ring-emerald-600/20" },
  FINALIZED: { label: "Finalized", style: "bg-slate-100 text-slate-600 ring-slate-500/20" },
};

const message = (error: unknown) => (error instanceof ApiError ? error.message : "Unexpected error.");

/**
 * Guided automation workflow. All state comes from the backend (`/automation/workflows/current/`),
 * so a refresh resumes exactly where the operator left off; this component never decides progress.
 */
export function AutomationWorkspace() {
  const [workflow, setWorkflow] = useState<Loadable<Workflow | null>>({ kind: "loading" });
  const [refreshKey, setRefreshKey] = useState(0);
  const [starting, setStarting] = useState(false);
  const [connectionIssue, setConnectionIssue] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [finalizing, setFinalizing] = useState(false);

  const data = workflow.kind === "ready" ? workflow.data : null;
  const active = starting || data?.state === "PROCESSING" || data?.state === "SENDING";
  const refresh = useCallback(() => setRefreshKey((key) => key + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      getCurrentWorkflow(controller.signal)
        .then((current) => {
          setWorkflow({ kind: "ready", data: current });
          setConnectionIssue(false);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          // Keep showing the last known state on transient failures.
          setWorkflow((previous) => (previous.kind === "ready" ? previous : { kind: "error", message: message(err) }));
          setConnectionIssue(true);
        });
    void load();
    const timer = active ? window.setInterval(() => void load(), POLL_INTERVAL_MS) : undefined;
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [active, refreshKey]);

  const start = async () => {
    setStarting(true);
    setNotice(null);
    setError(null);
    try {
      await runAutomation();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setNotice("An automation run is already in progress; showing it below.");
      } else if (isWorkerDown(err)) {
        setNotice(WORKER_DOWN_MESSAGE);
      } else if (err instanceof ApiError && err.status !== null && err.status < 500) {
        setError(err.message);
      } else {
        setNotice("Lost contact while the run was processing. It may still be running; its progress is shown below.");
      }
    } finally {
      setStarting(false);
      refresh();
    }
  };

  const withItem = async (key: string, action: () => Promise<unknown>) => {
    setBusyItem(key);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusyItem(null);
      refresh();
    }
  };

  const finalize = async (id: number) => {
    setFinalizing(true);
    setError(null);
    try {
      setWorkflow({ kind: "ready", data: await finalizeWorkflow(id) });
    } catch (err) {
      setError(message(err));
      refresh();
    } finally {
      setFinalizing(false);
    }
  };

  const open = data !== null && data.state !== "FINALIZED";
  const actions = data?.pending_actions;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Automation</h1>
          <p className="mt-1 text-sm text-slate-500">
            Sync, draft with AI, review, and confirm each Gmail send in one place. Nothing is emailed without your confirmation.
          </p>
        </div>
        <div className="shrink-0">
          <button
            type="button"
            onClick={() => void start()}
            disabled={active || open}
            title={open ? "Finish or finalize the current workflow first." : undefined}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
          >
            {active ? <Spinner /> : <Icon name="sparkles" className="h-4 w-4" />}
            {active ? "Running…" : "Run Automation"}
          </button>
        </div>
      </div>

      {connectionIssue && workflow.kind === "ready" && (
        <p className="rounded-lg bg-amber-50 px-4 py-2 text-sm text-amber-800">Connection problem; retrying. Showing the last known state.</p>
      )}
      {notice && <p className="rounded-lg bg-sky-50 px-4 py-3 text-sm text-sky-800">{notice}</p>}
      {error && <ErrorMessage message={error} />}

      {workflow.kind === "loading" && <Skeleton className="h-64 w-full rounded-xl" />}
      {workflow.kind === "error" && <ErrorMessage message={`Could not load the workflow: ${workflow.message}`} />}
      {workflow.kind === "ready" && data === null && (
        <Card className="px-6 py-14 text-center">
          <p className="text-sm font-semibold text-slate-900">No automation workflow yet</p>
          <p className="mt-1 text-sm text-slate-500">Click Run Automation to sync invoices and draft reminders for overdue ones.</p>
        </Card>
      )}

      {data && actions && (
        <>
          <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 text-sm">
            <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${STATE_META[data.state].style}`}>
              {STATE_META[data.state].label}
            </span>
            <span className="text-slate-600">
              Workflow #{data.id} · {data.run.trigger === "MANUAL" ? "manual" : "scheduled"} run · started{" "}
              {formatDateTime(data.run.started_at)}
            </span>
            {data.run.finalized_at && <span className="text-slate-500">finalized {formatDateTime(data.run.finalized_at)}</span>}
            {data.run.error_summary && <span className="w-full text-rose-700">{data.run.error_summary}</span>}
          </Card>

          <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
            <div className="lg:sticky lg:top-6 lg:self-start">
              <StepTracker steps={data.steps} currentStep={data.current_step} />
            </div>

            <div className="min-w-0 space-y-6">
              {data.state === "PROCESSING" && (
                <Card className="flex items-center gap-3 px-5 py-4 text-sm text-sky-800">
                  <Spinner className="h-5 w-5" />
                  {data.steps.find((step) => step.key === data.current_step)?.label ?? "Processing"}…{" "}
                  {data.steps.find((step) => step.key === data.current_step)?.detail}
                </Card>
              )}

              {actions.approvals.length > 0 && (
                <Section
                  title={`Review drafts (${actions.approvals.length})`}
                  subtitle="Drafts generated — awaiting approval. Edit, approve or reject each one. Approving does not send anything."
                  tone={data.state === "WAITING_FOR_APPROVAL" ? "highlight" : "default"}
                >
                  {actions.approvals.map((entry) => (
                    <ReviewItem
                      key={entry.follow_up.id}
                      entry={entry}
                      regenerating={busyItem === `regen:${entry.follow_up.id}`}
                      onChanged={refresh}
                      onRegenerate={() =>
                        void withItem(`regen:${entry.follow_up.id}`, () => analyzeInvoice(entry.follow_up.invoice_id, true))
                      }
                    />
                  ))}
                </Section>
              )}

              {actions.send_confirmations.length > 0 && (
                <Section
                  title={`Confirm Gmail sends (${actions.send_confirmations.length})`}
                  subtitle="Approved reminders. Each one is sent only when you confirm it; you can also leave it unsent."
                  tone={data.state === "WAITING_FOR_SEND_CONFIRMATION" ? "highlight" : "default"}
                >
                  {actions.send_confirmations.map((entry) => (
                    <SendItem
                      key={entry.follow_up.id}
                      entry={entry}
                      leaving={busyItem === `leave:${entry.item_id}`}
                      onChanged={refresh}
                      onLeaveUnsent={() => void withItem(`leave:${entry.item_id}`, () => leaveUnsent(data.id, entry.item_id))}
                    />
                  ))}
                </Section>
              )}

              {actions.deliveries.length > 0 && (
                <Section title="Delivery results" subtitle="Outcomes of confirmed sends. Unknown outcomes are never retried automatically.">
                  {actions.deliveries.map((entry) => (
                    <DeliveryItem key={entry.follow_up.id} entry={entry} onChanged={refresh} />
                  ))}
                </Section>
              )}

              {actions.attention.length > 0 && (
                <Section title="Needs attention" subtitle="Existing reminders the automation found but did not change.">
                  <AttentionList items={actions.attention} />
                </Section>
              )}

              <Card className="space-y-4 p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="text-base font-semibold text-slate-900">
                    {data.state === "FINALIZED" ? "Final summary" : "Summary so far"}
                  </h2>
                  {data.can_finalize && (
                    <button
                      type="button"
                      onClick={() => void finalize(data.id)}
                      disabled={finalizing}
                      className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-60"
                    >
                      {finalizing && <Spinner />}
                      {data.summary.awaiting_send_confirmation > 0
                        ? `Finish — leave ${data.summary.awaiting_send_confirmation} approved unsent`
                        : "Finish workflow"}
                    </button>
                  )}
                </div>
                {data.can_finalize && data.summary.drafts_pending_review > 0 && (
                  <p className="text-xs text-slate-500">
                    Finishing now keeps {data.summary.drafts_pending_review} unreviewed draft(s) in the dashboard approval queue.
                  </p>
                )}
                <SummaryGrid summary={data.summary} />
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
