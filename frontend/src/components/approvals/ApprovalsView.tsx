"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { FollowUpStatusBadge, ToneBadge } from "@/components/dashboard/FollowUpBadges";
import {
  EmptyState,
  ErrorMessage,
  PageHeader,
  Section,
  SegmentedFilter,
  Skeleton,
  buttonStyles,
  tableStyles,
} from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { getFollowUps, type FollowUp } from "@/lib/api/followups";
import { formatMoney, formatShortDateTime } from "@/lib/format";
import { REMINDER_STATUS } from "@/lib/status";
import { links } from "@/lib/url";

/**
 * Approvals: reminders and statements before they are sent, filtered from the existing follow-up list.
 * Review, approve and send happen in the invoice drawer (Reminders tab) as before; the backend decides who
 * may approve, and this page only explains it. Approving never sends.
 */
type Filter = "waiting" | "ready" | "rejected" | "all";

const FILTERS: readonly { key: Filter; label: string; statuses: FollowUp["status"][] | null; empty: [string, string] }[] = [
  {
    key: "waiting",
    label: "Awaiting approval",
    statuses: ["DRAFT"],
    empty: ["Nothing is waiting for approval", "Drafts appear here when a scheduled reminder is due or someone drafts a reminder."],
  },
  {
    key: "ready",
    label: "Approved, not sent",
    statuses: ["APPROVED"],
    empty: ["No approved email is waiting to be sent", "Approved reminders wait here until someone confirms the send."],
  },
  {
    key: "rejected",
    label: "Rejected",
    statuses: ["REJECTED"],
    empty: ["No rejected drafts", "Drafts that a person rejected are kept here for reference; they are never sent."],
  },
  { key: "all", label: "All", statuses: null, empty: ["No reminders yet", "Every reminder and statement appears here once drafted."] },
];

function amountOf(f: FollowUp): string {
  if (f.statement) {
    return Object.entries(f.statement.totals)
      .map(([currency, amount]) => formatMoney(amount, currency))
      .join(" + ");
  }
  const { amount, currency } = f.invoice_snapshot;
  return amount && currency ? formatMoney(amount, currency) : "—";
}

/** Who has to approve and whether the signed-in user can, in plain words. */
function ApprovalNeed({ followUp }: { followUp: FollowUp }) {
  if (followUp.status !== "DRAFT") {
    return (
      <span className="text-xs text-ink-subtle">
        {followUp.approved_by ? `Approved by ${followUp.approved_by}` : REMINDER_STATUS[followUp.status].label}
      </span>
    );
  }
  const blocked = followUp.approval.blocked_for_you;
  const needsApprover = followUp.approval.required_role === "APPROVER";
  return (
    <div className="space-y-0.5 text-xs">
      <p className="font-medium text-ink">{needsApprover ? "Approver needed" : "Any operator"}</p>
      {needsApprover && followUp.approval.reasons.length > 0 && (
        <p className="text-ink-subtle">{followUp.approval.reasons.join("; ")}</p>
      )}
      {blocked ? (
        <p className="font-medium text-brand-800">
          {blocked.code === "self_approval" ? "You edited it: another approver must approve." : "Your role cannot approve it."}
        </p>
      ) : (
        <p className="text-success-700">You can approve it.</p>
      )}
    </div>
  );
}

function StaleWarning({ followUp }: { followUp: FollowUp }) {
  const changed = followUp.invoice_changed_fields.length > 0 || Boolean(followUp.statement?.lines.some((l) => l.invoice_changed_fields.length));
  if (!changed) return null;
  return (
    <p className="mt-1 text-xs font-medium text-warning-800">
      Invoice changed since it was written: it cannot be approved or sent as it is.
    </p>
  );
}

function actionFor(followUp: FollowUp): { label: string; primary: boolean } {
  if (followUp.status === "DRAFT") return followUp.approval.blocked_for_you ? { label: "View", primary: false } : { label: "Review", primary: true };
  if (followUp.status === "APPROVED") return { label: "Review and send", primary: true };
  return { label: "View", primary: false };
}

function Type({ followUp }: { followUp: FollowUp }) {
  return (
    <span className="whitespace-nowrap">
      {followUp.kind === "STATEMENT" ? `Statement · ${followUp.statement?.lines.length ?? 0} invoices` : "Reminder"}
      {followUp.step ? ` · step ${followUp.step.order}` : ""}
    </span>
  );
}

