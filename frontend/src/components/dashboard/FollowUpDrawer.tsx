"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { getInvoiceHistory, type HistoryEvent } from "@/lib/api/cases";
import { ACTIVE_STATUSES, type FollowUp, type StatementInfo } from "@/lib/api/followups";
import type { Invoice } from "@/lib/api/invoices";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { REMINDER_STATUS } from "@/lib/status";
import { links } from "@/lib/url";

import { CasePanel } from "./CasePanel";
import { DeliveryPanel } from "./DeliveryPanel";
import { DraftEditor } from "./DraftEditor";
import { FollowUpStatusBadge, ToneBadge } from "./FollowUpBadges";
import { BLOCKED_REASONS, SequencePanel } from "./SequencePanel";
import {
  Drawer,
  ErrorMessage,
  Icon,
  KeyValues,
  Notice,
  Skeleton,
  Spinner,
  StatusPill,
  TabPanel,
  Tabs,
  buttonStyles,
} from "./ui";

export type GenerationState = { running: boolean; regenerate: boolean; error: string | null };

type DrawerTab = "overview" | "reminders" | "activity" | "related";

/** Reminder states that need a person now: the drawer opens on the Reminders tab for these. */
const NEEDS_ACTION: readonly FollowUp["status"][] = ["DRAFT", "APPROVED", "SENDING", "DELIVERY_UNKNOWN"];

function AiInsight({ followUp }: { followUp: FollowUp }) {
  return (
    <div className="space-y-2 rounded-xl border border-brand-100 bg-brand-50/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="sparkles" className="h-4 w-4 text-brand-600" />
        <span className="text-sm font-semibold text-ink">AI suggestion</span>
        <ToneBadge tone={followUp.tone} />
      </div>
      <p className="text-sm text-ink-muted">{followUp.reasoning}</p>
      <p className="text-sm text-ink-muted">
        <span className="font-medium text-ink">Suggested next step:</span> {followUp.recommended_action}
      </p>
      <p className="text-xs text-ink-subtle">The backend chose the tone from the invoice; a person reviews every draft.</p>
    </div>
  );
}

function ReadOnlyMessage({ followUp }: { followUp: FollowUp }) {
  return (
    <div className="space-y-3 rounded-xl border border-line p-4">
      <p className="text-xs text-ink-subtle">To {followUp.recipient_email}</p>
      <p className="text-sm font-semibold text-ink">{followUp.subject}</p>
      <p className="whitespace-pre-line text-sm leading-6 text-ink-muted">{followUp.body}</p>
    </div>
  );
}

