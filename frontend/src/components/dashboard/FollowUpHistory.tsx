import type { FollowUp } from "@/lib/api/followups";
import { formatDateTime } from "@/lib/format";

import { FollowUpStatusBadge, ToneBadge } from "./FollowUpBadges";
import type { Loadable } from "./types";
import { Card, ErrorMessage, Skeleton } from "./ui";

export function FollowUpHistory({
  state,
  onOpen,
}: {
  state: Loadable<FollowUp[]>;
  onOpen: (invoiceId: string) => void;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-200 px-5 py-4">
        <h2 className="text-base font-semibold text-slate-900">Follow-up history</h2>
        <p className="mt-0.5 text-sm text-slate-500">AI-drafted reminders and their review status.</p>
      </div>

      {state.kind === "loading" && (
        <div className="space-y-3 p-5" aria-busy="true">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      )}
      {state.kind === "error" && (
        <div className="p-5">
          <ErrorMessage message={`Could not load follow-ups: ${state.message}`} />
        </div>
      )}
      {state.kind === "ready" && state.data.length === 0 && (
        <p className="px-5 py-10 text-center text-sm text-slate-500">
          No follow-ups yet. Use <span className="font-medium text-slate-700">Analyze with AI</span> on an overdue
          invoice.
        </p>
      )}
      {state.kind === "ready" && state.data.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {state.data.map((followUp) => (
            <li key={followUp.id}>
              <button
                type="button"
                onClick={() => onOpen(followUp.invoice_id)}
                className="flex w-full flex-col gap-2 px-5 py-3.5 text-left transition-colors hover:bg-slate-50 focus:outline-none focus-visible:bg-slate-50 sm:flex-row sm:items-center sm:gap-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-900">{followUp.subject}</p>
                  <p className="truncate text-xs text-slate-500">
                    {followUp.client_name} · <span className="font-mono">{followUp.invoice_external_id}</span> ·{" "}
                    {formatDateTime(followUp.generated_at)}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <ToneBadge tone={followUp.tone} />
                  <FollowUpStatusBadge status={followUp.status} />
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
