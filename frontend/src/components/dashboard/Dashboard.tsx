"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  getAutomationRuns,
  getDashboard,
  getInvoices,
  syncInvoices,
  type AutomationRun,
  type DashboardSummary,
  type Invoice,
} from "@/lib/api/invoices";

import { InvoiceTable } from "./InvoiceTable";
import { SummaryCards } from "./SummaryCards";
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
      <SummaryCards state={summary} />
      <InvoiceTable state={invoices} expectedColumns={expectedColumns} />
    </div>
  );
}