function ReminderHistory({ followUps }: { followUps: FollowUp[] }) {
  if (followUps.length === 0) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold text-ink">Earlier reminders</h3>
      <ul className="space-y-2">
        {followUps.map((followUp) => (
          <li key={followUp.id}>
            <details className="group rounded-lg border border-line">
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                <Icon name="chevron" className="h-3.5 w-3.5 text-ink-subtle transition-transform group-open:rotate-90" />
                <FollowUpStatusBadge status={followUp.status} />
                <span className="min-w-0 flex-1 truncate text-ink">{followUp.subject}</span>
                <span className="text-xs text-ink-subtle">{formatDateTime(followUp.generated_at)}</span>
              </summary>
              <div className="space-y-2 border-t border-slate-100 px-3 py-3">
                <ToneBadge tone={followUp.tone} />
                <p className="whitespace-pre-line text-sm leading-6 text-ink-muted">{followUp.body}</p>
              </div>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Whether the invoice is being chased right now, and every reason it is not (from the invoice's own data). */
function ChasingSummary({ invoice, draftBlocked }: { invoice: Invoice; draftBlocked: string | null }) {
  if (invoice.status === "PAID") {
    return <Notice tone="success" title="Paid">The Google Sheet shows this invoice as paid. No reminders are prepared.</Notice>;
  }
  if (!invoice.in_sheet) {
    return <Notice tone="neutral" title="Not chased: no longer in the sheet">An invoice removed from the sheet gets no reminders.</Notice>;
  }
  if (invoice.holds.length > 0) {
    return (
      <Notice
        tone="warning"
        title={
          invoice.open_dispute
            ? "Chasing is stopped: the invoice is disputed"
            : `Chasing is stopped by ${invoice.holds.length === 1 ? "a hold" : `${invoice.holds.length} holds`}`
        }
      >
        <ul className="mt-1 space-y-1">
          {invoice.holds.map((hold) => (
            <li key={hold.id}>
              <span className="font-medium">{hold.reason_display}.</span> {hold.release_rule}{" "}
              {hold.reply_id && (
                <Link href={links.reply(hold.reply_id)} className="font-medium underline">
                  Review the reply
                </Link>
              )}
              {hold.dispute_id && (
                <Link href={links.dispute(hold.dispute_id)} className="font-medium underline">
                  Open the dispute
                </Link>
              )}
            </li>
          ))}
        </ul>
        {invoice.holds.length > 1 && <p className="mt-1">Chasing resumes only when every hold has ended.</p>}
      </Notice>
    );
  }
  if (draftBlocked) return <Notice tone="warning" title="Reminders are held">{draftBlocked}</Notice>;
  if (!invoice.is_overdue) {
    return <Notice tone="info" title="Not due yet">Reminders start after the due date, following the reminder schedule.</Notice>;
  }
  return <Notice tone="success" title="Chasing allowed">Reminders follow the schedule. Each one waits for a person to approve and send it.</Notice>;
}

function InvoiceActivity({ invoiceId, refreshKey }: { invoiceId: string; refreshKey: number }) {
  const [events, setEvents] = useState<HistoryEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getInvoiceHistory(invoiceId, controller.signal)
      .then(setEvents)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Unexpected error.");
      });
    return () => controller.abort();
  }, [invoiceId, refreshKey]);
  if (error) return <ErrorMessage message={`Could not load the history: ${error}`} />;
  if (events === null) return <Skeleton className="h-32" />;
  if (events.length === 0) return <p className="text-sm text-ink-muted">Nothing recorded yet.</p>;
  return (
    <section aria-label="Activity" className="space-y-3">
      <h3 className="text-sm font-semibold text-ink">History</h3>
      <p className="text-xs text-ink-subtle">Every sync, draft, approval, send, reply and hold change, newest first. History cannot be edited.</p>
      <ol className="relative space-y-4 border-l border-line pl-5">
        {events.map((event) => (
          <li key={event.id} className="relative">
            <span className="absolute top-1.5 -left-6.25 h-2 w-2 rounded-full bg-slate-300 ring-4 ring-white" aria-hidden="true" />
            <p className="text-sm text-ink">{event.type_label}</p>
            <p className="text-xs text-ink-subtle">
              {formatDateTime(event.occurred_at)}
              {event.actor_username ? ` · by ${event.actor_username}` : event.actor_type !== "USER" ? ` · ${event.actor_type.toLowerCase()}` : ""}
            </p>
          </li>
        ))}
      </ol>
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
  const active = followUps.find((followUp) => ACTIVE_STATUSES.includes(followUp.status)) ?? null;
  // Keep showing the latest reminder after delivery finishes (sent or failed).
  const latest = followUps[0] ?? null;
  const current = active ?? (latest && (latest.status === "SENT" || latest.status === "FAILED") ? latest : null);
  const history = followUps.filter((followUp) => followUp !== current);
  const canDraftNew = active === null && invoice.is_overdue;
  const running = generation?.running ?? false;
  // Until the user picks a tab, open on Reminders when a reminder needs a person (or is being drafted). Derived, so
  // it is right even when the reminders arrive after the drawer opened from a link.
  // Once Reminders has been shown it stays, so finishing an action (e.g. a send) never switches the tab away.
  const [chosen, setChosen] = useState<DrawerTab | null>(null);
  const needsReminders = running || Boolean(current && NEEDS_ACTION.includes(current.status));
  if (chosen === null && needsReminders) setChosen("reminders");
  const tab: DrawerTab = chosen ?? (needsReminders ? "reminders" : "overview");
  const setTab = setChosen;
  // Manual drafting writes the invoice's next sequence step (or re-drafts a failed one).
  const enrollment = invoice.enrollment;
  const nextStep = enrollment ? (enrollment.pending_step ?? enrollment.next_step) : 1;
  const draftLabel = `Draft step ${nextStep} with AI`;
  const draftBlocked =
    enrollment?.status === "PAUSED"
      ? enrollment.pause_reason === "history_review"
        ? "An approver must confirm where this invoice stands in its reminder schedule first (Reminders tab)."
        : "Reminders are paused. Resume them to draft the next step."
      : enrollment?.status === "COMPLETED"
        ? "All steps of this invoice's reminder schedule are done."
        : enrollment?.blocked_reason === "delivery_unknown"
          ? (BLOCKED_REASONS.delivery_unknown ?? null)
          : null;
  const refreshKey = followUps.length + (followUps[0] ? Date.parse(followUps[0].updated_at) : 0);
  const draftAllowed = canDraftNew && !draftBlocked && invoice.holds.length === 0 && !running;

  const statusPill =
    invoice.status === "PAID" ? (
      <StatusPill label="Paid" tone="good" />
    ) : invoice.is_overdue ? (
      <StatusPill label={`Overdue · ${invoice.days_overdue} ${invoice.days_overdue === 1 ? "day" : "days"}`} tone="bad" />
    ) : (
      <StatusPill label="Unpaid" tone="neutral" />
    );

  const summary = (
    <div className="space-y-4">
      <KeyValues
        columns={4}
        items={[
          { label: "Amount", value: <span className="tabular-nums">{formatMoney(invoice.amount, invoice.currency)}</span> },
          { label: "Due date", value: formatDate(invoice.due_date) },
          { label: "Payment status", value: statusPill },
          { label: "Reminder", value: current ? <FollowUpStatusBadge status={current.status} /> : <span className="text-ink-subtle">None active</span> },
        ]}
      />
      <ChasingSummary invoice={invoice} draftBlocked={draftBlocked} />
    </div>
  );

  let nextAction: ReactNode;
  if (current && NEEDS_ACTION.includes(current.status)) {
    const label = { DRAFT: "Review the draft", APPROVED: "Review and send", SENDING: "See the send in progress", DELIVERY_UNKNOWN: "Check the delivery" }[
      current.status as "DRAFT" | "APPROVED" | "SENDING" | "DELIVERY_UNKNOWN"
    ];
    nextAction = (
      <button type="button" onClick={() => setTab("reminders")} className={buttonStyles.primary}>
        {label}
      </button>
    );
  } else if (draftAllowed) {
    nextAction = (
      <button type="button" onClick={() => onAnalyze(false)} className={buttonStyles.primary}>
        <Icon name="sparkles" className="h-4 w-4" />
        {draftLabel}
      </button>
    );
  } else {
    nextAction = (
      <p className="text-sm text-ink-muted">
        {invoice.status === "PAID"
          ? "Nothing to do: the invoice is paid."
          : invoice.holds.length > 0
            ? "End the holds above first; each one says who can end it."
            : draftBlocked ?? (invoice.is_overdue ? "Nothing to do now." : "Nothing to do until the invoice is overdue.")}
      </p>
    );
  }

  return (
    <Drawer
      eyebrow="Invoice"
      title={
        <>
          <span className="font-mono">{invoice.external_id}</span> · {invoice.client_name}
        </>
      }
      subtitle={invoice.client_email}
      onClose={onClose}
      summary={summary}
      bodyKey={tab}
      tabs={
        <Tabs
          idBase="invoice"
          label="Invoice details"
          value={tab}
          onChange={setTab}
          tabs={[
            { key: "overview", label: "Overview" },
            { key: "reminders", label: "Reminders", count: followUps.length || undefined },
            { key: "activity", label: "Activity" },
            { key: "related", label: "Related" },
          ]}
        />
      }
    >
      <TabPanel idBase="invoice" tab={tab} className="space-y-6">
        {tab === "overview" && (
          <>
            <section className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">Invoice facts</h3>
              <KeyValues
                items={[
                  { label: "Invoice", value: <span className="font-mono">{invoice.external_id}</span> },
                  { label: "Customer", value: invoice.client_name },
                  { label: "Currency", value: invoice.currency },
                  { label: "Days overdue", value: invoice.is_overdue ? invoice.days_overdue : "Not overdue" },
                  { label: "In the sheet", value: invoice.in_sheet ? "Yes" : "No" },
                  { label: "Last synced", value: formatDateTime(invoice.last_synced_at) },
                ]}
              />
            </section>
            <section className="space-y-2 border-t border-line pt-5">
              <h3 className="text-sm font-semibold text-ink">Collection status</h3>
              <p className="text-sm text-ink-muted">
                {enrollment
                  ? `Reminder schedule: step ${enrollment.last_completed_step} of ${enrollment.total_steps} done${
                      enrollment.pending_step ? `, step ${enrollment.pending_step} in progress` : ""
                    }${enrollment.next_action_at && !enrollment.pending_step ? `, step ${enrollment.next_step} due ${formatDateTime(enrollment.next_action_at)}` : ""}.`
                  : "Not on a reminder schedule yet. Overdue unpaid invoices are enrolled after the next sync."}
              </p>
              {current && (
                <p className="text-sm text-ink-muted">
                  Latest reminder: {REMINDER_STATUS[current.status].label.toLowerCase()} ({current.subject}).
                </p>
              )}
            </section>
            <section className="space-y-3 border-t border-line pt-5">
              <h3 className="text-sm font-semibold text-ink">Next step</h3>
              {nextAction}
            </section>
          </>
        )}

        {tab === "reminders" && (
          <>
            <SequencePanel invoiceId={invoice.id} refreshKey={refreshKey} />

            {running && (
              <div className="flex items-center gap-3 rounded-xl border border-brand-100 bg-brand-50/60 p-4 text-sm text-brand-800" role="status">
                <Spinner className="h-5 w-5" />
                {generation?.regenerate ? "Rewriting the reminder with Gemini…" : "Gemini is drafting a reminder…"}
              </div>
            )}

            {generation?.error && !running && (
              <div className="space-y-3">
                <ErrorMessage message={generation.error} />
                <button type="button" onClick={() => onAnalyze(generation.regenerate)} className={buttonStyles.secondary}>
                  Retry
                </button>
              </div>
            )}

            {current === null && !running && !generation?.error && (
              <div className="rounded-xl border border-dashed border-line-strong p-6 text-center">
                {invoice.is_overdue ? (
                  <>
                    <p className="text-sm font-semibold text-ink">No active reminder</p>
                    <p className="mx-auto mt-1 max-w-sm text-sm text-ink-muted">
                      Gemini drafts the next reminder step from the invoice facts, in the tone the step sets. You review it
                      before anything happens.
                    </p>
                    {draftBlocked || invoice.holds.length > 0 ? (
                      <p className="mt-4 text-sm text-ink-muted">{draftBlocked ?? "Chasing is on hold (see above)."}</p>
                    ) : (
                      <button type="button" onClick={() => onAnalyze(false)} className={`mt-4 ${buttonStyles.primary}`}>
                        <Icon name="sparkles" className="h-4 w-4" />
                        {draftLabel}
                      </button>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-ink-muted">Reminders can only be drafted for unpaid invoices that are past their due date.</p>
                )}
              </div>
            )}

            {current && (
              <section className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-ink">{current.kind === "STATEMENT" ? "Consolidated statement" : "Current reminder"}</h3>
                    <FollowUpStatusBadge status={current.status} />
                    {current.step && <span className="text-xs font-medium text-ink-muted">Step {current.step.order}</span>}
                    {current.threaded && <span className="text-xs text-ink-subtle">Reply in the earlier Gmail thread</span>}
                  </div>
                  <span className="text-xs text-ink-subtle">Drafted {formatDateTime(current.generated_at)}</span>
                </div>
                {current.invoice_changed_fields.length > 0 && (
                  <Notice tone="warning" role="alert" title="Invoice changed since this reminder was written">
                    ({current.invoice_changed_fields.join(", ")}).{" "}
                    {current.status === "DRAFT"
                      ? "It cannot be approved as is; rewrite or reject it."
                      : "It can no longer be sent. Withdraw the approval below, then draft a new reminder."}
                  </Notice>
                )}
                {current.statement && <StatementLines statement={current.statement} />}
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
                      <Notice tone="info" title="Approved, not sent yet">
                        Approved{current.approved_at ? ` on ${formatDateTime(current.approved_at)}` : ""}
                        {current.approved_by ? ` by ${current.approved_by}` : ""}. Review the details below before sending.
                      </Notice>
                    )}
                    <DeliveryPanel key={`${current.id}:${current.status}`} followUp={current} onChanged={() => onChanged(current)} />
                  </>
                )}
                {canDraftNew && current.status !== "DRAFT" && !running && (draftBlocked || invoice.holds.length > 0) && (
                  <p className="text-sm text-ink-muted">{draftBlocked ?? "Chasing is on hold (see above)."}</p>
                )}
                {canDraftNew && current.status !== "DRAFT" && draftAllowed && (
                  <button type="button" onClick={() => onAnalyze(false)} className={buttonStyles.secondary}>
                    <Icon name="sparkles" className="h-4 w-4 text-brand-600" />
                    {draftLabel}
                  </button>
                )}
              </section>
            )}

            <ReminderHistory followUps={history} />
          </>
        )}

        {tab === "activity" && <InvoiceActivity invoiceId={invoice.id} refreshKey={refreshKey} />}

        {tab === "related" && <CasePanel invoice={invoice} compact />}
      </TabPanel>
    </Drawer>
  );
}

