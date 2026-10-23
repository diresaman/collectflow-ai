"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card, ErrorMessage, Skeleton } from "@/components/dashboard/ui";
import { getAnalytics, type AnalyticsOverview, type Forecast } from "@/lib/api/analytics";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { appLink } from "@/lib/url";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function Bar({ value, max, className }: { value: number; max: number; className: string }) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-2 w-full rounded bg-slate-100" aria-hidden>
      {value > 0 && <div className={`h-2 rounded ${className}`} style={{ width: `${width}%` }} />}
    </div>
  );
}

function Section({ title, computedAt, children }: { title: string; computedAt?: string; children: React.ReactNode }) {
  return (
    <Card className="space-y-3 p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        {computedAt && <span className="text-xs text-slate-500">Calculated {formatDateTime(computedAt)}</span>}
      </div>
      {children}
    </Card>
  );
}

function ForecastSection({ forecast }: { forecast: Forecast & { computed_at: string } }) {
  const [showAll, setShowAll] = useState(false);
  const rows = showAll ? forecast.invoices : forecast.invoices.slice(0, 8);
  return (
    <Section title={`Cash forecast: next ${forecast.days} days (estimate)`} computedAt={forecast.computed_at}>
      <p className="text-sm text-slate-600">{forecast.note}</p>
      {forecast.currencies.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing outstanding.</p>
      ) : (
        forecast.currencies.map((c) => {
          const max = Math.max(...c.weeks.map(Number));
          return (
            <div key={c.currency} className="space-y-2">
              <p className="text-sm text-slate-800">
                <span className="font-semibold">{formatMoney(c.expected, c.currency)}</span> expected of{" "}
                {formatMoney(c.outstanding, c.currency)} outstanding · from promises {formatMoney(c.promise_expected, c.currency)}
                {Number(c.excluded_disputed) > 0 ? ` · disputed, not counted ${formatMoney(c.excluded_disputed, c.currency)}` : ""}
              </p>
              <ul className="space-y-1">
                {forecast.weeks.map((week, n) => (
                  <li key={week.start} className="grid grid-cols-[9rem_1fr_7rem] items-center gap-3 text-xs text-slate-600">
                    <span>
                      {formatDate(week.start)} – {formatDate(week.end)}
                    </span>
                    <Bar value={Number(c.weeks[n])} max={max} className="bg-emerald-500" />
                    <span className="text-right tabular-nums">{formatMoney(c.weeks[n] ?? "0", c.currency)}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })
      )}
      {forecast.invoices.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-3 font-medium">Invoice</th>
                <th className="py-1 pr-3 font-medium">Expected</th>
                <th className="py-1 pr-3 font-medium">Chance</th>
                <th className="py-1 pr-3 font-medium">When</th>
                <th className="py-1 font-medium">Why</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {rows.map((r) => (
                <tr key={r.invoice_id}>
                  <td className="py-1.5 pr-3 align-top">
                    <Link href={appLink(r.link)} className="font-medium text-indigo-600 hover:underline">
                      {r.external_id}
                    </Link>
                    <div className="text-slate-500">{r.client_name}</div>
                  </td>
                  <td className="py-1.5 pr-3 align-top tabular-nums">
                    {formatMoney(r.expected, r.currency)} <span className="text-slate-500">of {formatMoney(r.amount, r.currency)}</span>
                  </td>
                  <td className="py-1.5 pr-3 align-top tabular-nums">
                    {Math.round(Number(r.probability) * 100)}%{r.low_confidence ? " (low confidence)" : ""}
                  </td>
                  <td className="py-1.5 pr-3 align-top">{r.expected_date ? formatDate(r.expected_date) : "—"}</td>
                  <td className="py-1.5 align-top">{r.explanation.join(" ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {forecast.invoices.length > 8 && (
            <button type="button" onClick={() => setShowAll((v) => !v)} className="mt-2 text-xs font-medium text-indigo-600">
              {showAll ? "Show fewer" : `Show all ${forecast.invoices.length}`}
            </button>
          )}
        </div>
      )}
    </Section>
  );
}

export function AnalyticsView() {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

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
  const dso = s?.dso_cei;
  const attribution = s?.attribution;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Reports &amp; Insights</h1>
        <p className="max-w-3xl text-sm text-slate-600">
          Receivables from the synced sheet, per currency (never added across currencies). Paid dates are when a sync
          first saw PAID, not the bank date. Figures are recalculated after each change and every hour; nothing here
          sends anything.
        </p>
      </div>
      {error && <ErrorMessage message={`Could not load the reports: ${error}`} />}
      {data === null && !error && (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-48" />
        </div>
      )}
      {data && !data.computed && (
        <p className="text-sm text-slate-600">
          Not calculated yet. The worker calculates these figures within the hour or after the next change; you can also use
          &quot;Recalculate scores&quot; on the Customers page.
        </p>
      )}

      {s?.kpis && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Summary figures">
          {s.kpis.currencies.map((c) => (
            <Card key={c.currency} className="space-y-1 p-4 sm:col-span-2 lg:col-span-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{c.currency}</p>
              <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div>
                  <dt className="text-xs text-slate-500">Overdue</dt>
                  <dd className="font-semibold text-slate-900">{formatMoney(c.overdue, c.currency)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Expected in 30 days (estimate)</dt>
                  <dd className="font-semibold text-slate-900">{formatMoney(c.expected_30_days, c.currency)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">DSO</dt>
                  <dd className="font-semibold text-slate-900" title={c.dso_note}>
                    {c.dso_days === null ? "Not enough history" : `${c.dso_days} days`}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Open promises</dt>
                  <dd className="font-semibold text-slate-900">
                    {c.open_promises} · {formatMoney(c.open_promise_amount, c.currency)}
                  </dd>
                </div>
              </dl>
            </Card>
          ))}
        </div>
      )}

      {s?.aging && (
        <Section title="Aging" computedAt={s.aging.computed_at}>
          <p className="text-xs text-slate-500">{s.aging.note}</p>
          {s.aging.currencies.length === 0 ? (
            <p className="text-sm text-slate-500">Nothing outstanding.</p>
          ) : (
            s.aging.currencies.map((c) => {
              const max = Math.max(...c.buckets.map((b) => Number(b.amount)));
              return (
                <div key={c.currency} className="space-y-1">
                  <p className="text-sm font-medium text-slate-900">
                    {c.currency}: {formatMoney(c.total, c.currency)} in {c.count} invoice(s), {formatMoney(c.overdue, c.currency)} overdue
                  </p>
                  {c.buckets.map((b) => (
                    <div key={b.key} className="grid grid-cols-[9rem_1fr_9rem] items-center gap-3 text-xs text-slate-600">
                      <span>{b.label}</span>
                      <Bar value={Number(b.amount)} max={max} className={b.key === "current" ? "bg-slate-400" : "bg-amber-500"} />
                      <span className="text-right tabular-nums">
                        {formatMoney(b.amount, c.currency)} ({b.count})
                      </span>
                    </div>
                  ))}
                </div>
              );
            })
          )}
        </Section>
      )}

      {dso && (
        <Section title={`DSO and CEI (last ${dso.period_days} days)`} computedAt={dso.computed_at}>
          {dso.status === "insufficient_history" && <p className="text-sm text-amber-800">{dso.reason}</p>}
          {dso.currencies.map((c) => (
            <div key={c.currency} className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <p className="font-medium text-slate-900">
                  {c.currency} DSO: {c.dso_days === null ? "—" : `${c.dso_days} days`}
                </p>
                <p className="text-xs text-slate-500">
                  Closing receivables ÷ billed in the period × {dso.period_days}. {c.dso_note}
                </p>
              </div>
              <div>
                <p className="font-medium text-slate-900">
                  {c.currency} CEI: {c.cei_percent === null ? "—" : `${c.cei_percent}%`}
                </p>
                <p className="text-xs text-slate-500">
                  (Opening + billed − closing) ÷ (opening + billed − closing current) × 100. {c.cei_note}
                </p>
              </div>
              <p className="text-xs text-slate-500 sm:col-span-2">
                Opening {formatMoney(c.inputs.opening_ar, c.currency)} · billed {formatMoney(c.inputs.billed, c.currency)} ·
                closing {formatMoney(c.inputs.closing_ar, c.currency)} (current {formatMoney(c.inputs.closing_current_ar, c.currency)})
              </p>
            </div>
          ))}
          <p className="text-xs text-slate-500">{dso.note}</p>
        </Section>
      )}

      {s?.forecast && <ForecastSection forecast={s.forecast} />}

      {attribution && (
        <Section title="Payments after reminders" computedAt={attribution.computed_at}>
          <p className="text-sm text-slate-600">{attribution.note}</p>
          {attribution.payments.length === 0 ? (
            <p className="text-sm text-slate-500">
              No payment seen in the last {attribution.lookback_days} days
              {attribution.unmeasured_paid_invoices
                ? ` (${attribution.unmeasured_paid_invoices} invoice(s) were already paid when imported, so their date is unknown)`
                : ""}
              .
            </p>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {attribution.payments.map((p) => (
                <li key={p.invoice_id} className="flex flex-wrap justify-between gap-2 py-1.5">
                  <Link href={appLink(p.link)} className="font-medium text-indigo-600 hover:underline">
                    {p.external_id}
                  </Link>
                  <span className="text-xs text-slate-600">
                    {formatMoney(p.amount, p.currency)} · paid seen {formatDateTime(p.paid_seen_at)} ·{" "}
                    {p.after_reminder
                      ? `${p.days_after_reminder} day(s) after the ${p.last_reminder} reminder`
                      : p.last_reminder_at
                        ? `last reminder ${formatDateTime(p.last_reminder_at)} (outside the ${attribution.window_days}-day window)`
                        : "no reminder sent before it"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}
    </div>
  );
}
