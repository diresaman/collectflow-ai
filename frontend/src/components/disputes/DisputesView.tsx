"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import { DisputeCard } from "@/components/dashboard/CasePanel";
import {
  Card,
  EmptyState,
  ErrorMessage,
  KeyValues,
  Notice,
  PageHeader,
  SegmentedFilter,
  Skeleton,
  StatusPill,
  buttonStyles,
} from "@/components/dashboard/ui";
import { getUsers, listDisputes, type AppUser, type DisputeCase, type DisputeFilter } from "@/lib/api/cases";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatDateTime, formatMoney, formatShortDateTime } from "@/lib/format";
import { DISPUTE_STATUS, labelFor } from "@/lib/status";
import { links, setQueryParam, useUrlParam } from "@/lib/url";

const FILTERS: { key: DisputeFilter; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "unassigned", label: "Unassigned" },
  { key: "mine", label: "Mine" },
  { key: "resolved", label: "Resolved" },
  { key: "all", label: "All" },
];

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function DisputeDetails({ dispute, users, onChanged }: { dispute: DisputeCase; users: AppUser[]; onChanged: () => void }) {
  const user = useCurrentUser();
  const isApprover = user.role === "APPROVER" || user.role === "ADMIN";
  const status = labelFor(DISPUTE_STATUS, dispute.status);
  const section = "space-y-2 border-t border-line px-5 py-5 sm:px-6";
  const title = "text-sm font-semibold text-ink";
  return (
    <div>
      <header className="space-y-4 px-5 py-5 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-ink">
              {dispute.client_name} <span className="font-mono text-base font-medium text-ink-muted">{dispute.invoice_external_id}</span>
            </h2>
            <p className="mt-0.5 text-sm text-ink-muted">{dispute.reason_display}</p>
          </div>
          <div className="flex items-center gap-3">
            <StatusPill label={status.label} tone={status.tone} />
            <Link href={links.invoice(dispute.invoice)} className={buttonStyles.secondarySm}>
              Open invoice
            </Link>
          </div>
        </div>
        <KeyValues
          columns={4}
          items={[
            {
              label: "Amount",
              value: dispute.invoice_amount && dispute.invoice_currency ? formatMoney(dispute.invoice_amount, dispute.invoice_currency) : "—",
            },
            { label: "Due", value: dispute.invoice_due_date ? formatDate(dispute.invoice_due_date) : "—" },
            { label: "Invoice status", value: dispute.invoice_status ? dispute.invoice_status.charAt(0) + dispute.invoice_status.slice(1).toLowerCase() : "—" },
            { label: "Owner", value: dispute.owner?.display_name ?? "No owner yet" },
          ]}
        />
      </header>

      <section aria-label="Customer's original reply" className={section}>
        <h3 className={title}>Customer&apos;s original reply</h3>
        {dispute.reply_summary ? (
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-line bg-white px-4 py-3">
            <div className="min-w-0 text-sm">
              <p className="font-medium text-ink">{dispute.reply_summary.subject || "(no subject)"}</p>
              <p className="text-xs text-ink-subtle">
                From {dispute.reply_summary.sender_email} on {formatDateTime(dispute.reply_summary.received_at)}
                {dispute.reply_summary.ai_label ? ` · AI suggested ${dispute.reply_summary.ai_label.toLowerCase().replace(/_/g, " ")}` : ""}
              </p>
            </div>
            <Link href={links.reply(dispute.reply_summary.id)} className={buttonStyles.link}>
              Open the reply
            </Link>
          </div>
        ) : (
          <p className="text-sm text-ink-subtle">This dispute was opened from the invoice, not from a customer reply.</p>
        )}
      </section>

      <div className="border-t border-line px-5 py-5 sm:px-6">
        <DisputeCard
          bare
          dispute={dispute}
          users={users}
          isApprover={isApprover}
          onChanged={() => onChanged()}
          impact={
            dispute.chasing ? (
              <section aria-label="Collection impact" className="space-y-2 border-t border-line pt-4">
                <h4 className={title}>Collection impact</h4>
                {dispute.chasing.resumes ? (
                  <Notice tone="success">Chasing resumes. {dispute.chasing.note}</Notice>
                ) : (
                  <Notice tone="warning" title="Chasing is stopped">
                    <ul className="list-disc space-y-0.5 pl-4">
                      {dispute.chasing.blocked_by.map((b) => (
                        <li key={b.code}>{b.detail}</li>
                      ))}
                    </ul>
                  </Notice>
                )}
              </section>
            ) : undefined
          }
        />
      </div>
    </div>
  );
}

