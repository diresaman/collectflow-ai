"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import {
  EmptyState,
  ErrorMessage,
  Notice,
  PageHeader,
  Section,
  SegmentedFilter,
  Skeleton,
  StatCard,
  TabPanel,
  Tabs,
  buttonStyles,
  tableStyles,
} from "@/components/dashboard/ui";
import { getAnalytics, type AnalyticsOverview } from "@/lib/api/analytics";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { appLink } from "@/lib/url";

/**
 * Reports & Insights: the stored M14 analytics snapshots, presented per currency (never combined). Nothing
 * here is recalculated in the browser beyond picking one currency; the worker computes every figure.
 */
type ReportTab = "overview" | "aging" | "collections" | "forecast";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function Bar({ value, max, className }: { value: number; max: number; className: string }) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-2 w-full rounded-full bg-slate-100" aria-hidden="true">
      {value > 0 && <div className={`h-2 rounded-full ${className}`} style={{ width: `${width}%` }} />}
    </div>
  );
}

function Calculated({ at }: { at?: string }) {
  return at ? <span className="text-xs text-ink-subtle">Calculated {formatDateTime(at)}</span> : null;
}

const BUCKET_HELP: Record<string, string> = {
  current: "Not yet due.",
  "1_30": "1 to 30 days past the due date.",
  "31_60": "31 to 60 days past the due date.",
  "61_90": "61 to 90 days past the due date.",
  "90_plus": "More than 90 days past the due date.",
};