function StatementLines({ statement }: { statement: StatementInfo }) {
  const changed = statement.lines.filter((line) => line.invoice_changed_fields.length > 0);
  return (
    <section aria-label="Statement lines" className="space-y-3 rounded-xl border border-line p-4">
      <h3 className="text-sm font-semibold text-ink">Invoices in this statement</h3>
      <ul className="divide-y divide-slate-100 text-sm">
        {statement.lines.map((line) => (
          <li key={line.invoice_id} className="flex justify-between gap-3 py-2">
            <Link href={links.invoice(line.invoice_id)} className="font-mono font-medium text-brand-700 hover:underline">
              {line.external_id}
            </Link>
            <span className="text-ink-muted">
              <span className="tabular-nums text-ink">{formatMoney(line.amount, line.currency)}</span> · due {formatDate(line.due_date)}
              {line.invoice_changed_fields.length > 0 && <span className="ml-1 font-medium text-warning-800">· changed</span>}
            </span>
          </li>
        ))}
      </ul>
      <p className="flex justify-between text-sm font-semibold text-ink">
        <span>Total</span>
        <span className="tabular-nums">
          {Object.entries(statement.totals)
            .map(([currency, amount]) => formatMoney(amount, currency))
            .join(" + ")}
        </span>
      </p>
      {changed.length > 0 && (
        <Notice tone="warning" role="alert">
          {changed.map((line) => line.external_id).join(", ")} changed since the statement was prepared: it cannot be
          approved or sent. Reject it and prepare a new one from the customer&apos;s page.
        </Notice>
      )}
      <p className="text-xs text-ink-subtle">
        The lines and totals were written by CollectFlow, not the AI, and must stay as they are in the text. A statement
        always needs an approver who did not edit it. Each invoice is checked again before it is sent.
      </p>
    </section>
  );
}