export function ApprovalsView() {
  const [rows, setRows] = useState<FollowUp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("waiting");

  useEffect(() => {
    const controller = new AbortController();
    getFollowUps(controller.signal)
      .then(setRows)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof ApiError ? err.message : "Unexpected error.");
      });
    return () => controller.abort();
  }, []);

  const matching = (key: Filter) => {
    const statuses = FILTERS.find((f) => f.key === key)?.statuses ?? null;
    return (rows ?? [])
      .filter((f) => statuses === null || statuses.includes(f.status))
      .sort((a, b) => (key === "all" ? b.generated_at.localeCompare(a.generated_at) : a.generated_at.localeCompare(b.generated_at)));
  };
  const current = FILTERS.find((f) => f.key === filter) ?? FILTERS[0]!;
  const shown = matching(filter);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Approvals"
        description="Review collection emails before they are sent. Approving never sends: sending is a separate, confirmed step."
      />
      {error && <ErrorMessage message={`Could not load approvals: ${error}`} />}
      <Section
        title={current.label}
        count={rows ? shown.length : undefined}
        flush
        actions={
          <SegmentedFilter
            label="Filter approvals"
            value={filter}
            onChange={setFilter}
            options={FILTERS.map((f) => ({ key: f.key, label: f.label, count: rows ? matching(f.key).length : undefined }))}
          />
        }
      >
        {rows === null && !error ? (
          <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading approvals">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : shown.length === 0 ? (
          <EmptyState title={current.empty[0]} icon="check">
            {current.empty[1]}
          </EmptyState>
        ) : (
          <>
            {/* Phones: one stacked row per email. */}
            <ul className="divide-y divide-slate-100 xl:hidden">
              {shown.map((f) => {
                const action = actionFor(f);
                return (
                  <li key={f.id} className="space-y-2 px-5 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-ink">{f.client_name}</p>
                        <p className="font-mono text-xs text-ink-subtle">{f.invoice_external_id}</p>
                      </div>
                      <p className="shrink-0 text-sm font-semibold tabular-nums text-ink">{amountOf(f)}</p>
                    </div>
                    <p className="text-sm text-ink-muted">{f.subject}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      <FollowUpStatusBadge status={f.status} />
                      <ToneBadge tone={f.tone} />
                    </div>
                    <ApprovalNeed followUp={f} />
                    <p className="text-xs text-ink-subtle">Last edited by {f.last_edited_by ?? "nobody (AI draft)"}</p>
                    <StaleWarning followUp={f} />
                    <Link href={links.invoice(f.invoice_id)} className={`${action.primary ? buttonStyles.primary : buttonStyles.secondary} w-full sm:w-auto`}>
                      {action.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
            <div className={`hidden xl:block ${tableStyles.wrap}`}>
              <table className={tableStyles.table}>
                <thead>
                  <tr className={tableStyles.headRow}>
                    <th scope="col" className={tableStyles.th}>Customer</th>
                    <th scope="col" className={tableStyles.th}>Invoice and subject</th>
                    <th scope="col" className={tableStyles.thNum}>Amount</th>
                    <th scope="col" className={tableStyles.th}>Approval</th>
                    <th scope="col" className={`${tableStyles.th} text-right`}>
                      <span className="sr-only">Action</span>
                    </th>
                  </tr>
                </thead>
                <tbody className={tableStyles.body}>
                  {shown.map((f) => {
                    const action = actionFor(f);
                    return (
                      <tr key={f.id} className={tableStyles.row}>
                        <td className={`${tableStyles.td} whitespace-nowrap`}>
                          <p className="font-medium text-ink">{f.client_name}</p>
                          <p className="text-xs text-ink-subtle">Drafted {formatShortDateTime(f.generated_at)}</p>
                        </td>
                        <td className={`${tableStyles.td} max-w-[20rem]`}>
                          <p className="text-xs text-ink-subtle">
                            <Link href={links.invoice(f.invoice_id)} className="font-mono font-medium text-brand-700 hover:underline">
                              {f.invoice_external_id}
                            </Link>
                            {" · "}
                            <Type followUp={f} />
                          </p>
                          <p className="truncate text-sm text-ink-muted" title={f.subject}>
                            {f.subject}
                          </p>
                          <div className="mt-1.5">
                            <ToneBadge tone={f.tone} />
                          </div>
                          <StaleWarning followUp={f} />
                        </td>
                        <td className={tableStyles.tdNum}>{amountOf(f)}</td>
                        <td className={`${tableStyles.td} min-w-44 max-w-56`}>
                          {filter === "all" && (
                            <div className="mb-1">
                              <FollowUpStatusBadge status={f.status} />
                            </div>
                          )}
                          <ApprovalNeed followUp={f} />
                          <p className="mt-1 text-xs text-ink-subtle">Last edited by {f.last_edited_by ?? "nobody (AI draft)"}</p>
                        </td>
                        <td className={`${tableStyles.td} text-right`}>
                          <Link href={links.invoice(f.invoice_id)} className={action.primary ? buttonStyles.primarySm : buttonStyles.secondarySm}>
                            {action.label}
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>
      <p className="text-xs text-ink-subtle">
        Status words: {REMINDER_STATUS.DRAFT.label} → {REMINDER_STATUS.APPROVED.label} → {REMINDER_STATUS.SENT.label}. If
        Gmail&apos;s answer is lost the status becomes &quot;{REMINDER_STATUS.DELIVERY_UNKNOWN.label}&quot; and nothing is resent.
      </p>
    </div>
  );
}