export function AnalyticsView() {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<ReportTab>("overview");
  const [picked, setPicked] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getAnalytics(controller.signal)
      .then(setData)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, []);

  const s = data?.snapshots;
  const currencies = [
    ...new Set([
      ...(s?.kpis?.currencies.map((c) => c.currency) ?? []),
      ...(s?.aging?.currencies.map((c) => c.currency) ?? []),
      ...(s?.forecast?.currencies.map((c) => c.currency) ?? []),
    ]),
  ];
  const currency = picked && currencies.includes(picked) ? picked : (currencies[0] ?? null);
  const kpi = s?.kpis?.currencies.find((c) => c.currency === currency);
  const aging = s?.aging?.currencies.find((c) => c.currency === currency);
  const dso = s?.dso_cei;
  const dsoC = dso?.currencies.find((c) => c.currency === currency);
  const forecast = s?.forecast;
  const fc = forecast?.currencies.find((c) => c.currency === currency);
  const attribution = s?.attribution;
  const payments = attribution?.payments.filter((p) => p.currency === currency) ?? [];
  const forecastRows = forecast?.invoices.filter((r) => r.currency === currency) ?? [];

  const header = (
    <PageHeader
      title="Reports & Insights"
      description="Understand outstanding payments and collection performance. Every figure is per currency; paid dates are when a sync first saw PAID, not the bank date."
      actions={
        currencies.length > 1 && currency ? (
          <SegmentedFilter label="Currency" value={currency} onChange={setPicked} options={currencies.map((c) => ({ key: c, label: c }))} />
        ) : currency ? (
          <span className="text-sm text-ink-muted">
            Currency: <span className="font-semibold text-ink">{currency}</span> · last {dso?.period_days ?? 30} days
          </span>
        ) : undefined
      }
    />
  );

  if (error || data === null || !data.computed || !currency) {
    return (
      <div className="space-y-6">
        {header}
        {error && <ErrorMessage message={`Could not load the reports: ${error}`} />}
        {data === null && !error && (
          <div className="space-y-4" aria-busy="true" aria-label="Loading reports">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-28 rounded-xl" />
              ))}
            </div>
            <Skeleton className="h-64 rounded-xl" />
          </div>
        )}
        {data && !data.computed && (
          <Section title="Not calculated yet">
            <p className="text-sm text-ink-muted">
              The worker calculates these figures within the hour or after the next change; you can also use
              &quot;Recalculate scores&quot; on the Customers page.
            </p>
          </Section>
        )}
        {data?.computed && !currency && (
          <Section title="Nothing to report">
            <EmptyState title="Nothing outstanding" icon="check">
              Reports appear once the Google Sheet has unpaid invoices.
            </EmptyState>
          </Section>
        )}
      </div>
    );
  }

  const agingMax = aging ? Math.max(...aging.buckets.map((b) => Number(b.amount))) : 0;
  const weeksMax = fc ? Math.max(...fc.weeks.map(Number)) : 0;
  const forecastTitle = `Cash forecast: next ${forecast?.days ?? 30} days (estimate)`;

  const agingBars = aging && (
    <ul className="space-y-3">
      {aging.buckets.map((b) => (
        <li key={b.key} className="grid grid-cols-[7.5rem_minmax(0,1fr)_auto] items-center gap-3 text-sm sm:grid-cols-[9rem_minmax(0,1fr)_10rem]">
          <span className="text-ink-muted">{b.label}</span>
          <Bar value={Number(b.amount)} max={agingMax} className={b.key === "current" ? "bg-slate-400" : b.key === "1_30" ? "bg-warning-400" : "bg-danger-500"} />
          <span className="text-right tabular-nums text-ink">
            {formatMoney(b.amount, aging.currency)} <span className="text-ink-subtle">({b.count})</span>
          </span>
        </li>
      ))}
    </ul>
  );

  const weekBars = forecast && fc && (
    <ul className="space-y-3">
      {forecast.weeks.map((week, n) => (
        <li key={week.start} className="grid grid-cols-[7.5rem_minmax(0,1fr)_auto] items-center gap-3 text-sm sm:grid-cols-[11rem_minmax(0,1fr)_8rem]">
          <span className="whitespace-nowrap text-ink-muted">
            {formatDate(week.start).replace(/, \d{4}$/, "")} – {formatDate(week.end).replace(/, \d{4}$/, "")}
          </span>
          <Bar value={Number(fc.weeks[n])} max={weeksMax} className="bg-success-500" />
          <span className="text-right tabular-nums text-ink">{formatMoney(fc.weeks[n] ?? "0", fc.currency)}</span>
        </li>
      ))}
    </ul>
  );

  const metric = (label: string, value: ReactNode, explanation: ReactNode) => (
    <div className="space-y-1">
      <p className="text-sm font-medium text-ink-muted">{label}</p>
      <p className="text-2xl font-semibold tabular-nums text-ink">{value}</p>
      <p className="text-sm text-ink-muted">{explanation}</p>
    </div>
  );

  return (
    <div className="space-y-6">
      {header}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label={`Summary figures in ${currency}`}>
        <StatCard
          label="Outstanding"
          icon="document"
          value={aging ? formatMoney(aging.total, currency) : "—"}
          detail={aging ? `${aging.count} unpaid invoice(s)` : undefined}
        />
        <StatCard
          label="Overdue"
          icon="clock"
          tone={kpi && Number(kpi.overdue) > 0 ? "danger" : "neutral"}
          value={kpi ? formatMoney(kpi.overdue, currency) : "—"}
          detail="Past the due date"
        />
        <StatCard
          label="Effectiveness (CEI)"
          icon="check"
          value={dsoC?.cei_percent ? `${dsoC.cei_percent}%` : "Not available"}
          detail={dsoC?.cei_percent ? `Last ${dso?.period_days} days` : "Needs a full tracked period"}
        />
        <StatCard
          label="Expected in 30 days"
          icon="sync"
          tone="success"
          value={kpi ? formatMoney(kpi.expected_30_days, currency) : "—"}
          detail="An estimate, not guaranteed"
        />
      </div>

      <Tabs
        idBase="reports"
        label="Reports"
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "overview", label: "Overview" },
          { key: "aging", label: "Aging" },
          { key: "collections", label: "Collections" },
          { key: "forecast", label: "Forecast" },
        ]}
      />

      <TabPanel idBase="reports" tab={tab} className="space-y-6">
        {tab === "overview" && (
          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="Aging" description="Unpaid invoices by how far past the due date they are." actions={<Calculated at={s?.aging?.computed_at} />}>
              {agingBars ?? <p className="text-sm text-ink-muted">Nothing outstanding.</p>}
            </Section>
            <Section title={forecastTitle} description="Expected payments by week." actions={<Calculated at={forecast?.computed_at} />}>
              {fc ? (
                <div className="space-y-4">
                  <p className="text-sm text-ink">
                    <span className="text-lg font-semibold tabular-nums">{formatMoney(fc.expected, currency)}</span> expected of{" "}
                    {formatMoney(fc.outstanding, currency)} outstanding
                  </p>
                  {weekBars}
                </div>
              ) : (
                <p className="text-sm text-ink-muted">Nothing outstanding.</p>
              )}
            </Section>
            <Section title="Collection performance" headingLevel={2}>
              <div className="grid gap-6 sm:grid-cols-2">
                {metric(
                  "Days sales outstanding (DSO)",
                  dsoC?.dso_days ? `${dsoC.dso_days} days` : "Not available",
                  "How many days of billing are still unpaid. Lower is better.",
                )}
                {metric(
                  "Open promises",
                  kpi ? `${kpi.open_promises}` : "—",
                  kpi ? `${formatMoney(kpi.open_promise_amount, currency)} promised and not yet due or settled.` : "",
                )}
              </div>
              {dso?.status === "insufficient_history" && (
                <div className="mt-4">
                  <Notice tone="info" title="Not enough history yet">
                    {dso.reason}
                  </Notice>
                </div>
              )}
            </Section>
            <Section title="How to read these figures">
              <ul className="list-disc space-y-2 pl-5 text-sm text-ink-muted">
                <li>Paid dates are when a sync first saw PAID in the sheet, not the bank date, so payments can look later than they were.</li>
                <li>&quot;Billed&quot; means first seen by CollectFlow: the sheet has no invoice date.</li>
                <li>Every figure is calculated per currency; amounts in different currencies are never added together.</li>
                <li>The forecast is an estimate from fixed assumptions. It is not a promise of cash.</li>
              </ul>
            </Section>
          </div>
        )}

        {tab === "aging" && aging && s?.aging && (
          <Section title="Aging" count={`${aging.count} invoices`} description={s.aging.note} actions={<Calculated at={s.aging.computed_at} />} flush>
            <div className="border-b border-line px-6 py-5">{agingBars}</div>
            <div className={tableStyles.wrap}>
              <table className={tableStyles.table}>
                <thead>
                  <tr className={tableStyles.headRow}>
                    <th scope="col" className={tableStyles.th}>Bucket</th>
                    <th scope="col" className={tableStyles.th}>What it means</th>
                    <th scope="col" className={tableStyles.thNum}>Invoices</th>
                    <th scope="col" className={tableStyles.thNum}>Amount</th>
                    <th scope="col" className={tableStyles.thNum}>Share</th>
                  </tr>
                </thead>
                <tbody className={tableStyles.body}>
                  {aging.buckets.map((b) => (
                    <tr key={b.key} className={tableStyles.row}>
                      <td className={`${tableStyles.td} font-medium text-ink`}>{b.label}</td>
                      <td className={`${tableStyles.td} text-ink-muted`}>{BUCKET_HELP[b.key] ?? ""}</td>
                      <td className={tableStyles.tdNum}>{b.count}</td>
                      <td className={tableStyles.tdNum}>{formatMoney(b.amount, currency)}</td>
                      <td className={tableStyles.tdNum}>
                        {Number(aging.total) > 0 ? `${Math.round((Number(b.amount) / Number(aging.total)) * 100)}%` : "—"}
                      </td>
                    </tr>
                  ))}
                  <tr className="bg-slate-50/70">
                    <td className={`${tableStyles.td} font-semibold text-ink`} colSpan={2}>
                      Total ({formatMoney(aging.overdue, currency)} overdue)
                    </td>
                    <td className={`${tableStyles.tdNum} font-semibold`}>{aging.count}</td>
                    <td className={`${tableStyles.tdNum} font-semibold`}>{formatMoney(aging.total, currency)}</td>
                    <td className={tableStyles.tdNum}>100%</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Section>
        )}

        {tab === "collections" && (
          <>
            {dso && (
              <Section title={`DSO and CEI (last ${dso.period_days} days)`} actions={<Calculated at={dso.computed_at} />}>
                <div className="space-y-5">
                  {dso.status === "insufficient_history" && (
                    <Notice tone="info" title="Not enough history yet">
                      {dso.reason}
                    </Notice>
                  )}
                  {dsoC && (
                    <div className="grid gap-6 md:grid-cols-2">
                      <div className="space-y-2 rounded-xl border border-line p-4">
                        {metric(
                          "Days sales outstanding (DSO)",
                          dsoC.dso_days ? `${dsoC.dso_days} days` : "—",
                          "How many days of billing are still unpaid. Lower means customers pay sooner.",
                        )}
                        <p className="text-xs text-ink-subtle">
                          Closing receivables ÷ billed in the period × {dso.period_days}. {dsoC.dso_note}
                        </p>
                      </div>
                      <div className="space-y-2 rounded-xl border border-line p-4">
                        {metric(
                          "Collection effectiveness (CEI)",
                          dsoC.cei_percent ? `${dsoC.cei_percent}%` : "—",
                          "The share of collectable receivables actually collected in the period. Higher is better.",
                        )}
                        <p className="text-xs text-ink-subtle">
                          (Opening + billed − closing) ÷ (opening + billed − closing current) × 100. {dsoC.cei_note}
                        </p>
                      </div>
                    </div>
                  )}
                  {dsoC && (
                    <p className="text-xs text-ink-subtle">
                      Inputs: opening {formatMoney(dsoC.inputs.opening_ar, currency)} · billed {formatMoney(dsoC.inputs.billed, currency)} ·
                      closing {formatMoney(dsoC.inputs.closing_ar, currency)} (current {formatMoney(dsoC.inputs.closing_current_ar, currency)}). {dso.note}
                    </p>
                  )}
                </div>
              </Section>
            )}
            {attribution && (
              <Section title="Payments after reminders" description={attribution.note} actions={<Calculated at={attribution.computed_at} />} flush>
                {payments.length === 0 ? (
                  <EmptyState compact title={`No payment seen in the last ${attribution.lookback_days} days`}>
                    {attribution.unmeasured_paid_invoices
                      ? `${attribution.unmeasured_paid_invoices} invoice(s) were already paid when imported, so their date is unknown.`
                      : "Payments appear here when the sheet marks invoices paid."}
                  </EmptyState>
                ) : (
                  <div className={tableStyles.wrap}>
                    <table className={tableStyles.table}>
                      <thead>
                        <tr className={tableStyles.headRow}>
                          <th scope="col" className={tableStyles.th}>Invoice</th>
                          <th scope="col" className={tableStyles.thNum}>Amount</th>
                          <th scope="col" className={tableStyles.th}>Paid seen</th>
                          <th scope="col" className={tableStyles.th}>Last reminder</th>
                        </tr>
                      </thead>
                      <tbody className={tableStyles.body}>
                        {payments.map((p) => (
                          <tr key={p.invoice_id} className={tableStyles.row}>
                            <td className={tableStyles.td}>
                              <Link href={appLink(p.link)} className="font-mono font-medium text-brand-700 hover:underline">
                                {p.external_id}
                              </Link>
                            </td>
                            <td className={tableStyles.tdNum}>{formatMoney(p.amount, p.currency)}</td>
                            <td className={`${tableStyles.td} whitespace-nowrap`}>{formatDateTime(p.paid_seen_at)}</td>
                            <td className={`${tableStyles.td} text-ink-muted`}>
                              {p.after_reminder
                                ? `${p.days_after_reminder} day(s) after the ${p.last_reminder} reminder`
                                : p.last_reminder_at
                                  ? `${formatDateTime(p.last_reminder_at)} (outside the ${attribution.window_days}-day window)`
                                  : "No reminder sent before it"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            )}
          </>
        )}

        {tab === "forecast" && forecast && (
          <>
            <Notice tone="info" title="This is an estimate">
              {forecast.note} It must not be read as guaranteed collections.
            </Notice>
            <div className="grid gap-6 lg:grid-cols-[minmax(0,60fr)_minmax(0,40fr)]">
              <Section title={forecastTitle} actions={<Calculated at={forecast.computed_at} />}>
                {fc ? (
                  <div className="space-y-4">
                    <p className="text-sm text-ink">
                      <span className="text-lg font-semibold tabular-nums">{formatMoney(fc.expected, currency)}</span> expected of{" "}
                      {formatMoney(fc.outstanding, currency)} outstanding
                    </p>
                    {weekBars}
                  </div>
                ) : (
                  <p className="text-sm text-ink-muted">Nothing outstanding.</p>
                )}
              </Section>
              <Section title="Main assumptions">
                <ul className="list-disc space-y-2 pl-5 text-sm text-ink-muted">
                  <li>Each invoice counts with a probability from its aging bucket or the customer&apos;s promise record.</li>
                  <li>Open disputes count as zero{fc && Number(fc.excluded_disputed) > 0 ? ` (${formatMoney(fc.excluded_disputed, currency)} not counted)` : ""}.</li>
                  <li>Unconfirmed &quot;we paid&quot; claims change nothing until the sheet says PAID.</li>
                  <li>From promises: {fc ? formatMoney(fc.promise_expected, currency) : "—"}.</li>
                  <li>The probabilities are fixed for the demonstration, not fitted to this business.</li>
                </ul>
                <p className="mt-3 text-xs text-ink-subtle">The full rules are in docs/ANALYTICS.md.</p>
              </Section>
            </div>
            <Section title="Contributing invoices" count={forecastRows.length} flush>
              {forecastRows.length === 0 ? (
                <EmptyState compact title="No invoices in the forecast">Unpaid invoices appear here.</EmptyState>
              ) : (
                <>
                  <div className={tableStyles.wrap}>
                    <table className={tableStyles.table}>
                      <thead>
                        <tr className={tableStyles.headRow}>
                          <th scope="col" className={tableStyles.th}>Invoice</th>
                          <th scope="col" className={tableStyles.th}>Customer</th>
                          <th scope="col" className={tableStyles.thNum}>Expected</th>
                          <th scope="col" className={tableStyles.thNum}>Amount</th>
                          <th scope="col" className={tableStyles.thNum}>Chance</th>
                          <th scope="col" className={tableStyles.th}>When</th>
                          <th scope="col" className={tableStyles.th}>Why</th>
                        </tr>
                      </thead>
                      <tbody className={tableStyles.body}>
                        {(showAll ? forecastRows : forecastRows.slice(0, 10)).map((r) => (
                          <tr key={r.invoice_id} className={tableStyles.row}>
                            <td className={`${tableStyles.td} whitespace-nowrap`}>
                              <Link href={appLink(r.link)} className="font-mono font-medium text-brand-700 hover:underline">
                                {r.external_id}
                              </Link>
                            </td>
                            <td className={`${tableStyles.td} whitespace-nowrap text-ink-muted`}>{r.client_name}</td>
                            <td className={tableStyles.tdNum}>{formatMoney(r.expected, r.currency)}</td>
                            <td className={`${tableStyles.tdNum} font-normal text-ink-muted`}>{formatMoney(r.amount, r.currency)}</td>
                            <td className={tableStyles.tdNum}>
                              {Math.round(Number(r.probability) * 100)}%
                              {r.low_confidence && <span className="block text-xs font-normal text-ink-subtle">low confidence</span>}
                            </td>
                            <td className={`${tableStyles.td} whitespace-nowrap`}>{r.expected_date ? formatDate(r.expected_date) : "—"}</td>
                            <td className={`${tableStyles.td} min-w-64 text-xs text-ink-muted`}>{r.explanation.join(" ")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {forecastRows.length > 10 && (
                    <div className="border-t border-line px-6 py-3">
                      <button type="button" onClick={() => setShowAll((v) => !v)} className={buttonStyles.link}>
                        {showAll ? "Show fewer" : `Show all ${forecastRows.length}`}
                      </button>
                    </div>
                  )}
                </>
              )}
            </Section>
          </>
        )}
      </TabPanel>
    </div>
  );
}

