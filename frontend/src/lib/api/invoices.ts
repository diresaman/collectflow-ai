import { ApiError, backendGet, backendPost } from "./client";

export type InvoiceStatus = "UNPAID" | "PAID";

export type Invoice = {
  id: string;
  external_id: string;
  client_name: string;
  client_email: string;
  /** Decimal string, e.g. "1250.00". */
  amount: string;
  currency: string;
  /** ISO date, e.g. "2026-01-15". */
  due_date: string;
  status: InvoiceStatus;
  is_overdue: boolean;
  days_overdue: number;
  last_synced_at: string;
};

export type CurrencyAmount = {
  currency: string;
  amount: string;
};

export type DashboardSummary = {
  total_invoices: number;
  unpaid_invoices: number;
  overdue_invoices: number;
  outstanding_amounts_by_currency: CurrencyAmount[];
  overdue_amounts_by_currency: CurrencyAmount[];
  expected_columns: string[];
};

export type SyncRowError = {
  row: number | null;
  field: string | null;
  message: string;
};

export type AutomationRunStatus = "RUNNING" | "SUCCEEDED" | "FAILED";

export type AutomationRun = {
  id: number;
  trigger: string;
  status: AutomationRunStatus;
  started_at: string;
  completed_at: string | null;
  records_processed: number;
  created_count: number;
  updated_count: number;
  unchanged_count: number;
  rejected_count: number;
  error_summary: string;
  errors: SyncRowError[];
};

// Runtime validators ---------------------------------------------------------

type UnknownRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is UnknownRecord => typeof value === "object" && value !== null;
const isString = (value: unknown): value is string => typeof value === "string";
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isNullable =
  <T>(check: (value: unknown) => value is T) =>
  (value: unknown): value is T | null =>
    value === null || check(value);
const isArrayOf =
  <T>(check: (value: unknown) => value is T) =>
  (value: unknown): value is T[] =>
    Array.isArray(value) && value.every(check);

function isInvoice(value: unknown): value is Invoice {
  return (
    isRecord(value) &&
    isString(value.id) &&
    isString(value.external_id) &&
    isString(value.client_name) &&
    isString(value.client_email) &&
    isString(value.amount) &&
    isString(value.currency) &&
    isString(value.due_date) &&
    (value.status === "UNPAID" || value.status === "PAID") &&
    typeof value.is_overdue === "boolean" &&
    isNumber(value.days_overdue) &&
    isString(value.last_synced_at)
  );
}

function isCurrencyAmount(value: unknown): value is CurrencyAmount {
  return isRecord(value) && isString(value.currency) && isString(value.amount);
}

function isDashboardSummary(value: unknown): value is DashboardSummary {
  return (
    isRecord(value) &&
    isNumber(value.total_invoices) &&
    isNumber(value.unpaid_invoices) &&
    isNumber(value.overdue_invoices) &&
    isArrayOf(isCurrencyAmount)(value.outstanding_amounts_by_currency) &&
    isArrayOf(isCurrencyAmount)(value.overdue_amounts_by_currency) &&
    isArrayOf(isString)(value.expected_columns)
  );
}

function isSyncRowError(value: unknown): value is SyncRowError {
  return (
    isRecord(value) && isNullable(isNumber)(value.row) && isNullable(isString)(value.field) && isString(value.message)
  );
}

function isAutomationRun(value: unknown): value is AutomationRun {
  return (
    isRecord(value) &&
    isNumber(value.id) &&
    isString(value.trigger) &&
    (value.status === "RUNNING" || value.status === "SUCCEEDED" || value.status === "FAILED") &&
    isString(value.started_at) &&
    isNullable(isString)(value.completed_at) &&
    isNumber(value.records_processed) &&
    isNumber(value.created_count) &&
    isNumber(value.updated_count) &&
    isNumber(value.unchanged_count) &&
    isNumber(value.rejected_count) &&
    isString(value.error_summary) &&
    isArrayOf(isSyncRowError)(value.errors)
  );
}

// Requests -------------------------------------------------------------------

/** Syncing waits on the Google Sheets API, so allow longer than the default timeout. */
const SYNC_TIMEOUT_MS = 45_000;

export function getInvoices(signal?: AbortSignal): Promise<Invoice[]> {
  return backendGet("/invoices/", isArrayOf(isInvoice), { signal });
}

export function getDashboard(signal?: AbortSignal): Promise<DashboardSummary> {
  return backendGet("/dashboard/", isDashboardSummary, { signal });
}

export function getAutomationRuns(signal?: AbortSignal): Promise<AutomationRun[]> {
  return backendGet("/automation-runs/", isArrayOf(isAutomationRun), { signal });
}

/**
 * Triggers a sync. Resolves with the run whether it succeeded or failed validation /
 * retrieval (the backend responds 422 / 502 with the run); rejects only when no run
 * was recorded (e.g. network or authorization errors).
 */
export async function syncInvoices(): Promise<AutomationRun> {
  try {
    return await backendPost("/sync/", isAutomationRun, { timeoutMs: SYNC_TIMEOUT_MS });
  } catch (error) {
    if (error instanceof ApiError && isAutomationRun(error.body)) return error.body;
    throw error;
  }
}
