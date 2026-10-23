"use client";

import { ErrorMessage, Icon, Notice, PageHeader, Section, Skeleton, type IconName } from "@/components/dashboard/ui";
import { formatDateTime } from "@/lib/format";

import { HealthPill, JOB_INFO, JobsTable, useIntegrationReport } from "./health";

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

/** What each service is for, so the page explains itself. */
const SERVICE_INFO: Record<string, { icon: IconName; does: string }> = {
  sheets: { icon: "document", does: "Reads invoices and payment status from your Google Sheet (read-only)." },
  gmail_send: { icon: "mail", does: "Sends reminders from your Gmail account, only after a person approves and confirms each one." },
  gmail_read: { icon: "mail", does: "Read-only access to replies in the reminder threads." },
  reply_polling: { icon: "sync", does: "Checks for new customer replies on a schedule." },
  gemini: { icon: "sparkles", does: "AI that drafts reminder wording and suggests what replies mean. It never sends or decides." },
  worker: { icon: "clock", does: "Runs syncs, drafting, reply checks and scheduled jobs in the background." },
};

function factValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) return formatDateTime(value);
  return String(value);
}

export function IntegrationsView() {
  const { report, error } = useIntegrationReport();
  const problems = report
    ? [
        ...report.integrations.filter((item) => item.status === "failed" || item.status === "stale").map((item) => item.name),
        ...report.scheduled_jobs
          .filter((job) => job.status === "failed" || job.status === "stale")
          .map((job) => JOB_INFO[job.kind]?.title ?? job.name),
      ]
    : [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Connected Services"
        description="Monitor the services used for invoices, email and AI assistance, and the background jobs that depend on them."
        actions={report ? <span className="text-xs text-ink-subtle">Updated {formatDateTime(report.generated_at)} · refreshes every 30 s</span> : undefined}
      />
      {error && <ErrorMessage message={`Could not load the service status: ${error}`} />}
      {report === null ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading service status">
          <Skeleton className="h-24 rounded-xl" />
          <Skeleton className="h-24 rounded-xl" />
          <Skeleton className="h-24 rounded-xl" />
        </div>
      ) : (
        <>
          {problems.length === 0 ? (
            <Notice tone="success" role="status" title="Everything is working">
              Nothing needs fixing.
            </Notice>
          ) : (
            <Notice tone="danger" role="alert" title={`${problems.length} problem(s): ${problems.join(", ")}`}>
              Each one below says what to do. Problems with the background worker stop scheduled syncs, reply checks and drafting
              until it runs again.
            </Notice>
          )}

          <Section title="Services" description="Connection and health of each external service." flush>
            <ul className="divide-y divide-slate-100">
              {[...report.integrations]
                .sort((a, b) => Number(b.status === "failed" || b.status === "stale") - Number(a.status === "failed" || a.status === "stale"))
                .map((item) => {
                  const info = SERVICE_INFO[item.key];
                  const facts = Object.entries(item.facts);
                  return (
                    <li key={item.key} className="grid gap-4 px-5 py-5 sm:px-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)]">
                      <div className="flex min-w-0 gap-3">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-ink-muted" aria-hidden="true">
                          <Icon name={info?.icon ?? "info"} className="h-4 w-4" />
                        </span>
                        <div className="min-w-0">
                          <h3 className="text-sm font-semibold text-ink">{item.name}</h3>
                          {info && <p className="mt-0.5 text-sm text-ink-muted">{info.does}</p>}
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <HealthPill status={item.status} />
                        <p className="text-sm text-ink">{item.detail}</p>
                        {item.action && (
                          <p className="text-xs text-ink-muted">
                            <span className="font-semibold text-ink">What to do:</span> {item.action}
                          </p>
                        )}
                      </div>
                      <div className="text-xs">
                        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
                          <dt className="text-ink-subtle">Last success</dt>
                          <dd className="text-ink">{item.last_success_at ? formatDateTime(item.last_success_at) : "—"}</dd>
                          <dt className="text-ink-subtle">Last attempt</dt>
                          <dd className="text-ink">{item.last_attempt_at ? formatDateTime(item.last_attempt_at) : "—"}</dd>
                        </dl>
                        {facts.length > 0 && (
                          <details className="group mt-2">
                            <summary className="flex cursor-pointer list-none items-center gap-1 font-medium text-ink-muted hover:text-ink">
                              <Icon name="chevron" className="h-3 w-3 transition-transform group-open:rotate-90" />
                              Details
                            </summary>
                            <dl className="mt-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
                              {facts.map(([key, value]) => (
                                <div key={key} className="contents">
                                  <dt className="text-ink-subtle">{FACT_NAMES[key] ?? key.replace(/_/g, " ")}</dt>
                                  <dd className="text-ink">{factValue(value)}</dd>
                                </div>
                              ))}
                            </dl>
                          </details>
                        )}
                      </div>
                    </li>
                  );
                })}
            </ul>
          </Section>

          <Section
            title="Scheduled jobs"
            description="Background work the worker runs on a timer. It never approves or sends email. Job health is separate from service health: a job can be late while its service is fine."
            flush
          >
            <JobsTable jobs={report.scheduled_jobs} />
          </Section>
        </>
      )}
    </div>
  );
}
