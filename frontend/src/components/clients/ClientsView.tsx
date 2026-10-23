"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  Card,
  EmptyState,
  ErrorMessage,
  Icon,
  KeyValues,
  Notice,
  PageHeader,
  Section,
  Skeleton,
  Spinner,
  StatusPill,
  TabPanel,
  Tabs,
  buttonStyles,
  tableStyles,
  type PillTone,
} from "@/components/dashboard/ui";
import { getClientHistory, type HistoryEvent } from "@/lib/api/cases";
import { ApiError } from "@/lib/api/client";
import {
  getClient,
  getStatementPreview,
  getWorklist,
  prepareStatement,
  requestRecompute,
  type ClientProfile,
  type StatementPreview,
  type Worklist,
  type WorklistRow,
} from "@/lib/api/clients";
import { jobError, waitForJob } from "@/lib/api/jobs";
import { formatDate, formatDateTime, formatMoney, formatShortDateTime } from "@/lib/format";
import { SECTION_TITLES } from "@/lib/status";
import { appLink, links, useUrlParam } from "@/lib/url";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function percent(rate: string | null): string {
  return rate === null ? "No history yet" : `${Math.round(Number(rate) * 100)}%`;
}

function riskTone(score: number): PillTone {
  return score >= 60 ? "bad" : score >= 30 ? "warn" : "neutral";
}

/** The deterministic 0–100 score, labelled as such; "low confidence" when payment history is short. */
function RiskBadge({ score, lowConfidence }: { score: number | null; lowConfidence: boolean }) {
  if (score === null) return <StatusPill label="Not scored yet" tone="neutral" />;
  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <StatusPill label={`Risk ${score} / 100`} tone={riskTone(score)} />
      {lowConfidence && <span className="text-[11px] text-ink-subtle">Low confidence</span>}
    </span>
  );
}

function Amounts({ amounts }: { amounts: Record<string, string> }) {
  const entries = Object.entries(amounts);
  if (entries.length === 0) return <>Nothing outstanding</>;
  return <>{entries.map(([currency, amount]) => formatMoney(amount, currency)).join(" · ")}</>;
}

// Worklist ----------------------------------------------------------------------------------------------------

function Blockers({ row }: { row: WorklistRow }) {
  if (row.blockers.length === 0) return <span className="text-sm text-success-700">Chasing allowed</span>;
  const invoices = new Set(row.blockers.map((b) => b.invoice_external_id));
  return (
    <span className="text-sm text-warning-800">
      {invoices.size === 1 ? `${[...invoices][0]} on hold` : `${invoices.size} invoices on hold`}
    </span>
  );
}

