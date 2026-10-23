"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card, EmptyState, ErrorMessage, PageHeader, Skeleton, StatusPill } from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { getFollowUps, type FollowUp } from "@/lib/api/followups";
import { formatDateTime, formatMoney } from "@/lib/format";
import { REMINDER_STATUS } from "@/lib/status";
import { links } from "@/lib/url";

/**
 * Approvals (UX): reminders and statements waiting for a person, from the existing follow-up list.
 * Review, approve and send happen in the invoice drawer as before; the backend enforces who may approve.
 */
type Tab = "waiting" | "ready";

const TABS: readonly { key: Tab; label: string; status: FollowUp["status"]; empty: string; action: string }[] = [
  {
    key: "waiting",
    label: "Waiting for approval",
    status: "DRAFT",
    empty: "Nothing is waiting for approval. Drafts appear here when a sequence step is due or someone drafts a reminder.",
    action: "Review draft",
  },
  {
    key: "ready",
    label: "Approved, not sent",
    status: "APPROVED",
    empty: "No approved email is waiting to be sent.",
    action: "Review and send",
  },
];

function amountOf(f: FollowUp): string {
  if (f.statement) {
    return Object.entries(f.statement.totals)
      .map(([currency, amount]) => formatMoney(amount, currency))
      .join(" + ");
  }
  const { amount, currency } = f.invoice_snapshot;
  return amount && currency ? formatMoney(amount, currency) : "";
}

function Row({ followUp, action }: { followUp: FollowUp; action: string }) {
  const blocked = followUp.approval.blocked_for_you;
  const needsApprover = followUp.approval.required_role === "APPROVER";
  const changed = followUp.invoice_changed_fields.length > 0;
  return (
    <li className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-semibold text-slate-900">{followUp.client_name}</p>
          {followUp.kind === "STATEMENT" ? (
            <StatusPill label={`Statement · ${followUp.statement?.lines.length ?? 0} invoices`} tone="info" />
          ) : (
            <span className="font-mono text-xs text-slate-500">{followUp.invoice_external_id}</span>
          )}
          <span className="text-sm font-medium text-slate-700">{amountOf(followUp)}</span>
        </div>
        <p className="truncate text-sm text-slate-600">{followUp.subject}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>{followUp.tone.charAt(0) + followUp.tone.slice(1).toLowerCase()} tone</span>
          {followUp.step && <span>· sequence step {followUp.step.order}</span>}
          <span>· {followUp.status === "APPROVED" && followUp.approved_at ? `approved ${formatDateTime(followUp.approved_at)}` : `drafted ${formatDateTime(followUp.generated_at)}`}</span>
          {followUp.approved_by && <span>by {followUp.approved_by}</span>}
        </div>
        {changed && (
          <p className="text-xs font-medium text-amber-800">
            The invoice changed since this was written ({followUp.invoice_changed_fields.join(", ")}): it cannot be approved or
            sent as it is.
          </p>
        )}
        {followUp.status === "DRAFT" && needsApprover && (
          <p className={`text-xs ${blocked ? "text-violet-900" : "text-slate-600"}`}>
            <span className="font-medium">Approver needed</span>
            {followUp.approval.reasons.length ? ` (${followUp.approval.reasons.join("; ")})` : ""}
            {blocked ? `. ${blocked.code === "self_approval" ? "You edited it, so another approver must approve it." : "Your role cannot approve it."}` : ". You can approve it."}
          </p>
        )}
      </div>
      <Link
        href={links.invoice(followUp.invoice_id)}
        className={`shrink-0 rounded-lg px-3 py-2 text-center text-sm font-semibold ${
          blocked
            ? "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
            : "bg-indigo-600 text-white shadow-sm hover:bg-indigo-500"
        }`}
      >
        {blocked ? "View" : action}
      </Link>
    </li>
  );
}

export function ApprovalsView() {
  const [rows, setRows] = useState<FollowUp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("waiting");

  useEffect(() => {
    const controller = new AbortController();
    getFollowUps(controller.signal)
      .then(setRows)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof ApiError ? err.message : "Unexpected error.");
      });
    return () => controller.abort();
  }, []);

  const byTab = (key: Tab) => {
    const status = TABS.find((t) => t.key === key)?.status;
    return (rows ?? []).filter((f) => f.status === status).sort((a, b) => a.generated_at.localeCompare(b.generated_at));
  };
  const current = TABS.find((t) => t.key === tab) ?? TABS[0]!;
  const shown = byTab(tab);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Approvals"
        description="Reminders and statements a person must approve, and approved emails waiting to be sent. Approving never sends; sending is a separate, confirmed step."
      />
      <div className="flex flex-wrap gap-1" role="tablist" aria-label="Approvals">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
              tab === t.key ? "bg-slate-900 text-white" : "bg-white text-slate-600 ring-1 ring-inset ring-slate-200 hover:bg-slate-50"
            }`}
          >
            {t.label}
            {rows && <span className="ml-1.5 opacity-70">{byTab(t.key).length}</span>}
          </button>
        ))}
      </div>
      {error && <ErrorMessage message={`Could not load approvals: ${error}`} />}
      <Card className="overflow-hidden">
        {rows === null && !error ? (
          <div className="space-y-3 p-5">
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
          </div>
        ) : shown.length === 0 ? (
          <EmptyState title={current.label}>{current.empty}</EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100">
            {shown.map((f) => (
              <Row key={f.id} followUp={f} action={current.action} />
            ))}
          </ul>
        )}
      </Card>
      <p className="text-xs text-slate-500">
        Status words: {REMINDER_STATUS.DRAFT.label} → {REMINDER_STATUS.APPROVED.label} → {REMINDER_STATUS.SENT.label}. If
        Gmail&apos;s answer is lost the status becomes &quot;{REMINDER_STATUS.DELIVERY_UNKNOWN.label}&quot; and nothing is resent.
      </p>
    </div>
  );
}
