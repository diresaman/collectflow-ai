import type { ReactNode } from "react";

import type { CurrencyAmount, DashboardSummary } from "@/lib/api/invoices";
import { formatMoney } from "@/lib/format";

import type { Loadable } from "./types";
import { Card, ErrorMessage, Icon, Skeleton } from "./ui";

type Tone = "indigo" | "rose" | "emerald";

const TONES: Record<Tone, { icon: string; value: string }> = {
  indigo: { icon: "bg-indigo-50 text-indigo-600", value: "text-slate-900" },
  rose: { icon: "bg-rose-50 text-rose-600", value: "text-rose-600" },
  emerald: { icon: "bg-emerald-50 text-emerald-600", value: "text-slate-900" },
};

function StatCard({
  label,
  icon,
  tone,
  children,
  footer,
}: {
  label: string;
  icon: "document" | "clock" | "check";
  tone: Tone;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${TONES[tone].icon}`}>
          <Icon name={icon} />
        </span>
      </div>
      <div className={`mt-2 ${TONES[tone].value}`}>{children}</div>
      <div className="mt-3 text-sm text-slate-500">{footer}</div>
    </Card>
  );
}

/** One line per currency; amounts in different currencies are never combined. */
function Amounts({ amounts, empty }: { amounts: CurrencyAmount[]; empty: string }) {
  if (amounts.length === 0) return <p className="text-2xl font-semibold tracking-tight">{empty}</p>;
  return (
    <ul className="space-y-0.5">
      {amounts.map(({ currency, amount }) => (
        <li
          key={currency}
          className={`font-semibold tracking-tight tabular-nums ${amounts.length > 1 ? "text-xl" : "text-2xl"}`}
        >
          {formatMoney(amount, currency)}
        </li>
      ))}
    </ul>
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
      <StatCard label="Outstanding" icon="document" tone="indigo" footer={`${plural(summary.unpaid_invoices, "unpaid invoice")}`}>
        <Amounts amounts={summary.outstanding_amounts_by_currency} empty="—" />
      </StatCard>

      <StatCard
        label="Overdue"
        icon="clock"
        tone={summary.overdue_invoices > 0 ? "rose" : "emerald"}
        footer={summary.overdue_invoices > 0 ? `${plural(summary.overdue_invoices, "invoice")} past due` : "Nothing past due"}
      >
        <Amounts amounts={summary.overdue_amounts_by_currency} empty="—" />
      </StatCard>

      <StatCard
        label="Paid"
        icon="check"
        tone="emerald"
        footer={
          <div className="space-y-2">
            <div
              className="h-1.5 overflow-hidden rounded-full bg-slate-100"
              role="progressbar"
              aria-valuenow={paidPercent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Share of invoices paid"
            >
              <div className="h-full rounded-full bg-emerald-500" style={{ width: `${paidPercent}%` }} />
            </div>
            <p>{paidPercent}% of invoices collected</p>
          </div>
        }
      >
        <p className="text-2xl font-semibold tracking-tight tabular-nums">
          {paid} <span className="text-base font-medium text-slate-400">/ {summary.total_invoices}</span>
        </p>
      </StatCard>
    </div>
  );
}
