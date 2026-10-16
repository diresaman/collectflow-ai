"use client";

import { useEffect, type ReactNode } from "react";

import { ACTIVE_STATUSES, type FollowUp } from "@/lib/api/followups";
import type { Invoice } from "@/lib/api/invoices";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import { DeliveryPanel } from "./DeliveryPanel";
import { DraftEditor } from "./DraftEditor";
import { FollowUpStatusBadge, ToneBadge } from "./FollowUpBadges";
import { ErrorMessage, Icon, Spinner } from "./ui";

export type GenerationState = { running: boolean; regenerate: boolean; error: string | null };

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-slate-900">{children}</dd>
    </div>
  );
}

function AiInsight({ followUp }: { followUp: FollowUp }) {
  return (
    <div className="space-y-2 rounded-lg border border-indigo-100 bg-indigo-50/60 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="sparkles" className="h-4 w-4 text-indigo-600" />
        <span className="text-sm font-medium text-slate-900">AI reasoning</span>
        <ToneBadge tone={followUp.tone} />
      </div>
      <p className="text-sm text-slate-700">{followUp.reasoning}</p>
      <p className="text-sm text-slate-700">
        <span className="font-medium">Suggested next step:</span> {followUp.recommended_action}
      </p>
    </div>
  );
}

function ReadOnlyMessage({ followUp }: { followUp: FollowUp }) {
  return (
    <div className="space-y-3 rounded-lg border border-slate-200 p-4">
      <p className="text-sm font-semibold text-slate-900">{followUp.subject}</p>
      <p className="whitespace-pre-line text-sm leading-6 text-slate-700">{followUp.body}</p>
    </div>
  );
}

function History({ followUps }: { followUps: FollowUp[] }) {
  if (followUps.length === 0) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-900">History</h3>
      <ul className="space-y-2">
        {followUps.map((followUp) => (
          <li key={followUp.id}>
            <details className="group rounded-lg border border-slate-200">
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2.5 text-sm">
                <FollowUpStatusBadge status={followUp.status} />
                <span className="min-w-0 flex-1 truncate text-slate-700">{followUp.subject}</span>
                <span className="text-xs text-slate-400">{formatDateTime(followUp.generated_at)}</span>
              </summary>
              <div className="space-y-2 border-t border-slate-100 px-3 py-3">
                <ToneBadge tone={followUp.tone} />
                <p className="whitespace-pre-line text-sm leading-6 text-slate-600">{followUp.body}</p>
              </div>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function FollowUpDrawer({
  invoice,
  followUps,
  generation,
  onAnalyze,
  onChanged,
  onClose,
}: {
  invoice: Invoice;
  /** Follow-ups for this invoice, newest first. */
  followUps: FollowUp[];
  generation: GenerationState | null;
  onAnalyze: (regenerate: boolean) => void;
  onChanged: (followUp: FollowUp) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const active = followUps.find((followUp) => ACTIVE_STATUSES.includes(followUp.status)) ?? null;
  // Keep showing the latest reminder after delivery finishes (sent or failed).
  const latest = followUps[0] ?? null;
  const current = active ?? (latest && (latest.status === "SENT" || latest.status === "FAILED") ? latest : null);
  const history = followUps.filter((followUp) => followUp !== current);
  const canDraftNew = active === null && invoice.is_overdue;
  const running = generation?.running ?? false;

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-labelledby="follow-up-title">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} aria-hidden="true" />
      <div className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col bg-white shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-indigo-600">AI follow-up</p>
            <h2 id="follow-up-title" className="mt-1 text-lg font-semibold text-slate-900">
              {invoice.client_name}
            </h2>
            <p className="font-mono text-xs text-slate-500">{invoice.external_id}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            autoFocus
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            aria-label="Close"
          >
            <Icon name="close" />
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-6">
          <dl className="grid grid-cols-3 gap-4 rounded-lg bg-slate-50 p-4">
            <Fact label="Amount">{formatMoney(invoice.amount, invoice.currency)}</Fact>
            <Fact label="Due">{formatDate(invoice.due_date)}</Fact>
            <Fact label="Overdue">
              {invoice.is_overdue ? (
                <span className="text-rose-600">
                  {invoice.days_overdue} {invoice.days_overdue === 1 ? "day" : "days"}
                </span>
              ) : (
                <span className="text-slate-500">No</span>
              )}
            </Fact>
          </dl>

          {running && (
            <div className="flex items-center gap-3 rounded-lg border border-indigo-100 bg-indigo-50/60 p-4 text-sm text-indigo-700">
              <Spinner className="h-5 w-5" />
              {generation?.regenerate ? "Regenerating the reminder with Gemini…" : "Gemini is drafting a reminder…"}
            </div>
          )}

          {generation?.error && !running && (
            <div className="space-y-3">
              <ErrorMessage message={generation.error} />
              <button
                type="button"
                onClick={() => onAnalyze(generation.regenerate)}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
              >
                Retry
              </button>
            </div>
          )}

          {current === null && !running && !generation?.error && (
            <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center">
              {invoice.is_overdue ? (
                <>
                  <p className="text-sm font-medium text-slate-900">No active reminder</p>
                  <p className="mt-1 text-sm text-slate-500">
                    Gemini drafts a reminder from the invoice facts. You review it before anything happens.
                  </p>
                  <button
                    type="button"
                    onClick={() => onAnalyze(false)}
                    className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500"
                  >
                    <Icon name="sparkles" className="h-4 w-4" />
                    Analyze with AI
                  </button>
                </>
              ) : (
                <p className="text-sm text-slate-500">
                  Reminders can only be generated for unpaid invoices that are past their due date.
                </p>
              )}
            </div>
          )}

          {current && (
            <section className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <FollowUpStatusBadge status={current.status} />
                <span className="text-xs text-slate-500">Generated {formatDateTime(current.generated_at)}</span>
              </div>
              {current.invoice_changed_fields.length > 0 && (
                <p role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                  <span className="font-semibold">Invoice changed since this reminder was generated</span> (
                  {current.invoice_changed_fields.join(", ")}).{" "}
                  {current.status === "DRAFT"
                    ? "It cannot be approved as is; regenerate or reject it."
                    : "It can no longer be sent. Approved reminders cannot currently be withdrawn, so this invoice will not get a new reminder until that is resolved."}
                </p>
              )}
              <AiInsight followUp={current} />
              {current.status === "DRAFT" ? (
                <DraftEditor
                  key={`${current.id}:${current.updated_at}`}
                  followUp={current}
                  regenerating={running}
                  onChanged={onChanged}
                  onRegenerate={() => onAnalyze(true)}
                />
              ) : (
                <>
                  <ReadOnlyMessage followUp={current} />
                  {current.status === "APPROVED" && (
                    <p className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                      Approved{current.approved_at ? ` on ${formatDateTime(current.approved_at)}` : ""}.{" "}
                      <span className="font-medium">Not sent yet</span> — review the details below before sending.
                    </p>
                  )}
                  <DeliveryPanel key={`${current.id}:${current.status}`} followUp={current} onChanged={() => onChanged(current)} />
                </>
              )}
              {canDraftNew && current.status !== "DRAFT" && !running && (
                <button
                  type="button"
                  onClick={() => onAnalyze(false)}
                  className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-medium text-indigo-700 shadow-sm hover:bg-indigo-50"
                >
                  <Icon name="sparkles" className="h-4 w-4" />
                  Draft a new reminder
                </button>
              )}
            </section>
          )}

          <History followUps={history} />
        </div>
      </div>
    </div>
  );
}
