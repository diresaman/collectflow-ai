import { useState, type ReactNode } from "react";

import type { FollowUp } from "@/lib/api/followups";
import type { Invoice } from "@/lib/api/invoices";
import { formatDate, formatMoney } from "@/lib/format";

import { FollowUpStatusBadge } from "./FollowUpBadges";
import type { Loadable } from "./types";
import { Card, ErrorMessage, Icon, Skeleton, Spinner } from "./ui";

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

const AVATAR_COLORS = [
  "bg-indigo-100 text-indigo-700",
  "bg-sky-100 text-sky-700",
  "bg-violet-100 text-violet-700",
  "bg-teal-100 text-teal-700",
  "bg-amber-100 text-amber-700",
];

function Avatar({ name }: { name: string }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
  const hash = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return (
    <span
      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${AVATAR_COLORS[hash % AVATAR_COLORS.length]}`}
      aria-hidden="true"
    >
      {initials || "?"}
    </span>
  );
}

function StatusBadge({ invoice }: { invoice: Invoice }) {
  const [label, badge, dot] =
    invoice.status === "PAID"
      ? ["Paid", "bg-emerald-50 text-emerald-700 ring-emerald-600/20", "bg-emerald-500"]
      : invoice.is_overdue
        ? ["Overdue", "bg-rose-50 text-rose-700 ring-rose-600/20", "bg-rose-500"]
        : ["Unpaid", "bg-amber-50 text-amber-800 ring-amber-600/20", "bg-amber-500"];

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${badge}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {label}
    </span>
  );
}

function OverdueNote({ invoice, block = false }: { invoice: Invoice; block?: boolean }) {
  if (!invoice.is_overdue) return null;
  const Tag = block ? "p" : "span";
  return (
    <Tag className="text-xs font-medium text-rose-600">
      {invoice.days_overdue} {invoice.days_overdue === 1 ? "day" : "days"} overdue
    </Tag>
  );
}

function FollowUpCell({ invoice, actions }: { invoice: Invoice; actions: FollowUpActions }) {
  const { active, latest, total } = actions.followUpFor(invoice.id);

  if (actions.analyzingInvoiceId === invoice.id) {
    return (
      <span className="inline-flex items-center gap-2 text-xs font-medium text-indigo-600">
        <Spinner className="h-3.5 w-3.5" />
        Analyzing…
      </span>
    );
  }
  const shown = active ?? (latest && (latest.status === "SENT" || latest.status === "FAILED") ? latest : null);
  if (shown) {
    return (
      <button
        type="button"
        onClick={() => actions.onOpen(invoice.id)}
        className="rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        title="Review follow-up"
      >
        <FollowUpStatusBadge status={shown.status} />
      </button>
    );
  }
  if (invoice.is_overdue) {
    return (
      <button
        type="button"
        onClick={() => actions.onAnalyze(invoice.id)}
        disabled={actions.analyzingInvoiceId !== null}
        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-indigo-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Icon name="sparkles" className="h-3.5 w-3.5" />
        Analyze with AI
      </button>
    );
  }
  if (total > 0) {
    return (
      <button
        type="button"
        onClick={() => actions.onOpen(invoice.id)}
        className="text-xs font-medium text-slate-500 underline-offset-2 hover:text-slate-700 hover:underline"
      >
        View history
      </button>
    );
  }
  return <span className="text-slate-300">—</span>;
}

function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-6 py-14 text-center">
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      {children && <div className="mt-1 text-sm text-slate-500">{children}</div>}
    </div>
  );
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
    <Card className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-base font-semibold text-slate-900">Invoices</h2>
        {state.kind === "ready" && invoices.length > 0 && (
          <div className="flex gap-1 overflow-x-auto rounded-lg bg-slate-100 p-1" role="tablist" aria-label="Filter invoices">
            {FILTERS.map((option) => {
              const active = option.key === filter;
              return (
                <button
                  key={option.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setFilter(option.key)}
                  className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                    active ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {option.label}
                  <span className={`text-xs tabular-nums ${active ? "text-slate-500" : "text-slate-400"}`}>
                    {invoices.filter(option.matches).length}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {state.kind === "loading" && (
        <div className="space-y-4 p-5" aria-busy="true" aria-label="Loading invoices">
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="flex items-center gap-3">
              <Skeleton className="h-9 w-9 rounded-full" />
              <Skeleton className="h-4 flex-1" />
              <Skeleton className="h-4 w-20" />
            </div>
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <div className="p-5">
          <ErrorMessage message={`Could not load invoices: ${state.message}`} />
        </div>
      )}

      {state.kind === "ready" && invoices.length === 0 && (
        <EmptyState title="No invoices yet">
          <p>
            Click <span className="font-medium text-slate-700">Sync Invoices</span> to import them from Google Sheets.
          </p>
          {expectedColumns.length > 0 && (
            <p className="mt-3 text-xs text-slate-400">
              Expected columns: <code className="font-mono">{expectedColumns.join(", ")}</code>
            </p>
          )}
        </EmptyState>
      )}

      {state.kind === "ready" && invoices.length > 0 && visible.length === 0 && (
        <EmptyState title={`No ${activeFilter.label.toLowerCase()} invoices`} />
      )}

      {visible.length > 0 && (
        <ul className="divide-y divide-slate-100 md:hidden">
          {visible.map((invoice) => (
            <li key={invoice.id} className="flex gap-3 px-5 py-4">
              <Avatar name={invoice.client_name} />
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-900">{invoice.client_name}</p>
                    <p className="truncate text-xs text-slate-500">{invoice.client_email}</p>
                  </div>
                  <p className="shrink-0 font-semibold tabular-nums text-slate-900">
                    {formatMoney(invoice.amount, invoice.currency)}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
                  <StatusBadge invoice={invoice} />
                  <span className="font-mono">{invoice.external_id}</span>
                  <span aria-hidden="true">·</span>
                  <span>Due {formatDate(invoice.due_date)}</span>
                  <OverdueNote invoice={invoice} />
                </div>
                <div className="pt-1">
                  <FollowUpCell invoice={invoice} actions={followUps} />
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {visible.length > 0 && (
        <div className="hidden overflow-x-auto md:block">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/80 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                <th scope="col" className="px-5 py-3">Client</th>
                <th scope="col" className="px-5 py-3">Invoice</th>
                <th scope="col" className="px-5 py-3">Due date</th>
                <th scope="col" className="px-5 py-3 text-right">Amount</th>
                <th scope="col" className="px-5 py-3">Status</th>
                <th scope="col" className="px-5 py-3">Follow-up</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visible.map((invoice) => (
                <tr key={invoice.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-5 py-4">
                    <div className="flex min-w-48 items-center gap-3">
                      <Avatar name={invoice.client_name} />
                      <div className="min-w-0">
                        <p className="truncate font-medium text-slate-900">{invoice.client_name}</p>
                        <p className="truncate text-xs text-slate-500">{invoice.client_email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-5 py-4 font-mono text-xs text-slate-600">{invoice.external_id}</td>
                  <td className="whitespace-nowrap px-5 py-4">
                    <p className="text-slate-700">{formatDate(invoice.due_date)}</p>
                    <OverdueNote invoice={invoice} block />
                  </td>
                  <td className="whitespace-nowrap px-5 py-4 text-right font-semibold tabular-nums text-slate-900">
                    {formatMoney(invoice.amount, invoice.currency)}
                  </td>
                  <td className="whitespace-nowrap px-5 py-4">
                    <StatusBadge invoice={invoice} />
                  </td>
                  <td className="whitespace-nowrap px-5 py-4">
                    <FollowUpCell invoice={invoice} actions={followUps} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
