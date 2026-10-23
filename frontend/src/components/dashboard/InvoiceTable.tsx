import { useState } from "react";

import type { FollowUp } from "@/lib/api/followups";
import type { Invoice } from "@/lib/api/invoices";
import { formatDate, formatMoney } from "@/lib/format";
import { REMINDER_STATUS } from "@/lib/status";

import type { Loadable } from "./types";
import {
  EmptyState,
  ErrorMessage,
  Icon,
  Section,
  SegmentedFilter,
  Skeleton,
  Spinner,
  StatusPill,
  buttonStyles,
  tableStyles,
  type PillTone,
} from "./ui";

export type FollowUpActions = {
  /** Active follow-up for the invoice, its most recent one, and how many exist in total. */
  followUpFor: (invoiceId: string) => { active: FollowUp | null; latest: FollowUp | null; total: number };
  analyzingInvoiceId: string | null;
  onAnalyze: (invoiceId: string) => void;
  onOpen: (invoiceId: string) => void;
};

type Filter = "all" | "overdue" | "unpaid" | "paid";

const FILTERS: { key: Filter; label: string; matches: (invoice: Invoice) => boolean }[] = [
  { key: "all", label: "All", matches: () => true },
  { key: "overdue", label: "Overdue", matches: (invoice) => invoice.is_overdue },
  { key: "unpaid", label: "Unpaid", matches: (invoice) => invoice.status === "UNPAID" },
  { key: "paid", label: "Paid", matches: (invoice) => invoice.status === "PAID" },
];

function StatusBadge({ invoice }: { invoice: Invoice }) {
  if (invoice.status === "PAID") return <StatusPill label="Paid" tone="good" />;
  if (invoice.is_overdue) return <StatusPill label="Overdue" tone="bad" />;
  return <StatusPill label="Unpaid" tone="neutral" />;
}

function OverdueNote({ invoice }: { invoice: Invoice }) {
  if (!invoice.is_overdue) return null;
  return (
    <span className="block text-xs font-medium text-danger-700">
      {invoice.days_overdue} {invoice.days_overdue === 1 ? "day" : "days"} overdue
    </span>
  );
}

/** What the collection is doing now, as one clear state (the detail lives in the invoice drawer). */
function progressFor(invoice: Invoice, actions: FollowUpActions): { label: string; tone: PillTone; note?: string } | null {
  const { active, latest } = actions.followUpFor(invoice.id);
  const e = invoice.enrollment;
  if (active) return { label: REMINDER_STATUS[active.status].label, tone: active.status === "DRAFT" || active.status === "DELIVERY_UNKNOWN" ? "warn" : "info" };
  const hold = invoice.holds[0];
  if (invoice.status === "UNPAID" && hold) {
    return {
      label: invoice.open_dispute ? "Disputed" : invoice.open_promise ? "Promise due" : "On hold",
      tone: invoice.open_dispute ? "bad" : invoice.open_promise ? "info" : "warn",
      note: invoice.open_dispute
        ? invoice.open_dispute.reason_display
        : invoice.open_promise
          ? `Promised by ${formatDate(invoice.open_promise.promised_date)}`
          : hold.reason_display,
    };
  }
  if (invoice.status === "PAID") return latest?.status === "SENT" ? { label: "Paid after reminders", tone: "good" } : null;
  if (e?.status === "PAUSED") return { label: "Reminders paused", tone: "warn", note: e.pause_reason === "history_review" ? "Needs history review" : undefined };
  if (e?.status === "COMPLETED") return { label: "Reminders finished", tone: "neutral" };
  if (e?.blocked_reason) return { label: "Reminder held", tone: "warn", note: e.blocked_reason.replace(/_/g, " ") };
  if (latest?.status === "SENT") {
    return { label: "Reminder sent", tone: "good", note: e?.next_action_at ? `Next step due ${new Date(e.next_action_at).toLocaleDateString()}` : undefined };
  }
  if (latest?.status === "FAILED") return { label: "Not delivered", tone: "bad" };
  if (e?.next_action_at && !invoice.is_overdue) return { label: "Scheduled", tone: "neutral", note: `Step ${e.next_step} due ${new Date(e.next_action_at).toLocaleDateString()}` };
  return null;
}

function CollectionProgress({ invoice, actions }: { invoice: Invoice; actions: FollowUpActions }) {
  if (actions.analyzingInvoiceId === invoice.id) {
    return (
      <span className="inline-flex items-center gap-2 text-sm font-medium text-brand-700">
        <Spinner className="h-3.5 w-3.5" />
        Drafting…
      </span>
    );
  }
  const progress = progressFor(invoice, actions);
  if (progress) {
    return (
      <div>
        <StatusPill label={progress.label} tone={progress.tone} />
        {progress.note && <span className="mt-1 block text-xs text-ink-subtle">{progress.note}</span>}
      </div>
    );
  }
  // Overdue with nothing in progress: offer the next draft (the backend still checks eligibility).
  if (invoice.is_overdue) {
    const e = invoice.enrollment;
    return (
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          actions.onAnalyze(invoice.id);
        }}
        disabled={actions.analyzingInvoiceId !== null}
        className={buttonStyles.secondarySm}
      >
        <Icon name="sparkles" className="h-3.5 w-3.5 text-brand-600" />
        Draft reminder{e ? ` (step ${e.pending_step ?? e.next_step})` : ""}
      </button>
    );
  }
  return <span className="text-sm text-ink-subtle">—</span>;
}

