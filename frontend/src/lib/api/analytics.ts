import { backendGet } from "./client";

/** Stored analytics snapshots (M14). Every figure is per currency; the worker calculates them. */
export type Bucket = { key: string; label: string; amount: string; count: number; invoice_ids: string[] };
export type AgingCurrency = { currency: string; total: string; count: number; overdue: string; buckets: Bucket[] };
export type Aging = { as_of: string; currencies: AgingCurrency[]; note: string };

export type DsoCei = {
  period_start: string;
  period_end: string;
  period_days: number;
  status: "ok" | "insufficient_history";
  reason: string;
  excluded_invoices: number;
  note: string;
  currencies: {
    currency: string;
    dso_days: string | null;
    dso_note: string;
    cei_percent: string | null;
    cei_note: string;
    inputs: { opening_ar: string; billed: string; closing_ar: string; closing_current_ar: string };
  }[];
};

export type Attribution = {
  lookback_days: number;
  window_days: number;
  note: string;
  unmeasured_paid_invoices: number;
  summary: { currency: string; reminder: string; count: number; amount: string }[];
  payments: {
    invoice_id: string;
    external_id: string;
    currency: string;
    amount: string;
    paid_seen_at: string;
    last_reminder_at: string | null;
    last_reminder: string | null;
    days_after_reminder: number | null;
    after_reminder: boolean;
    link: string;
  }[];
};

export type Forecast = {
  as_of: string;
  window_end: string;
  days: number;
  note: string;
  weeks: { start: string; end: string }[];
  currencies: {
    currency: string;
    outstanding: string;
    expected: string;
    promise_expected: string;
    excluded_disputed: string;
    weeks: string[];
  }[];
  invoices: {
    invoice_id: string;
    external_id: string;
    client_name: string;
    currency: string;
    amount: string;
    probability: string;
    expected: string;
    expected_date: string | null;
    source: "promise" | "aging" | "dispute";
    bucket: string;
    low_confidence: boolean;
    explanation: string[];
    link: string;
  }[];
  assumptions: Record<string, unknown>;
};

export type Kpis = {
  currencies: {
    currency: string;
    overdue: string;
    expected_30_days: string;
    dso_days: string | null;
    dso_note: string;
    open_promises: number;
    open_promise_amount: string;
  }[];
};

type Stamped<T> = T & { computed_at: string };

export type AnalyticsOverview = {
  computed: boolean;
  snapshots: {
    aging?: Stamped<Aging>;
    dso_cei?: Stamped<DsoCei>;
    attribution?: Stamped<Attribution>;
    forecast?: Stamped<Forecast>;
    kpis?: Stamped<Kpis>;
  };
};

const isOverview = (v: unknown): v is AnalyticsOverview =>
  typeof v === "object" && v !== null && typeof (v as Record<string, unknown>).computed === "boolean" &&
  typeof (v as Record<string, unknown>).snapshots === "object";

export function getAnalytics(signal?: AbortSignal): Promise<AnalyticsOverview> {
  return backendGet("/analytics/", isOverview, { signal });
}