export function DisputesView() {
  const caseParam = useUrlParam("case");
  const [filter, setFilter] = useState<DisputeFilter>(caseParam ? "all" : "open");
  const [rows, setRows] = useState<DisputeCase[] | null>(null);
  const [selected, setSelected] = useState<string | null>(caseParam);
  const [mobileDetail, setMobileDetail] = useState(Boolean(caseParam));
  const [seenCaseParam, setSeenCaseParam] = useState(caseParam);
  if (caseParam !== seenCaseParam) {
    setSeenCaseParam(caseParam);
    if (caseParam) {
      setSelected(caseParam);
      setFilter("all");
      setMobileDetail(true);
    }
  }
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
    setMobileDetail(true);
    setQueryParam("case", id);
  }, []);

  const shown = rows?.find((d) => d.id === selected) ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Disputes"
        description="Review and resolve customer concerns about invoices. While a dispute is open its invoice is not chased; an approver resolves it."
      />
      {error && <ErrorMessage message={`Could not load disputes: ${error}`} />}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,38fr)_minmax(0,62fr)] lg:items-start">
        <Card className={`overflow-hidden lg:sticky lg:top-6 ${mobileDetail ? "max-lg:hidden" : ""}`}>
          <div className="border-b border-line p-4">
            <SegmentedFilter fill label="Filter disputes" options={FILTERS} value={filter} onChange={setFilter} />
          </div>
          {rows === null ? (
            <div className="space-y-3 p-4" aria-busy="true" aria-label="Loading disputes">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
          ) : rows.length === 0 ? (
            <EmptyState title={filter === "open" ? "No open disputes" : "No disputes here"} icon="check">
              A dispute is opened from a reply in Customer Replies or from an invoice.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-slate-100 lg:max-h-[calc(100vh-14rem)] lg:overflow-y-auto" role="listbox" aria-label="Disputes">
              {rows.map((d) => {
                const active = d.id === selected;
                const status = labelFor(DISPUTE_STATUS, d.status);
                return (
                  <li key={d.id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={active}
                      onClick={() => select(d.id)}
                      className={`relative block w-full px-4 py-3.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 ${
                        active ? "bg-brand-50/70" : "hover:bg-slate-50"
                      }`}
                    >
                      {active && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand-600" aria-hidden="true" />}
                      <div className="flex items-start justify-between gap-3">
                        <span className="truncate text-sm font-semibold text-ink">{d.client_name}</span>
                        <span className="shrink-0 text-xs text-ink-subtle">{formatShortDateTime(d.created_at, false)}</span>
                      </div>
                      <p className="mt-0.5 text-sm text-ink-muted">
                        <span className="font-mono text-xs">{d.invoice_external_id}</span> · {d.reason_display}
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <StatusPill label={status.label} tone={status.tone} />
                        <span className="text-xs text-ink-subtle">{d.owner ? `Owner: ${d.owner.display_name}` : "No owner yet"}</span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        <div className={mobileDetail ? "" : "max-lg:hidden"}>
          <button type="button" onClick={() => setMobileDetail(false)} className={`mb-3 lg:hidden ${buttonStyles.ghost} -ml-3`}>
            Back to disputes
          </button>
          <Card className="overflow-hidden">
            {shown ? (
              <DisputeDetails key={`${shown.id}:${shown.status}:${shown.notes.length}`} dispute={shown} users={users} onChanged={() => setReload((n) => n + 1)} />
            ) : (
              <EmptyState title={rows && rows.length === 0 ? "Nothing to review" : "Select a dispute"} icon="info">
                {rows && rows.length === 0
                  ? "Disputes appear here when a customer questions an invoice."
                  : "Choose a dispute on the left to see the reply, the notes and what blocks chasing."}
              </EmptyState>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