export function InvoiceTable({
  state,
  expectedColumns,
  followUps,
}: {
  state: Loadable<Invoice[]>;
  expectedColumns: string[];
  followUps: FollowUpActions;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const invoices = state.kind === "ready" ? state.data : [];
  const activeFilter = FILTERS.find((option) => option.key === filter) ?? FILTERS[0]!;
  const visible = invoices.filter(activeFilter.matches);

  return (
    <Section
      title="Invoices"
      count={state.kind === "ready" ? visible.length : undefined}
      flush
      actions={
        state.kind === "ready" && invoices.length > 0 ? (
          <SegmentedFilter
            label="Filter invoices"
            value={filter}
            onChange={setFilter}
            options={FILTERS.map((option) => ({ key: option.key, label: option.label, count: invoices.filter(option.matches).length }))}
          />
        ) : undefined
      }
    >
      {state.kind === "loading" && (
        <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading invoices">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className="h-10" />
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <div className="p-5">
          <ErrorMessage message={`Could not load invoices: ${state.message}`} />
        </div>
      )}

      {state.kind === "ready" && invoices.length === 0 && (
        <EmptyState title="No invoices yet" icon="document">
          <p>
            Click <span className="font-medium text-ink">Sync Invoices</span> to import them from your Google Sheet.
          </p>
          {expectedColumns.length > 0 && (
            <p className="mt-3 text-xs text-ink-subtle">
              Expected columns: <code className="font-mono">{expectedColumns.join(", ")}</code>
            </p>
          )}
        </EmptyState>
      )}

      {state.kind === "ready" && invoices.length > 0 && visible.length === 0 && (
        <EmptyState compact title={`No ${activeFilter.label.toLowerCase()} invoices`}>
          Choose another filter to see the rest.
        </EmptyState>
      )}

      {visible.length > 0 && (
        <ul className="divide-y divide-slate-100 xl:hidden">
          {visible.map((invoice) => (
            <li key={invoice.id}>
              {/* The whole card opens the invoice by mouse; the invoice number is the keyboard target. */}
              <div onClick={() => followUps.onOpen(invoice.id)} className="cursor-pointer space-y-2 px-5 py-4 hover:bg-slate-50">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-ink">{invoice.client_name}</p>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        followUps.onOpen(invoice.id);
                      }}
                      className="rounded font-mono text-xs font-medium text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      {invoice.external_id}
                    </button>
                  </div>
                  <p className="shrink-0 font-semibold tabular-nums text-ink">{formatMoney(invoice.amount, invoice.currency)}</p>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-subtle">
                  <span className="flex items-center gap-2">
                    <StatusBadge invoice={invoice} />
                    Due {formatDate(invoice.due_date)}
                  </span>
                  <OverdueNote invoice={invoice} />
                </div>
                <CollectionProgress invoice={invoice} actions={followUps} />
              </div>
            </li>
          ))}
        </ul>
      )}

      {visible.length > 0 && (
        <div className={`hidden xl:block ${tableStyles.wrap}`}>
          <table className={tableStyles.table}>
            <thead>
              <tr className={tableStyles.headRow}>
                <th scope="col" className={tableStyles.th}>Customer</th>
                <th scope="col" className={tableStyles.th}>Invoice</th>
                <th scope="col" className={tableStyles.th}>Due date</th>
                <th scope="col" className={tableStyles.thNum}>Amount</th>
                <th scope="col" className={tableStyles.th}>Status</th>
                <th scope="col" className={tableStyles.th}>Collection progress</th>
              </tr>
            </thead>
            <tbody className={tableStyles.body}>
              {visible.map((invoice) => (
                <tr key={invoice.id} onClick={() => followUps.onOpen(invoice.id)} className={`${tableStyles.row} cursor-pointer`}>
                  <td className={tableStyles.td}>
                    <p className="max-w-56 truncate font-medium text-ink">{invoice.client_name}</p>
                    <p className="max-w-56 truncate text-xs text-ink-subtle">{invoice.client_email}</p>
                  </td>
                  <td className={`${tableStyles.td} whitespace-nowrap`}>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        followUps.onOpen(invoice.id);
                      }}
                      className="rounded font-mono text-sm font-medium text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      {invoice.external_id}
                    </button>
                  </td>
                  <td className={`${tableStyles.td} whitespace-nowrap`}>
                    <span className="text-ink">{formatDate(invoice.due_date)}</span>
                    <OverdueNote invoice={invoice} />
                  </td>
                  <td className={tableStyles.tdNum}>{formatMoney(invoice.amount, invoice.currency)}</td>
                  <td className={tableStyles.td}>
                    <StatusBadge invoice={invoice} />
                  </td>
                  <td className={`${tableStyles.td} min-w-44`}>
                    <CollectionProgress invoice={invoice} actions={followUps} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
