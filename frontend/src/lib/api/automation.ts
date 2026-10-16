import { ApiError, backendGet, backendPost } from "./client";

export type AutomationRunStatus = "RUNNING" | "COMPLETED" | "PARTIAL" | "FAILED";
export type AutomationItemOutcome = "GENERATED" | "SKIPPED" | "FAILED";

export type AutomationRunItem = {
  id: number;
  invoice_id: string | null;
  invoice_external_id: string;
  outcome: AutomationItemOutcome;
  reason_code: string;
  detail: string;
  follow_up_id: string | null;
};

export type AutomationRunSummary = {
  id: number;
  trigger: "MANUAL" | "SCHEDULED" | "COMMAND";
  status: AutomationRunStatus;
  started_at: string;
  completed_at: string | null;
  invoices_evaluated: number;
  drafts_generated: number;
  invoices_skipped: number;
  generation_failures: number;
  error_summary: string;
  sync_run: number | null;
};

export type AutomationRunDetail = AutomationRunSummary & { items: AutomationRunItem[] };

type UnknownRecord = Record<string, unknown>;
const isRecord = (value: unknown): value is UnknownRecord => typeof value === "object" && value !== null;
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isString = (value: unknown): value is string => typeof value === "string";

function isSummary(value: unknown): value is AutomationRunSummary {
  return (
    isRecord(value) &&
    value.kind === "AUTOMATION" &&
    isNumber(value.id) &&
    ["MANUAL", "SCHEDULED", "COMMAND"].includes(value.trigger as string) &&
    ["RUNNING", "COMPLETED", "PARTIAL", "FAILED"].includes(value.status as string) &&
    isString(value.started_at) &&
    (value.completed_at === null || isString(value.completed_at)) &&
    ["invoices_evaluated", "drafts_generated", "invoices_skipped", "generation_failures"].every((key) =>
      isNumber(value[key]),
    ) &&
    isString(value.error_summary) &&
    (value.sync_run === null || isNumber(value.sync_run))
  );
}

function isItem(value: unknown): value is AutomationRunItem {
  return (
    isRecord(value) &&
    isNumber(value.id) &&
    (value.invoice_id === null || isString(value.invoice_id)) &&
    isString(value.invoice_external_id) &&
    ["GENERATED", "SKIPPED", "FAILED"].includes(value.outcome as string) &&
    isString(value.reason_code) &&
    isString(value.detail) &&
    (value.follow_up_id === null || isString(value.follow_up_id))
  );
}

function isDetail(value: unknown): value is AutomationRunDetail {
  return isSummary(value) && Array.isArray((value as UnknownRecord).items) && ((value as UnknownRecord).items as unknown[]).every(isItem);
}

const isSummaryList = (value: unknown): value is AutomationRunSummary[] => Array.isArray(value) && value.every(isSummary);

/** A full run can take minutes (sync plus up to N Gemini drafts). */
const RUN_TIMEOUT_MS = 15 * 60_000;

export async function getLatestAutomationRun(signal?: AbortSignal): Promise<AutomationRunDetail | null> {
  const runs = await backendGet("/automation-runs/?kind=AUTOMATION", isSummaryList, { signal });
  const latest = runs[0];
  return latest ? backendGet(`/automation-runs/${latest.id}/`, isDetail, { signal }) : null;
}

/**
 * Runs the automation (sync + AI drafts; never sends email). Resolves with the finished run,
 * including a FAILED one (HTTP 502). Rejects with ApiError status 409 if another run is active.
 */
export async function runAutomation(): Promise<AutomationRunDetail> {
  try {
    return await backendPost("/automation/run/", isDetail, { timeoutMs: RUN_TIMEOUT_MS });
  } catch (error) {
    if (error instanceof ApiError && isDetail(error.body)) return error.body;
    throw error;
  }
}