function WorklistGroup({ currency, rows, onOpen }: { currency: string; rows: WorklistRow[]; onOpen: (row: WorklistRow) => void }) {
  return (
    <Section
      title={`Customers owing ${currency}`}
      count={rows.length}
      description={`Ranked by risk × amount owed in ${currency} only; other currencies are ranked separately.`}
      flush
    >
      <ul className="divide-y divide-slate-100 xl:hidden" role="listbox" aria-label={`Customers owing ${currency}`}>
        {rows.map((row) => (
          <li key={`${row.client_id}:${row.currency}`}>
            <button
              type="button"
              role="option"
              aria-selected={false}
              onClick={() => onOpen(row)}
              className="block w-full space-y-2 px-5 py-4 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-ink">
                    <span className="mr-1.5 text-ink-subtle">#{row.rank}</span>
                    {row.client_name}
                  </p>
                  <p className="truncate text-xs text-ink-subtle">{row.client_email}</p>
                </div>
                <p className="shrink-0 text-sm font-semibold tabular-nums text-ink">{formatMoney(row.outstanding, row.currency)}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <RiskBadge score={row.risk_score} lowConfidence={row.low_confidence} />
                {row.top_factors[0] && <span className="text-xs text-ink-muted">{row.top_factors[0].label}</span>}
              </div>
              <p className="text-sm text-ink">Next: {row.next_action.title}</p>
            </button>
          </li>
        ))}
      </ul>
      <div className={`hidden xl:block ${tableStyles.wrap}`}>
        <table className={tableStyles.table}>
          <thead>
            <tr className={tableStyles.headRow}>
              <th scope="col" className={tableStyles.th}>Customer</th>
              <th scope="col" className={tableStyles.thNum}>Outstanding</th>
              <th scope="col" className={tableStyles.thNum}>Overdue</th>
              <th scope="col" className={tableStyles.th}>Risk</th>
              <th scope="col" className={tableStyles.th}>Main reason</th>
              <th scope="col" className={tableStyles.th}>Next action</th>
              <th scope="col" className={tableStyles.th}>Chasing</th>
            </tr>
          </thead>
          <tbody className={tableStyles.body}>
            {rows.map((row) => {
              const overdue = row.invoices.filter((i) => i.days_overdue > 0).length;
              return (
                <tr
                  key={`${row.client_id}:${row.currency}`}
                  onClick={() => onOpen(row)}
                  className={`${tableStyles.row} cursor-pointer`}
                >
                  <td className={tableStyles.td}>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpen(row);
                      }}
                      className="rounded text-left font-medium text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      <span className="mr-1.5 text-xs text-ink-subtle tabular-nums">#{row.rank}</span>
                      {row.client_name}
                    </button>
                    <p className="text-xs text-ink-subtle">{row.client_email}</p>
                  </td>
                  <td className={tableStyles.tdNum}>{formatMoney(row.outstanding, row.currency)}</td>
                  <td className={tableStyles.tdNum}>
                    {overdue} {overdue === 1 ? "invoice" : "invoices"}
                    {row.oldest_days_overdue > 0 && (
                      <span className="block text-xs font-normal text-ink-subtle">oldest {row.oldest_days_overdue} days</span>
                    )}
                  </td>
                  <td className={tableStyles.td}>
                    <RiskBadge score={row.risk_score} lowConfidence={row.low_confidence} />
                  </td>
                  <td className={`${tableStyles.td} max-w-[12rem] text-sm text-ink-muted`}>
                    {row.top_factors[0] ? `${row.top_factors[0].label} (+${row.top_factors[0].points})` : "—"}
                  </td>
                  <td className={`${tableStyles.td} max-w-[15rem] text-sm text-ink`}>
                    {row.next_action.title}
                    {row.next_action.blocked && <span className="block text-xs text-brand-800">For {row.next_action.who_can_act.toLowerCase()}</span>}
                  </td>
                  <td className={tableStyles.td}>
                    <Blockers row={row} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

// Customer detail ---------------------------------------------------------------------------------------------

type DetailTab = "overview" | "invoices" | "activity" | "statements";

function Overview({ detail, row }: { detail: ClientProfile; row: WorklistRow | null }) {
  const open = detail.invoices.filter((i) => i.status === "UNPAID" && i.in_sheet);
  const blocked = open.filter((i) => i.blockers.length > 0);
  const promises = detail.promise_summary;
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,65fr)_minmax(0,35fr)]">
      <div className="space-y-6">
        <Section title="Outstanding invoices" count={open.length} headingLevel={3}>
          {open.length === 0 ? (
            <p className="text-sm text-ink-muted">No unpaid invoice in the sheet.</p>
          ) : (
            <KeyValues
              items={[
                { label: "Outstanding", value: <Amounts amounts={detail.outstanding} /> },
                { label: "Oldest overdue", value: detail.oldest_days_overdue ? `${detail.oldest_days_overdue} days` : "Not overdue" },
                { label: "On hold", value: blocked.length ? `${blocked.length} of ${open.length}` : "None" },
              ]}
            />
          )}
        </Section>

        <Section
          title="Why this score"
          headingLevel={3}
          description="Worked out from CollectFlow's own records, not by the AI. It only ranks customers; it never drafts, approves, sends or ends a hold."
          flush
        >
          {detail.factors.length === 0 ? (
            <p className="px-6 py-5 text-sm text-ink-muted">Not calculated yet. The worker calculates scores every hour and after changes.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <tbody className="divide-y divide-slate-100">
                {detail.factors.map((f) => (
                  <tr key={f.key}>
                    <td className="py-3 pr-4 pl-6 align-top font-medium text-ink">{f.label}</td>
                    <td className="py-3 pr-4 text-right align-top whitespace-nowrap tabular-nums text-ink">
                      {f.points} / {f.max}
                    </td>
                    <td className="py-3 pr-6 align-top text-ink-muted">{f.detail}</td>
                  </tr>
                ))}
                <tr className="bg-slate-50/70">
                  <td className="py-3 pr-4 pl-6 font-semibold text-ink">Total</td>
                  <td className="py-3 pr-4 text-right font-semibold whitespace-nowrap tabular-nums text-ink">{detail.risk_score} / 100</td>
                  <td className="py-3 pr-6 text-xs text-ink-subtle">
                    {detail.low_confidence ? "Low confidence: short payment history." : ""}
                    {detail.computed_at ? ` Calculated ${formatDateTime(detail.computed_at)}.` : ""}
                  </td>
                </tr>
              </tbody>
            </table>
          )}
        </Section>

        {row && (
          <Section title={`Recommended next action (${row.currency})`} headingLevel={3}>
            <div className="space-y-3">
              <p className="text-sm font-medium text-ink">{row.next_action.title}</p>
              {row.next_action.detail && <p className="text-sm text-ink-muted">{row.next_action.detail}</p>}
              <p className="text-xs text-ink-subtle">Who can act: {row.next_action.who_can_act || "anyone"}</p>
              {row.next_action.blocked && (
                <Notice tone="info">
                  <span className="font-semibold">You cannot do this yourself:</span> {row.next_action.blocked}
                </Notice>
              )}
              {row.blockers.length > 0 && (
                <Notice tone="warning" title="What stops chasing">
                  <ul className="list-disc space-y-0.5 pl-4">
                    {row.blockers.map((b) => (
                      <li key={`${b.invoice_id}:${b.code}`}>
                        {b.invoice_external_id}: {b.detail}
                      </li>
                    ))}
                  </ul>
                </Notice>
              )}
              {row.next_action.link && (
                <Link href={appLink(row.next_action.link)} className={buttonStyles.primary}>
                  Open
                </Link>
              )}
            </div>
          </Section>
        )}
      </div>

      <div className="space-y-6">
        <Section title="Customer" headingLevel={3}>
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-xs text-ink-subtle">Email</dt>
              <dd className="break-all text-ink">{detail.email}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-subtle">{detail.invoice_names.length > 1 ? "Names used on invoices" : "Name on invoices"}</dt>
              <dd className="text-ink">{detail.invoice_names.join(", ") || detail.display_name}</dd>
            </div>
          </dl>
        </Section>
        <Section title="Payment behaviour" headingLevel={3}>
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Average days late</dt>
              <dd className="font-medium tabular-nums text-ink">{detail.avg_days_late === null ? "No measured payment" : `${detail.avg_days_late} days`}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Promises kept</dt>
              <dd className="font-medium tabular-nums text-ink">{percent(detail.promise_kept_rate)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Reminders answered</dt>
              <dd className="font-medium tabular-nums text-ink">{percent(detail.response_rate)}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-ink-subtle">Paid dates are when a sync first saw PAID, not the bank date.</p>
        </Section>
        <Section title="Promises, disputes and tasks" headingLevel={3}>
          <div className="space-y-3 text-sm">
            <p className="text-ink-muted">
              Promises: {promises.kept} kept, {promises.broken} broken, {promises.open} open
              {promises.cancelled ? `, ${promises.cancelled} cancelled` : ""}.
            </p>
            {detail.disputes.length > 0 && (
              <ul className="space-y-1">
                {detail.disputes.map((d) => (
                  <li key={d.id} className="flex justify-between gap-3">
                    <Link href={appLink(d.link)} className={buttonStyles.link}>
                      Dispute · {d.invoice_external_id}
                    </Link>
                    <span className="text-xs text-ink-subtle">{d.status === "OPEN" ? "Open" : "Resolved"}</span>
                  </li>
                ))}
              </ul>
            )}
            {detail.open_tasks.length > 0 && (
              <ul className="space-y-1">
                {detail.open_tasks.map((t) => (
                  <li key={t.id} className="flex justify-between gap-3">
                    <Link href={appLink(t.link)} className={buttonStyles.link}>
                      {t.title}
                    </Link>
                    <span className="shrink-0 text-xs text-ink-subtle">due {formatShortDateTime(t.due_at, false)}</span>
                  </li>
                ))}
              </ul>
            )}
            {detail.disputes.length === 0 && detail.open_tasks.length === 0 && <p className="text-ink-subtle">No open dispute or task.</p>}
          </div>
        </Section>
      </div>
    </div>
  );
}

function InvoicesTab({ detail }: { detail: ClientProfile }) {
  return (
    <Section title="Invoices" count={detail.invoices.length} flush>
      {detail.invoices.length === 0 ? (
        <EmptyState compact title="No invoices">Invoices for this customer appear after the next sync.</EmptyState>
      ) : (
        <div className={tableStyles.wrap}>
          <table className={tableStyles.table}>
            <thead>
              <tr className={tableStyles.headRow}>
                <th scope="col" className={tableStyles.th}>Invoice</th>
                <th scope="col" className={tableStyles.th}>Due date</th>
                <th scope="col" className={tableStyles.thNum}>Amount</th>
                <th scope="col" className={tableStyles.th}>Status</th>
                <th scope="col" className={tableStyles.th}>What stops chasing</th>
              </tr>
            </thead>
            <tbody className={tableStyles.body}>
              {detail.invoices.map((i) => {
                const [label, tone]: [string, PillTone] =
                  i.status === "PAID" ? ["Paid", "good"] : i.open_dispute_id ? ["Disputed", "bad"] : i.open_promise_id ? ["Promise due", "info"] : ["Unpaid", "neutral"];
                return (
                  <tr key={i.id} className={tableStyles.row}>
                    <td className={tableStyles.td}>
                      <Link href={appLink(i.link)} className="font-mono text-sm font-medium text-brand-700 hover:underline">
                        {i.external_id}
                      </Link>
                      {!i.in_sheet && <span className="block text-xs text-ink-subtle">Not in the sheet</span>}
                    </td>
                    <td className={`${tableStyles.td} whitespace-nowrap`}>{formatDate(i.due_date)}</td>
                    <td className={tableStyles.tdNum}>{formatMoney(i.amount, i.currency)}</td>
                    <td className={tableStyles.td}>
                      <StatusPill label={label} tone={tone} />
                    </td>
                    <td className={`${tableStyles.td} max-w-sm text-sm text-ink-muted`}>{i.blockers.length ? i.blockers.join(" ") : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function ActivityTab({ detail }: { detail: ClientProfile }) {
  const [events, setEvents] = useState<HistoryEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getClientHistory(detail.id, controller.signal)
      .then(setEvents)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [detail.id]);
  const missing = detail.paid_history.filter((p) => p.paid_seen_on === null).length;
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,65fr)_minmax(0,35fr)]">
      <Section title="Communication" description="Reminders sent and replies received, newest first." headingLevel={3} flush>
        {detail.communications.length === 0 ? (
          <EmptyState compact title="No emails yet">Sent reminders and customer replies will appear here.</EmptyState>
        ) : (
          <ol className="divide-y divide-slate-100">
            {detail.communications.map((c, n) => (
              <li key={`${c.at}:${n}`} className="flex gap-3 px-6 py-3">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-ink-muted" aria-hidden="true">
                  <Icon name={c.type === "sent" ? "mail" : "user"} className="h-3.5 w-3.5" />
                </span>
                <div className="min-w-0 text-sm">
                  <p className="text-ink">
                    {c.type === "sent" ? (c.kind === "STATEMENT" ? "Statement sent" : `Reminder sent (${c.tone.toLowerCase()})`) : "Reply received"}
                    {" · "}
                    <Link href={appLink(c.link)} className="font-mono text-xs font-medium text-brand-700 hover:underline">
                      {c.invoice_external_id}
                    </Link>
                  </p>
                  <p className="truncate text-ink-muted">{c.subject}</p>
                  <p className="text-xs text-ink-subtle">{formatDateTime(c.at)}</p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </Section>
      <div className="space-y-6">
        <Section title="Payments seen" headingLevel={3}>
          {detail.paid_history.length === 0 ? (
            <p className="text-sm text-ink-muted">No paid invoice yet.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {detail.paid_history.map((p) => (
                <li key={p.id}>
                  <Link href={appLink(p.link)} className="font-mono text-xs font-medium text-brand-700 hover:underline">
                    {p.external_id}
                  </Link>{" "}
                  <span className="tabular-nums text-ink">{formatMoney(p.amount, p.currency)}</span>
                  <span className="block text-xs text-ink-subtle">
                    {p.paid_seen_on === null ? "Paid date unknown (already paid when imported)" : `Paid seen ${formatDate(p.paid_seen_on)}, ${p.days_late} day(s) late`}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {missing > 0 && <p className="mt-3 text-xs text-ink-subtle">{missing} paid invoice(s) have no observed paid date.</p>}
        </Section>
        <Section title="History" description="Every recorded event for this customer." headingLevel={3}>
          {error && <ErrorMessage message={`Could not load the history: ${error}`} />}
          {events === null && !error ? (
            <Skeleton className="h-24" />
          ) : events && events.length === 0 ? (
            <p className="text-sm text-ink-muted">Nothing recorded yet.</p>
          ) : (
            <ol className="space-y-2 border-l border-line pl-4 text-xs text-ink-muted">
              {(events ?? []).slice(0, 40).map((event) => (
                <li key={event.id}>
                  <span className="text-ink">{event.type_label}</span>
                  {event.actor_username ? ` by ${event.actor_username}` : event.actor_type !== "USER" ? ` (${event.actor_type.toLowerCase()})` : ""}
                  <span className="block text-ink-subtle">
                    {formatDateTime(event.occurred_at)}
                    {event.invoice ? (
                      <>
                        {" · "}
                        <Link href={links.invoice(event.invoice)} className="text-brand-700 hover:underline">
                          open invoice
                        </Link>
                      </>
                    ) : null}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </Section>
      </div>
    </div>
  );
}

function StatementSection({ clientId, statements }: { clientId: string; statements: ClientProfile["statements"] }) {
  const [preview, setPreview] = useState<StatementPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getStatementPreview(clientId, controller.signal)
      .then(setPreview)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [clientId, reload]);

  const prepare = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const { job_id } = await prepareStatement(clientId);
      const job = await waitForJob(job_id);
      if (job.status === "FAILED") throw jobError(job);
      setMessage("Statement drafted. It has not been sent: an approver who did not edit it reviews it in Approvals.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      setReload((n) => n + 1);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,65fr)_minmax(0,35fr)]">
      <div>
        <Section
          title="Consolidated statement"
          headingLevel={3}
          description="One email listing every overdue invoice that can be chased now. Gemini writes only the wording; CollectFlow writes the invoice list and totals."
          flush
        >
          {preview === null && !error && <Skeleton className="m-6 h-16" />}
          {preview && (
            <>
              {preview.lines.length > 0 ? (
                <table className="w-full text-sm">
                  <tbody className="divide-y divide-slate-100">
                    {preview.lines.map((line) => (
                      <tr key={line.invoice_id}>
                        <td className="py-3 pl-6 font-mono text-xs text-ink">{line.external_id}</td>
                        <td className="py-3 text-ink-muted">
                          due {formatDate(line.due_date)}
                          {line.replaces_draft_id ? " · replaces its unsent draft" : ""}
                        </td>
                        <td className="py-3 pr-6 text-right tabular-nums text-ink">{formatMoney(line.amount, line.currency)}</td>
                      </tr>
                    ))}
                    {Object.keys(preview.totals).length > 0 && (
                      <tr className="bg-slate-50/70">
                        <td className="py-3 pl-6 font-semibold text-ink" colSpan={2}>
                          Total:
                        </td>
                        <td className="py-3 pr-6 text-right font-semibold tabular-nums text-ink">
                          {Object.entries(preview.totals)
                            .map(([currency, amount]) => formatMoney(amount, currency))
                            .join(" + ")}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              ) : (
                <p className="px-6 pt-5 text-sm text-ink-muted">No invoice can go on a statement right now.</p>
              )}
              <div className="space-y-3 px-6 py-5">
                {preview.excluded.length > 0 && (
                  <details className="group text-sm text-ink-muted">
                    <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium text-ink">
                      <Icon name="chevron" className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
                      Not included ({preview.excluded.length})
                    </summary>
                    <ul className="mt-2 list-disc space-y-0.5 pl-5">
                      {preview.excluded.map((line) => (
                        <li key={line.invoice_id}>
                          {line.external_id}: {line.reasons?.join(" ")}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {message && (
                  <Notice tone="success" role="status">
                    {message}
                  </Notice>
                )}
                {error && <ErrorMessage message={error} />}
                <div className="flex flex-wrap items-center justify-end gap-3">
                  {!preview.can_prepare && <p className="mr-auto text-sm text-ink-muted">{preview.reason}</p>}
                  {preview.can_prepare && (
                    <button type="button" onClick={() => void prepare()} disabled={busy} className={buttonStyles.primary}>
                      {busy && <Spinner />}
                      Prepare statement draft
                    </button>
                  )}
                </div>
              </div>
            </>
          )}
        </Section>
      </div>
      <Section title="Statements so far" count={statements.length} headingLevel={3}>
        {statements.length === 0 ? (
          <p className="text-sm text-ink-muted">No statement prepared yet.</p>
        ) : (
          <ul className="space-y-3 text-sm">
            {statements.map((s) => (
              <li key={s.id}>
                <Link href={appLink(s.link)} className={buttonStyles.link}>
                  {s.subject}
                </Link>
                <span className="block text-xs text-ink-subtle">
                  {s.lines} invoices · {s.status.toLowerCase().replace(/_/g, " ")} ·{" "}
                  {s.sent_at ? `sent ${formatDateTime(s.sent_at)}` : `drafted ${formatDateTime(s.generated_at)}`}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-ink-subtle">Nothing is sent until an approver approves it and someone confirms the send.</p>
      </Section>
    </div>
  );
}

function CustomerDetail({ clientId, row, onBack }: { clientId: string; row: WorklistRow | null; onBack: () => void }) {
  const [detail, setDetail] = useState<ClientProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<DetailTab>("overview");

  useEffect(() => {
    const controller = new AbortController();
    getClient(clientId, controller.signal)
      .then(setDetail)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [clientId]);

  const back = (
    <button type="button" onClick={onBack} className={`${buttonStyles.ghost} -ml-3`}>
      <Icon name="chevron" className="h-4 w-4 rotate-180" />
      All customers
    </button>
  );
  if (error) {
    return (
      <div className="space-y-4">
        {back}
        <ErrorMessage message={`Could not load the customer: ${error}`} />
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading the customer">
        {back}
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }
  const openInvoices = detail.invoices.filter((i) => i.status === "UNPAID" && i.in_sheet).length;
  return (
    <div className="space-y-6">
      <div>
        {back}
        <header className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">{detail.display_name}</h1>
            <p className="mt-1 text-sm text-ink-muted">{detail.email}</p>
          </div>
          <div className="flex items-start gap-8">
            <div className="sm:text-right">
              <p className="text-xs text-ink-subtle">Outstanding</p>
              <p className="text-lg font-semibold tabular-nums text-ink">
                <Amounts amounts={detail.outstanding} />
              </p>
              <p className="text-xs text-ink-subtle">{openInvoices} unpaid invoice(s)</p>
            </div>
            <div>
              <p className="mb-1 text-xs text-ink-subtle">Collection risk</p>
              <RiskBadge score={detail.risk_score} lowConfidence={detail.low_confidence} />
            </div>
          </div>
        </header>
      </div>
      <Tabs
        idBase="customer"
        label="Customer details"
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "overview", label: "Overview" },
          { key: "invoices", label: "Invoices", count: detail.invoices.length },
          { key: "activity", label: "Activity" },
          { key: "statements", label: "Statements", count: detail.statements.length || undefined },
        ]}
      />
      <TabPanel idBase="customer" tab={tab}>
        {tab === "overview" && <Overview detail={detail} row={row} />}
        {tab === "invoices" && <InvoicesTab detail={detail} />}
        {tab === "activity" && <ActivityTab detail={detail} />}
        {tab === "statements" && <StatementSection clientId={detail.id} statements={detail.statements} />}
      </TabPanel>
    </div>
  );
}

// Page --------------------------------------------------------------------------------------------------------

export function ClientsView() {
  const router = useRouter();
  const [data, setData] = useState<Worklist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const clientParam = useUrlParam("client");
  const [currency, setCurrency] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [queued, setQueued] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getWorklist(controller.signal)
      .then((worklist) => {
        setData(worklist);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [reload]);

  // Opening a customer is a navigation (Back returns to the list); the URL keeps the deep link.
  const open = (row: WorklistRow) => {
    setCurrency(row.currency);
    router.push(`/clients?client=${row.client_id}`);
  };

  const recompute = () => {
    setQueueing(true);
    requestRecompute()
      .then(() => setQueued("Recalculation queued. The worker runs it within a few seconds; refresh to see the new scores."))
      .catch((err: unknown) => setQueued(`Could not queue it: ${errorText(err)}`))
      .finally(() => setQueueing(false));
  };

  const rows = data?.groups.flatMap((g) => g.rows) ?? [];
  if (clientParam) {
    const row =
      rows.find((r) => r.client_id === clientParam && (currency === null || r.currency === currency)) ??
      rows.find((r) => r.client_id === clientParam) ??
      null;
    return <CustomerDetail key={clientParam} clientId={clientParam} row={row} onBack={() => router.push("/clients")} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Customers"
        description="Understand customer balances, payment history and collection risk. The risk score (0–100) is worked out from your records and only helps you prioritise."
        actions={
          <>
            <button type="button" onClick={() => setReload((n) => n + 1)} className={buttonStyles.secondary}>
              <Icon name="sync" className="h-4 w-4" />
              Refresh
            </button>
            <button type="button" onClick={recompute} disabled={queueing} className={buttonStyles.secondary}>
              {queueing && <Spinner />}
              Recalculate scores
            </button>
          </>
        }
      />
      {queued && (
        <Notice tone="info" role="status">
          {queued}
        </Notice>
      )}
      {data && data.urgent.length > 0 && (
        <Notice
          tone="warning"
          role="status"
          title="Handle these first, whatever the ranking"
          action={
            <Link href="/today" className={buttonStyles.secondarySm}>
              Open Today&apos;s Work
            </Link>
          }
        >
          {data.urgent.map((u) => `${SECTION_TITLES[u.key] ?? u.title} (${u.count})`).join(" · ")}
        </Notice>
      )}
      {error && <ErrorMessage message={`Could not load the worklist: ${error}${data ? " Showing the last loaded list." : ""}`} />}
      {data === null && !error && (
        <Card className="space-y-3 p-5">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </Card>
      )}
      {data && data.groups.length === 0 && (
        <Card>
          <EmptyState title="No customer owes anything" icon="check">
            Customers appear here when they have an unpaid invoice in the Google Sheet.
          </EmptyState>
        </Card>
      )}
      {data?.groups.map((group) => (
        <WorklistGroup key={group.currency} currency={group.currency} rows={group.rows} onOpen={open} />
      ))}
      {data && (data.scores_computed_at || data.unscored.length > 0) && (
        <p className="text-xs text-ink-subtle">
          {data.scores_computed_at ? `Scores calculated ${formatDateTime(data.scores_computed_at)} or later. ` : ""}
          {data.unscored.length > 0 ? `${data.unscored.length} customer(s) not scored yet; they rank last until the next calculation.` : ""}
        </p>
      )}
    </div>
  );
}
