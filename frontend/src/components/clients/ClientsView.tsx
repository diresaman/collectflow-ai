"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Card, ErrorMessage, Skeleton, Spinner } from "@/components/dashboard/ui";
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
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { queryParam, setQueryParam } from "@/lib/url";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function percent(rate: string | null): string {
  return rate === null ? "no history" : `${Math.round(Number(rate) * 100)}%`;
}

function ScoreBadge({ score, lowConfidence }: { score: number | null; lowConfidence: boolean }) {
  if (score === null) {
    return <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] text-slate-500">not scored yet</span>;
  }
  const tone =
    score >= 60 ? "border-rose-200 bg-rose-50 text-rose-800" : score >= 30 ? "border-amber-200 bg-amber-50 text-amber-800" : "border-slate-200 bg-slate-50 text-slate-700";
  return (
    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${tone}`}>
      Risk {score}/100{lowConfidence ? " · low confidence" : ""}
    </span>
  );
}

function RowButton({ row, selected, onSelect }: { row: WorklistRow; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onSelect}
      className={`block w-full px-4 py-3 text-left hover:bg-slate-50 ${selected ? "bg-indigo-50/60" : ""}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold text-slate-900">
          <span className="mr-1.5 text-slate-400">#{row.rank}</span>
          {row.client_name}
        </span>
        <ScoreBadge score={row.risk_score} lowConfidence={row.low_confidence} />
      </div>
      <p className="mt-0.5 text-xs text-slate-500">
        {formatMoney(row.outstanding, row.currency)} outstanding · priority {Number(row.priority).toLocaleString()} ·{" "}
        {row.oldest_days_overdue > 0 ? `oldest ${row.oldest_days_overdue} days overdue` : "nothing overdue"}
      </p>
      {row.top_factors.length > 0 && (
        <p className="mt-0.5 text-xs text-slate-600">{row.top_factors.map((f) => `${f.label} (+${f.points})`).join(" · ")}</p>
      )}
      <p className="mt-1 text-xs font-medium text-slate-800">Next: {row.next_action.title}</p>
    </button>
  );
}

