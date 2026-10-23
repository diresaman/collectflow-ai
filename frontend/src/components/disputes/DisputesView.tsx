"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import { ChasingNote, DisputeCard } from "@/components/dashboard/CasePanel";
import { Card, ErrorMessage, Skeleton } from "@/components/dashboard/ui";
import { getUsers, listDisputes, type AppUser, type DisputeCase, type DisputeFilter } from "@/lib/api/cases";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { DISPUTE_STATUS, labelFor } from "@/lib/status";
import { links, queryParam, setQueryParam } from "@/lib/url";

const FILTERS: { key: DisputeFilter; label: string }[] = [
  { key: "open", label: "Open cases" },
  { key: "unassigned", label: "No owner yet" },
  { key: "mine", label: "Owned by me" },
  { key: "resolved", label: "Resolved" },
  { key: "all", label: "All" },
];

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function DisputeDetails({ dispute, users, onChanged }: { dispute: DisputeCase; users: AppUser[]; onChanged: () => void }) {
  const user = useCurrentUser();
  const isApprover = user.role === "APPROVER" || user.role === "ADMIN";
  return (
    <div className="space-y-4 p-5">
      <header className="space-y-1">
        <h2 className="text-lg font-semibold text-slate-900">
          {dispute.invoice_external_id} · {dispute.client_name}
        </h2>
        <p className="text-sm text-slate-600">
          {dispute.invoice_amount && dispute.invoice_currency
            ? `${formatMoney(dispute.invoice_amount, dispute.invoice_currency)} · `
            : ""}
          {dispute.invoice_due_date ? `due ${formatDate(dispute.invoice_due_date)} · ` : ""}
          invoice {dispute.invoice_status?.toLowerCase()}
        </p>
        <p className="flex flex-wrap gap-3 text-sm">
          <Link href={links.invoice(dispute.invoice)} className="font-medium text-indigo-600 hover:underline">
            Open the invoice
          </Link>
          {dispute.reply_summary && (
            <Link href={links.reply(dispute.reply_summary.id)} className="font-medium text-indigo-600 hover:underline">
              Open the customer&apos;s reply
            </Link>
          )}
        </p>
        {dispute.reply_summary && (
          <p className="text-xs text-slate-500">
            From {dispute.reply_summary.sender_email} on {formatDateTime(dispute.reply_summary.received_at)}
            {dispute.reply_summary.ai_label ? ` · the AI suggested ${dispute.reply_summary.ai_label.toLowerCase().replace(/_/g, " ")}` : ""}
          </p>
        )}
      </header>
      {dispute.chasing && (
        <section aria-label="Chasing status" className="space-y-1">
          <h3 className="text-sm font-semibold text-slate-900">Is the invoice being chased?</h3>
          <ChasingNote chasing={dispute.chasing} />
        </section>
      )}
      <DisputeCard dispute={dispute} users={users} isApprover={isApprover} onChanged={() => onChanged()} />
    </div>
  );
}

export function DisputesView() {
  const [filter, setFilter] = useState<DisputeFilter>(() => (queryParam("case") ? "all" : "open"));
  const [rows, setRows] = useState<DisputeCase[] | null>(null);
  const [selected, setSelected] = useState<string | null>(() => queryParam("case"));
  const [users, setUsers] = useState<AppUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getUsers(controller.signal)
      .then(setUsers)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listDisputes(filter, controller.signal)
      .then((data) => {
        setRows(data);
        setError(null);
        setSelected((current) => (current && data.some((d) => d.id === current) ? current : (data[0]?.id ?? null)));
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [filter, reload]);

  const select = useCallback((id: string) => {
    setSelected(id);
    setQueryParam("case", id);
  }, []);

  const shown = rows?.find((d) => d.id === selected) ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Disputes</h1>
          <p className="mt-1 text-sm text-slate-600">
            While a dispute is open its invoice is not chased at all. Anyone can add notes or take ownership; an approver
            resolves it.
          </p>
        </div>
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={filter === item.key}
              onClick={() => setFilter(item.key)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                filter === item.key ? "bg-slate-900 text-white" : "bg-white text-slate-600 hover:bg-slate-100"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      {error && <ErrorMessage message={`Could not load disputes: ${error}`} />}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <Card className="max-h-[75vh] overflow-auto">
          {rows === null ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : rows.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-500">No disputes here. A dispute is opened from a customer reply in the Inbox or from an invoice.</p>
          ) : (
            <ul className="divide-y divide-slate-100" role="listbox" aria-label="Disputes">
              {rows.map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={d.id === selected}
                    onClick={() => select(d.id)}
                    className={`block w-full px-4 py-3 text-left hover:bg-slate-50 ${d.id === selected ? "bg-indigo-50/60" : ""}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-slate-900">{d.client_name}</span>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                          d.status === "OPEN" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-slate-200 bg-slate-50 text-slate-600"
                        }`}
                      >
                        {labelFor(DISPUTE_STATUS, d.status).label}
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {d.invoice_external_id} · {d.reason_display} · opened {formatDate(d.created_at.slice(0, 10))}
                    </p>
                    <p className="mt-0.5 text-xs text-slate-600">{d.owner ? `Owner: ${d.owner.display_name}` : "No owner yet"}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          {shown ? (
            <DisputeDetails key={`${shown.id}:${shown.status}:${shown.notes.length}`} dispute={shown} users={users} onChanged={() => setReload((n) => n + 1)} />
          ) : (
            <p className="p-6 text-sm text-slate-500">{rows && rows.length === 0 ? "Nothing to show." : "Select a dispute."}</p>
          )}
        </Card>
      </div>
    </div>
  );
}
