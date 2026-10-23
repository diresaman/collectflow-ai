import type { CurrencyAmount, DashboardSummary } from "@/lib/api/invoices";
import { formatMoney } from "@/lib/format";

import type { Loadable } from "./types";
import { Card, ErrorMessage, Skeleton, StatCard } from "./ui";

/** One line per currency; amounts in different currencies are never combined. */
function Amounts({ amounts }: { amounts: CurrencyAmount[] }) {
  if (amounts.length === 0) return <>—</>;
  return (
    <span className="block space-y-0.5">
      {amounts.map(({ currency, amount }) => (
        <span key={currency} className={`block ${amounts.length > 1 ? "text-xl" : ""}`}>
          {formatMoney(amount, currency)}
        </span>
      ))}
    </span>
  );
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function SummaryCards({ state }: { state: Loadable<DashboardSummary> }) {
  if (state.kind === "error") return <ErrorMessage message={`Could not load summary: ${state.message}`} />;

  if (state.kind === "loading") {
    return (
      <div className="grid gap-4 md:grid-cols-3" aria-busy="true" aria-label="Loading summary">
        {[0, 1, 2].map((index) => (
          <Card key={index} className="space-y-4 p-5">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-7 w-32" />
            <Skeleton className="h-4 w-40" />
          </Card>
        ))}
      </div>
    );
  }

  const summary = state.data;
  const paid = summary.total_invoices - summary.unpaid_invoices;
  const paidPercent = summary.total_invoices > 0 ? Math.round((paid / summary.total_invoices) * 100) : 0;

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <StatCard
        label="Outstanding"
        icon="document"
        value={<Amounts amounts={summary.outstanding_amounts_by_currency} />}
        detail={`${plural(summary.unpaid_invoices, "unpaid invoice")}`}
      />
      <StatCard
        label="Overdue"
        icon="clock"
        tone={summary.overdue_invoices > 0 ? "danger" : "success"}
        value={<Amounts amounts={summary.overdue_amounts_by_currency} />}
        detail={summary.overdue_invoices > 0 ? `${plural(summary.overdue_invoices, "invoice")} past due` : "Nothing past due"}
      />
      <StatCard
        label="Paid"
        icon="check"
        tone="success"
        value={
          <>
            {paid} <span className="text-base font-medium text-ink-subtle">of {summary.total_invoices}</span>
          </>
        }
        detail={
          <span className="flex items-center gap-3">
            <span
              className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100"
              role="progressbar"
              aria-valuenow={paidPercent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Share of invoices paid"
            >
              <span className="block h-full rounded-full bg-success-500" style={{ width: `${paidPercent}%` }} />
            </span>
            {paidPercent}% of invoices paid
          </span>
        }
      />
    </div>
  );
}
