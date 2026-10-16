import type { AutomationRun } from "@/lib/api/invoices";
import { formatDateTime } from "@/lib/format";

import type { Loadable } from "./types";
import { ErrorMessage, Icon, Skeleton } from "./ui";

const MAX_ERRORS_SHOWN = 10;

export function SyncButton({ syncing, onSync }: { syncing: boolean; onSync: () => void }) {
  return (
    <button
      type="button"
      onClick={onSync}
      disabled={syncing}
      className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 sm:w-auto text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-70"
    >
      <Icon name="sync" className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
      {syncing ? "Syncing…" : "Sync Invoices"}
    </button>
  );
}

function Counts({ run }: { run: AutomationRun }) {
  const parts: [string, number][] = [
    ["processed", run.records_processed],
    ["created", run.created_count],
    ["updated", run.updated_count],
    ["unchanged", run.unchanged_count],
  ];
  return (
    <span className="text-slate-500">
      {parts.map(([label, value], index) => (
        <span key={label}>
          {index > 0 && <span className="mx-1.5 text-slate-300">·</span>}
          <span className="font-medium tabular-nums text-slate-700">{value}</span> {label}
        </span>
      ))}
      {run.rejected_count > 0 && (
        <>
          <span className="mx-1.5 text-slate-300">·</span>
          <span className="font-medium tabular-nums text-rose-600">{run.rejected_count}</span>{" "}
          <span className="text-rose-600">rejected</span>
        </>
      )}
    </span>
  );
}

function RunErrors({ run }: { run: AutomationRun }) {
  // The first line of error_summary is the headline; row details come from `errors`.
  const headline = run.error_summary.split("\n")[0];
  return (
    <div className="border-t border-rose-200 px-4 py-3 text-sm text-rose-700">
      <p className="font-medium">{headline}</p>
      {run.errors.length > 0 && (
        <ul className="mt-2 space-y-1">
          {run.errors.slice(0, MAX_ERRORS_SHOWN).map((error, index) => (
            <li key={index} className="flex gap-2">
              {error.row !== null && (
                <span className="shrink-0 rounded bg-rose-100 px-1.5 py-0.5 font-mono text-xs">Row {error.row}</span>
              )}
              <span>
                {error.field && <span className="font-mono text-xs">{error.field}: </span>}
                {error.message}
              </span>
            </li>
          ))}
          {run.errors.length > MAX_ERRORS_SHOWN && <li>…and {run.errors.length - MAX_ERRORS_SHOWN} more.</li>}
        </ul>
      )}
    </div>
  );
}

export function SyncStatus({ lastRun, syncError }: { lastRun: Loadable<AutomationRun | null>; syncError: string | null }) {
  return (
    <div className="space-y-3">
      {syncError && <ErrorMessage message={`Sync could not be started: ${syncError}`} />}

      {lastRun.kind === "loading" && <Skeleton className="h-12 w-full rounded-xl" />}
      {lastRun.kind === "error" && <ErrorMessage message={`Could not load sync history: ${lastRun.message}`} />}

      {lastRun.kind === "ready" && lastRun.data === null && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white px-4 py-3 text-sm text-slate-500">
          No sync has run yet. Click <span className="font-medium text-slate-700">Sync Invoices</span> to import
          invoices from Google Sheets.
        </div>
      )}

      {lastRun.kind === "ready" && lastRun.data !== null && (
        <div
          className={`overflow-hidden rounded-xl border text-sm ${
            lastRun.data.status === "FAILED" ? "border-rose-200 bg-rose-50" : "border-slate-200 bg-white"
          }`}
        >
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-3">
            <span className="inline-flex items-center gap-2 font-medium text-slate-900">
              <span
                className={`h-2 w-2 rounded-full ${
                  lastRun.data.status === "SUCCEEDED"
                    ? "bg-emerald-500"
                    : lastRun.data.status === "FAILED"
                      ? "bg-rose-500"
                      : "bg-slate-400"
                }`}
                aria-hidden="true"
              />
              {lastRun.data.status === "SUCCEEDED"
                ? "Last sync succeeded"
                : lastRun.data.status === "FAILED"
                  ? "Last sync failed"
                  : "Sync in progress"}
            </span>
            <span className="text-slate-500">{formatDateTime(lastRun.data.completed_at ?? lastRun.data.started_at)}</span>
            <Counts run={lastRun.data} />
          </div>
          {lastRun.data.status === "FAILED" && <RunErrors run={lastRun.data} />}
        </div>
      )}
    </div>
  );
}
