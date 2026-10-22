import type { FollowUp } from "@/lib/api/followups";
import { formatDateTime } from "@/lib/format";

import { ToneBadge } from "./FollowUpBadges";
import type { Loadable } from "./types";
import { Card, ErrorMessage, Skeleton } from "./ui";

/** Generated drafts waiting for an operator decision. Nothing here has been sent. */
export function ApprovalQueue({ state, onOpen }: { state: Loadable<FollowUp[]>; onOpen: (invoiceId: string) => void }) {
  const drafts = state.kind === "ready" ? state.data.filter((followUp) => followUp.status === "DRAFT") : [];

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-200 px-5 py-4">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-slate-900">Approval queue</h2>
          {drafts.length > 0 && (
            <span className="rounded-full bg-indigo-600 px-2 py-0.5 text-xs font-semibold text-white">{drafts.length}</span>
          )}
        </div>
        <p className="mt-0.5 text-sm text-slate-500">
          Drafts generated — awaiting approval. Nothing is sent until you approve and send it.
        </p>
      </div>

      {state.kind === "loading" && (
        <div className="space-y-3 p-5">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      )}
      {state.kind === "error" && (
        <div className="p-5">
          <ErrorMessage message={`Could not load drafts: ${state.message}`} />
        </div>
      )}
      {state.kind === "ready" && drafts.length === 0 && (
        <p className="px-5 py-8 text-center text-sm text-slate-500">No drafts awaiting approval.</p>
      )}
      {drafts.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {drafts.map((draft) => (
            <li key={draft.id}>
              <button
                type="button"
                onClick={() => onOpen(draft.invoice_id)}
                className="flex w-full flex-col gap-2 px-5 py-3.5 text-left transition-colors hover:bg-slate-50 focus:outline-none focus-visible:bg-slate-50 sm:flex-row sm:items-center sm:gap-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-900">{draft.client_name}</p>
                  <p className="truncate text-xs text-slate-500">
                    <span className="font-mono">{draft.invoice_external_id}</span> · generated {formatDateTime(draft.generated_at)}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  {draft.invoice_changed_fields.length > 0 && (
                    <span className="rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800">
                      Invoice changed — review
                    </span>
                  )}
                  {draft.step && <span className="text-xs text-slate-500">Step {draft.step.order}</span>}
                  {draft.approval.required_role === "APPROVER" && (
                    <span
                      className="rounded-md bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-800"
                      title={draft.approval.reasons.join("; ")}
                    >
                      Needs approver
                    </span>
                  )}
                  <ToneBadge tone={draft.tone} />
                  <span className="text-xs font-medium text-indigo-600">Review →</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
