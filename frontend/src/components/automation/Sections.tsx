import Link from "next/link";
import type { ReactNode } from "react";

import type { FollowUp } from "@/lib/api/followups";
import type { AttentionItem, WorkflowEntry, WorkflowSummary } from "@/lib/api/workflows";
import { formatDate, formatMoney } from "@/lib/format";

import { DeliveryPanel } from "../dashboard/DeliveryPanel";
import { DraftEditor } from "../dashboard/DraftEditor";
import { FollowUpStatusBadge, ToneBadge } from "../dashboard/FollowUpBadges";
import { Card, Icon, Spinner } from "../dashboard/ui";

export function Section({ title, subtitle, children, tone = "default" }: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  tone?: "default" | "highlight";
}) {
  return (
    <Card className={`overflow-hidden ${tone === "highlight" ? "ring-2 ring-indigo-500/30" : ""}`}>
      <div className="border-b border-slate-200 px-5 py-4">
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      </div>
      <div className="divide-y divide-slate-100">{children}</div>
    </Card>
  );
}

function InvoiceLine({ followUp }: { followUp: FollowUp }) {
  const snapshot = followUp.invoice_snapshot;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="font-semibold text-slate-900">{followUp.client_name}</span>
      <span className="font-mono text-xs text-slate-500">{followUp.invoice_external_id}</span>
      {snapshot.amount && snapshot.currency && (
        <span className="text-sm text-slate-700">{formatMoney(snapshot.amount, snapshot.currency)}</span>
      )}
      {snapshot.due_date && <span className="text-xs text-slate-500">due {formatDate(snapshot.due_date)}</span>}
      <ToneBadge tone={followUp.tone} />
    </div>
  );
}

function StaleWarning({ followUp }: { followUp: FollowUp }) {
  if (followUp.invoice_changed_fields.length === 0) return null;
  return (
    <p role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <span className="font-semibold">Invoice changed since generation</span> ({followUp.invoice_changed_fields.join(", ")}).
      {followUp.status === "DRAFT" ? " Regenerate or reject this draft." : " It can't be sent; withdraw the approval."}
    </p>
  );
}

export function ReviewItem({ entry, regenerating, onChanged, onRegenerate }: {
  entry: WorkflowEntry;
  regenerating: boolean;
  onChanged: () => void;
  onRegenerate: () => void;
}) {
  const followUp = entry.follow_up;
  return (
    <div className="space-y-3 px-5 py-5">
      <InvoiceLine followUp={followUp} />
      <p className="text-sm text-slate-600">
        <Icon name="sparkles" className="mr-1 inline h-3.5 w-3.5 text-indigo-500" />
        {followUp.reasoning}
      </p>
      <StaleWarning followUp={followUp} />
      <DraftEditor
        key={`${followUp.id}:${followUp.updated_at}`}
        followUp={followUp}
        regenerating={regenerating}
        onChanged={onChanged}
        onRegenerate={onRegenerate}
      />
    </div>
  );
}

export function SendItem({ entry, leaving, onChanged, onLeaveUnsent }: {
  entry: WorkflowEntry;
  leaving: boolean;
  onChanged: () => void;
  onLeaveUnsent: () => void;
}) {
  const followUp = entry.follow_up;
  return (
    <div className="space-y-3 px-5 py-5">
      <InvoiceLine followUp={followUp} />
      <StaleWarning followUp={followUp} />
      <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/60 p-4">
        <p className="text-xs uppercase tracking-wide text-slate-400">Approved email</p>
        <p className="text-sm font-semibold text-slate-900">{followUp.subject}</p>
        <p className="whitespace-pre-line text-sm leading-6 text-slate-700">{followUp.body}</p>
      </div>
      <DeliveryPanel
        key={`${followUp.id}:${followUp.status}`}
        followUp={followUp}
        onChanged={onChanged}
        secondaryActions={
          <button
            type="button"
            onClick={onLeaveUnsent}
            disabled={leaving}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50"
          >
            {leaving && <Spinner />}
            Leave unsent
          </button>
        }
      />
    </div>
  );
}

export function DeliveryItem({ entry, onChanged }: { entry: WorkflowEntry; onChanged: () => void }) {
  const followUp = entry.follow_up;
  return (
    <div className="space-y-3 px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <InvoiceLine followUp={followUp} />
        <FollowUpStatusBadge status={followUp.status} />
      </div>
      <DeliveryPanel key={`${followUp.id}:${followUp.status}`} followUp={followUp} onChanged={onChanged} />
    </div>
  );
}

const ATTENTION_LABELS: Record<string, string> = {
  stale_draft: "Invoice changed — draft needs review",
  stale_approved: "Invoice changed — approval needs review",
  delivery_unresolved: "Delivery outcome unresolved",
};

export function AttentionList({ items }: { items: AttentionItem[] }) {
  return (
    <>
      {items.map((item) => (
        <div key={item.item_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 text-sm">
          <span className="font-mono text-xs text-slate-600">{item.invoice_external_id}</span>
          <span className="font-medium text-amber-800">{ATTENTION_LABELS[item.reason_code] ?? item.reason_code}</span>
          <span className="text-xs text-slate-500">{item.detail}</span>
          <Link href="/" className="ml-auto text-xs font-medium text-indigo-600 hover:underline">
            Open dashboard →
          </Link>
        </div>
      ))}
    </>
  );
}

function Stat({ label, value, tone, hint }: { label: string; value: number; tone?: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2.5" title={hint}>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`text-xl font-semibold tabular-nums ${tone ?? "text-slate-900"}`}>{value}</dd>
    </div>
  );
}

export function SummaryGrid({ summary }: { summary: WorkflowSummary }) {
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Invoices evaluated" value={summary.invoices_evaluated} />
        <Stat label="Overdue identified" value={summary.invoices_overdue} />
        <Stat label="Drafts generated" value={summary.drafts_generated} tone="text-indigo-700" />
        <Stat label="Generation failures" value={summary.generation_failures} tone={summary.generation_failures ? "text-rose-600" : undefined} />
        <Stat label="Awaiting review" value={summary.drafts_pending_review} />
        <Stat label="Approved" value={summary.drafts_approved} />
        <Stat label="Rejected" value={summary.drafts_rejected} />
        <Stat label="Approved, left unsent" value={summary.approved_left_unsent} />
        <Stat
          label="Accepted by Gmail"
          value={summary.gmail_submissions_confirmed}
          tone="text-teal-700"
          hint="Gmail accepted the message for delivery. This does not prove the recipient received it."
        />
        <Stat label="Delivery failed" value={summary.delivery_failed} tone={summary.delivery_failed ? "text-rose-600" : undefined} />
        <Stat label="Delivery unknown" value={summary.delivery_unknown} tone={summary.delivery_unknown ? "text-amber-700" : undefined} />
      </dl>
      <p className="text-xs text-slate-500">
        “Accepted by Gmail” means Gmail took the message for delivery. It is not confirmation that the recipient received it.
        {summary.delivery_unknown > 0 && " Unknown outcomes are never counted as sent; reconcile them after checking the Gmail Sent folder."}
      </p>
    </div>
  );
}
