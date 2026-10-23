"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { ACTIVE_STATUSES, analyzeInvoice, getFollowUps, type FollowUp } from "@/lib/api/followups";
import {
  getAutomationRuns,
  getDashboard,
  getInvoices,
  syncInvoices,
  type AutomationRun,
  type DashboardSummary,
  type Invoice,
} from "@/lib/api/invoices";

import { ApprovalQueue } from "./ApprovalQueue";
import { AutomationPanel } from "./AutomationPanel";
import { queryParam, setQueryParam } from "@/lib/url";

import { FollowUpDrawer, type GenerationState } from "./FollowUpDrawer";
import { FollowUpHistory } from "./FollowUpHistory";
import { InvoiceTable, type FollowUpActions } from "./InvoiceTable";
import { SummaryCards } from "./SummaryCards";
import { TasksPanel } from "./TasksPanel";
import { SyncButton, SyncStatus } from "./SyncStatus";
import type { Loadable } from "./types";

function describeError(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unexpected error.";
}

function track<T>(promise: Promise<T>, setState: (state: Loadable<T>) => void, signal: AbortSignal): void {
  promise
    .then((data) => setState({ kind: "ready", data }))
    .catch((error: unknown) => {
      if (!signal.aborted) setState({ kind: "error", message: describeError(error) });
    });
}

