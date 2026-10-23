"use client";

import { useEffect, useState } from "react";

import { EmptyState, StatusPill, tableStyles, type PillTone } from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { getIntegrationStatus, type HealthStatus, type IntegrationReport, type ScheduledJob } from "@/lib/api/integrations";
import { formatDateTime } from "@/lib/format";

/**
 * Health presentation shared by Connected Services and Automation Settings: one wording for each status,
 * plain names and descriptions for the scheduled jobs, and the report hook (refreshes every 30 seconds).
 */
export const HEALTH: Record<HealthStatus, { label: string; tone: PillTone }> = {
  healthy: { label: "Healthy", tone: "good" },
  stale: { label: "Not running lately", tone: "warn" },
  failed: { label: "Not working", tone: "bad" },
  not_configured: { label: "Not configured", tone: "neutral" },
  awaiting_authorization: { label: "Awaiting authorization", tone: "info" },
};

export function HealthPill({ status }: { status: HealthStatus }) {
  return <StatusPill label={HEALTH[status].label} tone={HEALTH[status].tone} />;
}

/** What each scheduled process does, in plain words. None of them approves or sends email. */
export const JOB_INFO: Record<string, { title: string; does: string }> = {
  "sequences.tick": {
    title: "Prepare due reminders",
    does: "Drafts the reminder for every invoice whose schedule step is due. Drafts wait for a person to approve and send them.",
  },
  "inbox.poll": {
    title: "Check for customer replies",
    does: "Reads new replies from Gmail and asks the AI to suggest what they mean. Chasing pauses until a reply is handled.",
  },
  "inbox.purge": { title: "Remove old reply text", does: "Deletes stored reply text after the retention period. The decisions are kept." },
  "cases.monitor": {
    title: "Check promises and tasks",
    does: "Marks promises kept or broken from the sheet's payment status and flags overdue tasks.",
  },
  "insights.recompute": {
    title: "Risk and analytics",
    does: "Recalculates customer risk scores and the Reports & Insights figures.",
  },
};

export function every(minutes: number): string {
  if (minutes >= 1440) return "Daily";
  if (minutes >= 60 && minutes % 60 === 0) return minutes === 60 ? "Every hour" : `Every ${minutes / 60} hours`;
  return `Every ${minutes} min`;
}

export function useIntegrationReport(): { report: IntegrationReport | null; error: string | null } {
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
    const timer = window.setInterval(() => void load(), 30_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);
  return { report, error };
}

/** The scheduled background jobs as one table (Connected Services and Automation Settings). */
export function JobsTable({ jobs }: { jobs: ScheduledJob[] }) {
  if (jobs.length === 0) return <EmptyState compact title="No scheduled jobs">The worker has no scheduled jobs configured.</EmptyState>;
  return (
    <div className={tableStyles.wrap}>
      <table className={tableStyles.table}>
        <thead>
          <tr className={tableStyles.headRow}>
            <th scope="col" className={tableStyles.th}>Process</th>
            <th scope="col" className={tableStyles.th}>Status</th>
            <th scope="col" className={tableStyles.th}>How often</th>
            <th scope="col" className={tableStyles.th}>Last run</th>
          </tr>
        </thead>
        <tbody className={tableStyles.body}>
          {jobs.map((job) => {
            const info = JOB_INFO[job.kind];
            return (
              <tr key={job.kind} className={tableStyles.row}>
                <td className={`${tableStyles.td} min-w-56 max-w-md`}>
                  <p className="font-medium text-ink">
                    {info?.title ?? job.name}
                    {info && info.title !== job.name && <span className="ml-1.5 text-xs font-normal text-ink-subtle">({job.name})</span>}
                  </p>
                  {info && <p className="mt-0.5 text-xs text-ink-muted">{info.does}</p>}
                </td>
                <td className={tableStyles.td}>
                  <HealthPill status={job.status} />
                </td>
                <td className={`${tableStyles.td} whitespace-nowrap text-ink-muted`}>{every(job.every_minutes)}</td>
                <td className={`${tableStyles.td} whitespace-nowrap text-ink-muted`}>
                  {job.last_completed_at ? formatDateTime(job.last_completed_at) : "Not yet"}
                  {job.last_error_code && <span className="block text-xs text-danger-700">Last error: {job.last_error_code}</span>}
                  <span className={`block text-xs ${job.failed_24h > 0 ? "font-medium text-danger-700" : "text-ink-subtle"}`}>
                    {job.failed_24h} failure{job.failed_24h === 1 ? "" : "s"} in 24 h
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
