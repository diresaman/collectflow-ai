"use client";

import { useEffect, useState } from "react";

import { Card, ErrorMessage, Skeleton } from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { getIntegrationStatus, type HealthStatus, type IntegrationReport } from "@/lib/api/integrations";
import { formatDateTime } from "@/lib/format";

const REFRESH_MS = 30_000;

const BADGES: Record<HealthStatus, { text: string; className: string }> = {
  healthy: { text: "Healthy", className: "border-emerald-200 bg-emerald-50 text-emerald-800" },
  stale: { text: "Not running lately", className: "border-amber-300 bg-amber-50 text-amber-900" },
  failed: { text: "Not working", className: "border-rose-200 bg-rose-50 text-rose-800" },
  not_configured: { text: "Not configured", className: "border-slate-300 bg-slate-50 text-slate-700" },
  awaiting_authorization: { text: "Awaiting authorization", className: "border-sky-200 bg-sky-50 text-sky-800" },
};

const FACT_NAMES: Record<string, string> = {
  invoices_in_last_sync: "Invoices in last sync",
  replies_needing_review: "Replies needing review",
  invoices_on_hold: "Invoices on hold",
  poll_minutes: "Poll every (minutes)",
  cursor_set: "History cursor set",
  messages_ingested: "Replies stored",
  last_rescan_at: "Last thread rescan",
  last_rescan_reason: "Rescan reason",
  model: "Model",
  calls_24h: "Gemini calls (24 h)",
  drafting_budget_24h: "Automatic drafting budget (24 h)",
  classifications_24h: "Classifications (24 h)",
  cooldown_until: "Quota cooldown until",
  last_error_code: "Last error",
};

function Badge({ status }: { status: HealthStatus }) {
  const badge = BADGES[status];
  return <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${badge.className}`}>{badge.text}</span>;
}

function factValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) return formatDateTime(value);
  return String(value);
}

export function IntegrationsView() {
  const [report, setReport] = useState<IntegrationReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      getIntegrationStatus(controller.signal)
        .then((data) => {
          setReport(data);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setError(err instanceof ApiError ? err.message : "Unexpected error.");
        });
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Connected Services</h1>
        <p className="mt-1 text-sm text-slate-600">
          Whether Google Sheets, Gmail, Gemini and the background worker are working, and what to do if not.
        </p>
      </div>
      {error && <ErrorMessage message={`Could not load the service status: ${error}`} />}
      {report === null ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : (
        <>
          {(() => {
            const problems = [...report.integrations, ...report.scheduled_jobs].filter(
              (item) => item.status === "failed" || item.status === "stale",
            );
            return problems.length === 0 ? (
              <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
                <span className="font-semibold">Everything is working.</span> Nothing needs fixing.
              </p>
            ) : (
              <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
                <span className="font-semibold">{problems.length} problem(s):</span>{" "}
                {problems.map((item) => item.name).join(", ")}. Each one below says what to do. Problems with the background
                worker stop scheduled syncs, reply checks and drafting until it runs again.
              </p>
            );
          })()}
          <div className="grid gap-4 md:grid-cols-2">
            {[...report.integrations]
              .sort((a, b) => Number(b.status === "failed" || b.status === "stale") - Number(a.status === "failed" || a.status === "stale"))
              .map((item) => (
              <Card key={item.key} className="space-y-2 p-4">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-base font-semibold text-slate-900">{item.name}</h2>
                  <Badge status={item.status} />
                </div>
                <p className="text-sm text-slate-700">{item.detail}</p>
                {item.action && (
                  <p className="rounded-md bg-slate-50 px-2 py-1.5 text-xs text-slate-700">
                    <span className="font-semibold">What to do:</span> {item.action}
                  </p>
                )}
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  <dt className="text-slate-500">Last success</dt>
                  <dd className="text-slate-800">{item.last_success_at ? formatDateTime(item.last_success_at) : "—"}</dd>
                  <dt className="text-slate-500">Last attempt</dt>
                  <dd className="text-slate-800">{item.last_attempt_at ? formatDateTime(item.last_attempt_at) : "—"}</dd>
                  {Object.entries(item.facts).map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="text-slate-500">{FACT_NAMES[key] ?? key.replace(/_/g, " ")}</dt>
                      <dd className="text-slate-800">{factValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              </Card>
            ))}
          </div>
          <Card className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <caption className="px-4 pt-4 text-left text-base font-semibold text-slate-900">Scheduled jobs</caption>
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="px-4 py-2 font-medium">Job</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Every</th>
                  <th className="px-4 py-2 font-medium">Last finished</th>
                  <th className="px-4 py-2 font-medium">Failures (24 h)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {report.scheduled_jobs.map((job) => (
                  <tr key={job.kind}>
                    <td className="px-4 py-2 text-slate-900">{job.name}</td>
                    <td className="px-4 py-2">
                      <Badge status={job.status} />
                    </td>
                    <td className="px-4 py-2 text-slate-700">
                      {job.every_minutes >= 1440 ? "daily" : `${job.every_minutes} min`}
                    </td>
                    <td className="px-4 py-2 text-slate-700">
                      {job.last_completed_at ? formatDateTime(job.last_completed_at) : "—"}
                      {job.last_error_code ? ` (${job.last_error_code})` : ""}
                    </td>
                    <td className="px-4 py-2 text-slate-700">{job.failed_24h}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <p className="text-xs text-slate-500">Updated {formatDateTime(report.generated_at)}; refreshes every 30 seconds.</p>
        </>
      )}
    </div>
  );
}
