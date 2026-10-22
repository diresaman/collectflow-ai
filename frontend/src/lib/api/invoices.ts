import { backendGet, backendPost } from "./client";
import { waitForJob } from "./jobs";

export type InvoiceStatus = "UNPAID" | "PAID";

export type EnrollmentStatus = "ACTIVE" | "PAUSED" | "COMPLETED" | "EXITED";

/** The invoice's latest sequence enrollment. */
export type EnrollmentSummary = {
  id: string;
  status: EnrollmentStatus;
  last_completed_step: number;
  pending_step: number | null;
  next_step: number;
  total_steps: number;
  next_action_at: string | null;
  /** e.g. delivery_unknown, delivery_failed: why progress is held. */
  blocked_reason: string;
  pause_reason: string;
  exit_reason: string;
  review_required: boolean;
  review_reason: string;
  /** Next automatic AI retry after a transient failure. */
  retry_after: string | null;
};

/** An active chase hold (M10): a customer reply stopped chasing this invoice until it is reviewed. */
export type HoldSummary = {
  id: string;
  reason: string;
  reason_display: string;
  /** Promise, dispute or payment claim: only an approver may resume chasing. */
  protected: boolean;
  reply_id: string | null;
  detail: string;
  created_at: string;
};

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
  in_sheet: boolean;
  enrollment: EnrollmentSummary | null;
  holds: HoldSummary[];
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
  last_successful_sync_at: string | null;
  /** No successful sync for `stale_after_hours`: sequences keep running on the last good data. */
  stale: boolean;
  stale_after_hours: number;
  /** Gemini spending guard for automatic drafting (manual drafts are never blocked). */
  ai_budget: {
    used_24h: number;
    limit: number;
    cooldown_until: string | null;
    automatic_drafting_blocked: "" | "ai_quota_cooldown" | "ai_budget_exhausted";
  };
};

export type SyncRowError = {
  row: number | null;
  field: string | null;
  message: string;
};

export type AutomationRunStatus = "RUNNING" | "SUCCEEDED" | "COMPLETED" | "PARTIAL" | "FAILED";

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

export function isEnrollmentSummary(value: unknown): value is EnrollmentSummary {
  return (
    isRecord(value) &&
    isString(value.id) &&
    ["ACTIVE", "PAUSED", "COMPLETED", "EXITED"].includes(value.status as string) &&
    isNumber(value.last_completed_step) &&
    isNullable(isNumber)(value.pending_step) &&
    isNumber(value.next_step) &&
    isNumber(value.total_steps) &&
    isNullable(isString)(value.next_action_at) &&
    ["blocked_reason", "pause_reason", "exit_reason", "review_reason"].every((key) => isString(value[key])) &&
    typeof value.review_required === "boolean" &&
    isNullable(isString)(value.retry_after)
  );
}

export function isHoldSummary(value: unknown): value is HoldSummary {
  return (
    isRecord(value) &&
    ["id", "reason", "reason_display", "detail", "created_at"].every((key) => isString(value[key])) &&
    typeof value.protected === "boolean" &&
    isNullable(isString)(value.reply_id)
  );
}

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
    isString(value.last_synced_at) &&
    typeof value.in_sheet === "boolean" &&
    isNullable(isEnrollmentSummary)(value.enrollment) &&
    isArrayOf(isHoldSummary)(value.holds)
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
    isArrayOf(isString)(value.expected_columns) &&
    isNullable(isString)(value.last_successful_sync_at) &&
    typeof value.stale === "boolean" &&
    isNumber(value.stale_after_hours) &&
    isRecord(value.ai_budget) &&
    isNumber(value.ai_budget.used_24h) &&
    isNumber(value.ai_budget.limit) &&
    isNullable(isString)(value.ai_budget.cooldown_until) &&
    ["", "ai_quota_cooldown", "ai_budget_exhausted"].includes(value.ai_budget.automatic_drafting_blocked as string)
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
    ["RUNNING", "SUCCEEDED", "COMPLETED", "PARTIAL", "FAILED"].includes(value.status as string) &&
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

/** The worker fetches the sheet with a 30 s timeout; allow for queueing on top. */
const SYNC_WAIT_MS = 60_000;

type SyncQueued = { run_id: number; job_id: string };
const isSyncQueued = (value: unknown): value is SyncQueued =>
  isRecord(value) && isNumber(value.run_id) && isString(value.job_id);

export function getInvoices(signal?: AbortSignal): Promise<Invoice[]> {
  return backendGet("/invoices/", isArrayOf(isInvoice), { signal });
}

export function getDashboard(signal?: AbortSignal): Promise<DashboardSummary> {
  return backendGet("/dashboard/", isDashboardSummary, { signal });
}

export function getAutomationRuns(signal?: AbortSignal): Promise<AutomationRun[]> {
  // Google Sheets synchronizations only (automation runs are listed separately).
  return backendGet("/automation-runs/?kind=SYNC", isArrayOf(isAutomationRun), { signal });
}

/**
 * Queues a sync and waits for the worker to finish it. Resolves with the run whether it
 * succeeded or failed validation / retrieval; rejects when nothing finished (network,
 * authorization, or no worker running).
 */
export async function syncInvoices(): Promise<AutomationRun> {
  const queued = await backendPost("/sync/", isSyncQueued);
  await waitForJob(queued.job_id, { timeoutMs: SYNC_WAIT_MS });
  return backendGet(`/automation-runs/${queued.run_id}/`, isAutomationRun);
}