function ClientPanel({ row, detail }: { row: WorklistRow | null; detail: ClientProfile }) {
  return (
    <div className="space-y-5 p-5">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold text-slate-900">{detail.display_name}</h2>
          <ScoreBadge score={detail.risk_score} lowConfidence={detail.low_confidence} />
        </div>
        <p className="text-sm text-slate-600">
          {detail.email}
          {detail.invoice_names.length > 1 ? ` · invoiced as ${detail.invoice_names.join(", ")}` : ""}
        </p>
        <p className="text-xs text-slate-500">
          Outstanding:{" "}
          {Object.keys(detail.outstanding).length === 0
            ? "nothing"
            : Object.entries(detail.outstanding)
                .map(([currency, amount]) => formatMoney(amount, currency))
                .join(" + ")}
          {detail.computed_at ? ` · score calculated ${formatDateTime(detail.computed_at)}` : ""}
        </p>
      </header>

      {row && (
        <section aria-label="Next action" className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3">
          <h3 className="text-sm font-semibold text-slate-900">Suggested next action ({row.currency})</h3>
          <p className="text-sm text-slate-800">{row.next_action.title}</p>
          {row.next_action.detail && <p className="text-xs text-slate-600">{row.next_action.detail}</p>}
          {row.next_action.who_can_act && <p className="text-xs text-slate-600">Who can act: {row.next_action.who_can_act}</p>}
          {row.next_action.blocked && (
            <p className="text-xs text-amber-800">
              <span className="font-semibold">You cannot do this yourself:</span> {row.next_action.blocked}
            </p>
          )}
          {row.next_action.link && (
            <Link href={row.next_action.link} className="inline-block text-sm font-medium text-indigo-600 hover:underline">
              Open
            </Link>
          )}
          {row.blockers.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-slate-900">What stops chasing</h4>
              <ul className="list-disc pl-5 text-xs text-slate-700">
                {row.blockers.map((b) => (
                  <li key={`${b.invoice_id}:${b.code}`}>
                    {b.invoice_external_id}: {b.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-slate-500">The score only ranks clients. It never drafts, approves, sends or ends a hold.</p>
        </section>
      )}

      <section aria-label="Risk score breakdown">
        <h3 className="mb-2 text-sm font-semibold text-slate-900">Why this score</h3>
        {detail.factors.length === 0 ? (
          <p className="text-sm text-slate-500">Not calculated yet. The worker calculates scores every hour and after changes.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <tbody className="divide-y divide-slate-100">
              {detail.factors.map((f) => (
                <tr key={f.key}>
                  <td className="py-2 pr-3 align-top font-medium text-slate-900">{f.label}</td>
                  <td className="whitespace-nowrap py-2 pr-3 align-top tabular-nums text-slate-900">
                    {f.points} / {f.max}
                  </td>
                  <td className="py-2 align-top text-slate-600">{f.detail}</td>
                </tr>
              ))}
              <tr>
                <td className="py-2 pr-3 font-semibold text-slate-900">Total</td>
                <td className="py-2 pr-3 font-semibold tabular-nums text-slate-900">{detail.risk_score} / 100</td>
                <td />
              </tr>
            </tbody>
          </table>
        )}
        <p className="mt-2 text-xs text-slate-500">
          Payment behaviour: average {detail.avg_days_late === null ? "no measured payment" : `${detail.avg_days_late} days late`}
          {" · "}promises kept {percent(detail.promise_kept_rate)} · reminders answered {percent(detail.response_rate)}
        </p>
      </section>

      <section aria-label="Invoices">
        <h3 className="mb-2 text-sm font-semibold text-slate-900">Invoices</h3>
        <ul className="divide-y divide-slate-100 text-sm">
          {detail.invoices.map((i) => (
            <li key={i.id} className="py-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Link href={i.link} className="font-medium text-indigo-600 hover:underline">
                  {i.external_id}
                </Link>
                <span className="text-slate-700">
                  {formatMoney(i.amount, i.currency)} · due {formatDate(i.due_date)} · {i.status.toLowerCase()}
                  {i.in_sheet ? "" : " · not in the sheet"}
                </span>
              </div>
              {(i.open_promise_id || i.open_dispute_id) && (
                <p className="text-xs text-slate-600">
                  {i.open_promise_id ? "Open promise to pay. " : ""}
                  {i.open_dispute_id && (
                    <Link href={`/disputes?case=${i.open_dispute_id}`} className="text-indigo-600 hover:underline">
                      Open dispute
                    </Link>
                  )}
                </p>
              )}
              {i.blockers.length > 0 && <p className="text-xs text-amber-800">{i.blockers.join(" ")}</p>}
            </li>
          ))}
        </ul>
      </section>

      <PaymentHistory detail={detail} />

      <StatementSection clientId={detail.id} statements={detail.statements} />

      {(detail.open_tasks.length > 0 || detail.disputes.length > 0 || detail.promises.length > 0 || detail.replies.length > 0) && (
        <section aria-label="Related work" className="grid gap-4 sm:grid-cols-2">
          {detail.open_tasks.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-slate-900">Open tasks</h3>
              <ul className="space-y-1 text-sm">
                {detail.open_tasks.map((t) => (
                  <li key={t.id}>
                    <Link href={t.link} className="text-indigo-600 hover:underline">
                      {t.title}
                    </Link>{" "}
                    <span className="text-xs text-slate-500">due {formatDateTime(t.due_at)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {detail.disputes.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-slate-900">Disputes</h3>
              <ul className="space-y-1 text-sm">
                {detail.disputes.map((d) => (
                  <li key={d.id}>
                    <Link href={d.link} className="text-indigo-600 hover:underline">
                      {d.invoice_external_id}
                    </Link>{" "}
                    <span className="text-xs text-slate-500">{d.status.toLowerCase().replace(/_/g, " ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {detail.promises.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-slate-900">Promises to pay</h3>
              <ul className="space-y-1 text-sm">
                {detail.promises.map((p) => (
                  <li key={p.id}>
                    <Link href={p.link} className="text-indigo-600 hover:underline">
                      {p.invoice_external_id}
                    </Link>{" "}
                    <span className="text-xs text-slate-500">
                      {p.status.toLowerCase()} · promised {formatDate(p.promised_date)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {detail.replies.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-slate-900">Recent replies</h3>
              <ul className="space-y-1 text-sm">
                {detail.replies.map((r) => (
                  <li key={r.id}>
                    <Link href={r.link} className="text-indigo-600 hover:underline">
                      {r.invoice_external_id}
                    </Link>{" "}
                    <span className="text-xs text-slate-500">
                      {formatDateTime(r.received_at)} · {r.label ? r.label.toLowerCase().replace(/_/g, " ") : r.state.toLowerCase()}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function PaymentHistory({ detail }: { detail: ClientProfile }) {
  const missing = detail.paid_history.filter((p) => p.paid_seen_on === null).length;
  const promises = detail.promise_summary;
  return (
    <section aria-label="Payment and communication history" className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-900">Payments and communication</h3>
      <p className="text-xs text-slate-600">
        Promises: {promises.kept} kept, {promises.broken} broken, {promises.open} open
        {promises.cancelled ? `, ${promises.cancelled} cancelled` : ""}.
        {promises.kept + promises.broken === 0 ? " No settled promise yet, so there is no kept rate (not 0%)." : ""}
      </p>
      {detail.paid_history.length === 0 ? (
        <p className="text-xs text-slate-500">No paid invoice yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {detail.paid_history.map((p) => (
            <li key={p.id}>
              <Link href={p.link} className="text-indigo-600 hover:underline">
                {p.external_id}
              </Link>{" "}
              <span className="text-xs text-slate-600">
                {formatMoney(p.amount, p.currency)} · due {formatDate(p.due_date)} ·{" "}
                {p.paid_seen_on === null
                  ? "paid date unknown (already paid when imported)"
                  : `paid seen ${formatDate(p.paid_seen_on)}, ${p.days_late} day(s) late`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {missing > 0 && (
        <p className="text-xs text-slate-500">
          Paid dates are when a sync first saw PAID in the sheet, not the bank date; {missing} paid invoice(s) have none.
        </p>
      )}
      {detail.communications.length > 0 && (
        <ul className="space-y-1 text-sm">
          {detail.communications.slice(0, 10).map((c, n) => (
            <li key={`${c.at}:${n}`} className="text-xs text-slate-600">
              <span className="font-medium text-slate-800">{formatDateTime(c.at)}</span> ·{" "}
              {c.type === "sent" ? (c.kind === "STATEMENT" ? "statement sent" : `reminder sent (${c.tone.toLowerCase()})`) : "reply received"}{" "}
              ·{" "}
              <Link href={c.link} className="text-indigo-600 hover:underline">
                {c.invoice_external_id}
              </Link>{" "}
              · {c.subject}
            </li>
          ))}
        </ul>
      )}
    </section>
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
      setMessage("Statement drafted. Open its first invoice to review it; an approver who did not edit it approves it.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      setReload((n) => n + 1);
    }
  };

  return (
    <section aria-label="Consolidated statement" className="space-y-2 rounded-md border border-slate-200 p-3">
      <h3 className="text-sm font-semibold text-slate-900">Consolidated statement</h3>
      {preview === null && !error && <Skeleton className="h-10" />}
      {preview && (
        <>
          {preview.lines.length > 0 && (
            <ul className="text-xs text-slate-700">
              {preview.lines.map((line) => (
                <li key={line.invoice_id}>
                  {line.external_id}: {formatMoney(line.amount, line.currency)}, due {formatDate(line.due_date)}
                  {line.replaces_draft_id ? " (replaces its unsent draft)" : ""}
                </li>
              ))}
            </ul>
          )}
          {Object.keys(preview.totals).length > 0 && (
            <p className="text-xs font-medium text-slate-900">
              Total:{" "}
              {Object.entries(preview.totals)
                .map(([currency, amount]) => formatMoney(amount, currency))
                .join(" + ")}
            </p>
          )}
          {preview.excluded.length > 0 && (
            <details className="text-xs text-slate-600">
              <summary className="cursor-pointer">Not included ({preview.excluded.length})</summary>
              <ul className="mt-1 list-disc pl-5">
                {preview.excluded.map((line) => (
                  <li key={line.invoice_id}>
                    {line.external_id}: {line.reasons?.join(" ")}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {preview.can_prepare ? (
            <button
              type="button"
              onClick={() => void prepare()}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-60"
            >
              {busy && <Spinner />}
              Prepare statement draft
            </button>
          ) : (
            <p className="text-xs text-slate-600">{preview.reason}</p>
          )}
          <p className="text-xs text-slate-500">
            Gemini writes only the wording; CollectFlow writes the invoice list and totals. Nothing is sent until an
            approver approves it and someone sends it.
          </p>
        </>
      )}
      {message && <p className="text-xs text-emerald-800">{message}</p>}
      {error && <ErrorMessage message={error} />}
      {statements.length > 0 && (
        <ul className="space-y-1 border-t border-slate-100 pt-2 text-xs text-slate-600">
          {statements.map((s) => (
            <li key={s.id}>
              <Link href={s.link} className="font-medium text-indigo-600 hover:underline">
                {s.subject}
              </Link>{" "}
              · {s.lines} invoices · {s.status.toLowerCase().replace(/_/g, " ")} ·{" "}
              {s.sent_at ? `sent ${formatDateTime(s.sent_at)}` : `drafted ${formatDateTime(s.generated_at)}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ClientDetailLoader({ clientId, row }: { clientId: string; row: WorklistRow | null }) {
  const [detail, setDetail] = useState<ClientProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getClient(clientId, controller.signal)
      .then(setDetail)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [clientId]);

  if (error) return <div className="p-5"><ErrorMessage message={`Could not load the client: ${error}`} /></div>;
  if (!detail) return <div className="space-y-2 p-5"><Skeleton className="h-8" /><Skeleton className="h-40" /></div>;
  return <ClientPanel row={row} detail={detail} />;
}

export function ClientsView() {
  const [data, setData] = useState<Worklist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(() => queryParam("client"));
  const [selectedCurrency, setSelectedCurrency] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [queued, setQueued] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getWorklist(controller.signal)
      .then((worklist) => {
        setData(worklist);
        setError(null);
        const first = worklist.groups[0]?.rows[0];
        setSelected((current) => current ?? first?.client_id ?? null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [reload]);

  const select = useCallback((row: WorklistRow) => {
    setSelected(row.client_id);
    setSelectedCurrency(row.currency);
    setQueryParam("client", row.client_id);
  }, []);

  const recompute = () => {
    setQueueing(true);
    requestRecompute()
      .then(() => setQueued("Recalculation queued. The worker runs it within a few seconds; refresh to see it."))
      .catch((err: unknown) => setQueued(`Could not queue it: ${errorText(err)}`))
      .finally(() => setQueueing(false));
  };

  const rows = data?.groups.flatMap((g) => g.rows) ?? [];
  const row =
    rows.find((r) => r.client_id === selected && (selectedCurrency === null || r.currency === selectedCurrency)) ??
    rows.find((r) => r.client_id === selected) ??
    null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Clients</h1>
          <p className="max-w-3xl text-sm text-slate-600">
            Who to look at first: risk score (0–100, from records only, no AI) × amount outstanding, ranked separately in
            each currency. The score is decision support. It never sends anything or overrides a hold, a dispute or an
            approval rule.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setReload((n) => n + 1)}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={recompute}
            disabled={queueing}
            className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            {queueing && <Spinner />}
            Recalculate scores
          </button>
        </div>
      </div>
      {queued && <p className="text-sm text-slate-600">{queued}</p>}
      {data && data.urgent.length > 0 && (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="font-semibold">Handle these first, whatever the ranking:</span>{" "}
          {data.urgent.map((u) => `${u.title.toLowerCase()} (${u.count})`).join(", ")}.{" "}
          <Link href="/today" className="font-medium underline">
            Open Today
          </Link>
        </p>
      )}
      {error && <ErrorMessage message={`Could not load the worklist: ${error}${data ? " Showing the last loaded list." : ""}`} />}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
        <Card className="max-h-[75vh] overflow-auto">
          {data === null ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
          ) : data.groups.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-500">No client has an unpaid invoice in the sheet.</p>
          ) : (
            data.groups.map((group) => (
              <div key={group.currency}>
                <h2 className="sticky top-0 border-b border-slate-200 bg-white px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {group.currency} · ranked within {group.currency} only
                </h2>
                <ul className="divide-y divide-slate-100" role="listbox" aria-label={`Clients owing ${group.currency}`}>
                  {group.rows.map((r) => (
                    <li key={`${r.client_id}:${r.currency}`}>
                      <RowButton row={r} selected={row === r} onSelect={() => select(r)} />
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
          {data && data.scores_computed_at && (
            <p className="border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
              Scores calculated {formatDateTime(data.scores_computed_at)} or later.
            </p>
          )}
          {data && data.unscored.length > 0 && (
            <p className="border-t border-slate-100 px-4 py-2 text-xs text-amber-800">
              {data.unscored.length} client(s) not scored yet; they rank last until the next calculation.
            </p>
          )}
        </Card>
        <Card>
          {selected ? (
            <ClientDetailLoader key={`${selected}:${reload}`} clientId={selected} row={row} />
          ) : (
            <p className="p-6 text-sm text-slate-500">{data && rows.length === 0 ? "Nothing to show." : "Select a client."}</p>
          )}
        </Card>
      </div>
    </div>
  );
}