export function Dashboard() {
  const [summary, setSummary] = useState<Loadable<DashboardSummary>>({ kind: "loading" });
  const [invoices, setInvoices] = useState<Loadable<Invoice[]>>({ kind: "loading" });
  const [lastRun, setLastRun] = useState<Loadable<AutomationRun | null>>({ kind: "loading" });
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  // Incrementing this reloads all dashboard data; previous data stays visible meanwhile.
  const [reloadKey, setReloadKey] = useState(0);

  const [followUps, setFollowUps] = useState<Loadable<FollowUp[]>>({ kind: "loading" });
  const [followUpsKey, setFollowUpsKey] = useState(0);
  // Other screens link here with ?invoice=<id> to open that invoice's drawer.
  const [openInvoiceId, setOpenInvoiceId] = useState<string | null>(() => queryParam("invoice"));
  const [generation, setGeneration] = useState<(GenerationState & { invoiceId: string }) | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    track(getFollowUps(controller.signal), setFollowUps, controller.signal);
    return () => controller.abort();
  }, [followUpsKey, reloadKey]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    track(getDashboard(signal), setSummary, signal);
    track(getInvoices(signal), setInvoices, signal);
    track(
      getAutomationRuns(signal).then((runs) => runs[0] ?? null),
      setLastRun,
      signal,
    );
    return () => controller.abort();
  }, [reloadKey]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncError(null);
    try {
      const run = await syncInvoices();
      setLastRun({ kind: "ready", data: run });
      setReloadKey((key) => key + 1);
    } catch (error) {
      setSyncError(describeError(error));
    } finally {
      setSyncing(false);
    }
  };

  const expectedColumns = summary.kind === "ready" ? summary.data.expected_columns : [];

  const allFollowUps = followUps.kind === "ready" ? followUps.data : [];
  // An invoice's reminders, plus any consolidated statement that lists it (M14).
  const followUpsForInvoice = (invoiceId: string) =>
    allFollowUps.filter(
      (followUp) =>
        followUp.invoice_id === invoiceId || followUp.statement?.lines.some((line) => line.invoice_id === invoiceId),
    );

  const handleAnalyze = async (invoiceId: string, regenerate: boolean) => {
    setOpenInvoiceId(invoiceId);
    setGeneration({ invoiceId, running: true, regenerate, error: null });
    try {
      await analyzeInvoice(invoiceId, regenerate);
      setGeneration(null);
    } catch (error) {
      setGeneration({ invoiceId, running: false, regenerate, error: describeError(error) });
    } finally {
      // Regeneration also retires the previous draft, so reload the full list. Drafting also moves the
      // invoice's sequence, so reload invoices (with their enrollment) too.
      setFollowUpsKey((key) => key + 1);
      setReloadKey((key) => key + 1);
    }
  };

  const handleFollowUpChanged = () => {
    setFollowUpsKey((key) => key + 1);
    setReloadKey((key) => key + 1);
  };

  const closeDrawer = useCallback(() => {
    setOpenInvoiceId(null);
    setQueryParam("invoice", null);
    setGeneration((current) => (current?.running ? current : null));
  }, []);

  const followUpActions: FollowUpActions = {
    followUpFor: (invoiceId) => {
      const forInvoice = followUpsForInvoice(invoiceId);
      return {
        active: forInvoice.find((followUp) => ACTIVE_STATUSES.includes(followUp.status)) ?? null,
        latest: forInvoice[0] ?? null,
        total: forInvoice.length,
      };
    },
    analyzingInvoiceId: generation?.running ? generation.invoiceId : null,
    onAnalyze: (invoiceId) => void handleAnalyze(invoiceId, false),
    onOpen: (invoiceId) => {
      setOpenInvoiceId(invoiceId);
      setQueryParam("invoice", invoiceId);
      setGeneration((current) => (current && current.invoiceId !== invoiceId && !current.running ? null : current));
    },
  };

  const openInvoice =
    openInvoiceId && invoices.kind === "ready"
      ? (invoices.data.find((invoice) => invoice.id === openInvoiceId) ?? null)
      : null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Accounts receivable</h1>
          <p className="mt-1 text-sm text-slate-500">
            Invoices synchronized from Google Sheets. See what is outstanding and what is overdue.
          </p>
        </div>
        {/* Wrapper keeps the button at its natural height inside the flex row. */}
        <div className="shrink-0">
          <SyncButton syncing={syncing} onSync={() => void handleSync()} />
        </div>
      </div>
      <SyncStatus lastRun={lastRun} syncError={syncError} />
      {summary.kind === "ready" && summary.data.stale && (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="font-semibold">Invoice data may be out of date:</span>{" "}
          {summary.data.last_successful_sync_at
            ? `the last successful sync was more than ${summary.data.stale_after_hours} hours ago.`
            : "there has been no successful sync yet."}{" "}
          Sequences keep using the last synced data until the next successful sync.
        </p>
      )}
      {summary.kind === "ready" && summary.data.ai_budget.automatic_drafting_blocked && (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="font-semibold">Automatic AI drafting is paused:</span>{" "}
          {summary.data.ai_budget.automatic_drafting_blocked === "ai_quota_cooldown"
            ? `Gemini reported its quota is used up; scheduled drafting resumes after ${new Date(summary.data.ai_budget.cooldown_until ?? "").toLocaleString()}.`
            : `today's budget of ${summary.data.ai_budget.limit} AI drafts is used (${summary.data.ai_budget.used_24h} in the last 24 hours).`}{" "}
          Due steps wait; you can still draft manually.
        </p>
      )}
      <SummaryCards state={summary} />
      <AutomationPanel onRunFinished={() => setReloadKey((key) => key + 1)} onOpenInvoice={followUpActions.onOpen} />
      <ApprovalQueue state={followUps} onOpen={followUpActions.onOpen} />
      <TasksPanel reloadKey={reloadKey} onOpenInvoice={followUpActions.onOpen} />
      <InvoiceTable state={invoices} expectedColumns={expectedColumns} followUps={followUpActions} />
      <FollowUpHistory state={followUps} onOpen={followUpActions.onOpen} />

      {openInvoice && (
        <FollowUpDrawer
          invoice={openInvoice}
          followUps={followUpsForInvoice(openInvoice.id)}
          generation={generation?.invoiceId === openInvoice.id ? generation : null}
          onAnalyze={(regenerate) => void handleAnalyze(openInvoice.id, regenerate)}
          onChanged={handleFollowUpChanged}
          onClose={closeDrawer}
        />
      )}
    </div>
  );
}
