import { backendGet, backendPost } from "./client";
import { isFollowUp, type FollowUp } from "./followups";

export type WorkflowState =
  | "PROCESSING"
  | "WAITING_FOR_APPROVAL"
  | "WAITING_FOR_SEND_CONFIRMATION"
  | "SENDING"
  | "READY_TO_FINALIZE"
  | "FINALIZED";

export type StepStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED";

export type WorkflowStep = {
  key: string;
  label: string;
  status: StepStatus;
  started_at: string | null;
  completed_at: string | null;
  detail: string;
};

export type WorkflowEntry = { item_id: number; follow_up: FollowUp };

export type AttentionItem = {
  item_id: number;
  invoice_id: string | null;
  invoice_external_id: string;
  reason_code: string;
  detail: string;
};

export type WorkflowSummary = {
  invoices_evaluated: number;
  invoices_overdue: number;
  drafts_generated: number;
  generation_failures: number;
  drafts_pending_review: number;
  drafts_approved: number;
  drafts_rejected: number;
  awaiting_send_confirmation: number;
  approved_left_unsent: number;
  sending: number;
  gmail_submissions_confirmed: number;
  delivery_failed: number;
  delivery_unknown: number;
};

export type Workflow = {
  id: number;
  state: WorkflowState;
  current_step: string;
  run: {
    trigger: string;
    status: "RUNNING" | "COMPLETED" | "PARTIAL" | "FAILED";
    started_at: string;
    completed_at: string | null;
    finalized_at: string | null;
    error_summary: string;
  };
  steps: WorkflowStep[];
  summary: WorkflowSummary;
  pending_actions: {
    approvals: WorkflowEntry[];
    send_confirmations: WorkflowEntry[];
    deliveries: WorkflowEntry[];
    left_unsent: WorkflowEntry[];
    attention: AttentionItem[];
  };
  can_finalize: boolean;
};

type R = Record<string, unknown>;
const isRecord = (value: unknown): value is R => typeof value === "object" && value !== null;
const isStr = (value: unknown): value is string => typeof value === "string";
const isNullableStr = (value: unknown) => value === null || isStr(value);
const isNum = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

const STATES = ["PROCESSING", "WAITING_FOR_APPROVAL", "WAITING_FOR_SEND_CONFIRMATION", "SENDING", "READY_TO_FINALIZE", "FINALIZED"];
const STEP_STATUSES = ["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"];
const SUMMARY_KEYS: (keyof WorkflowSummary)[] = [
  "invoices_evaluated",
  "invoices_overdue",
  "drafts_generated",
  "generation_failures",
  "drafts_pending_review",
  "drafts_approved",
  "drafts_rejected",
  "awaiting_send_confirmation",
  "approved_left_unsent",
  "sending",
  "gmail_submissions_confirmed",
  "delivery_failed",
  "delivery_unknown",
];

const isStep = (v: unknown): v is WorkflowStep =>
  isRecord(v) && isStr(v.key) && isStr(v.label) && STEP_STATUSES.includes(v.status as string) &&
  isNullableStr(v.started_at) && isNullableStr(v.completed_at) && isStr(v.detail);

const isEntry = (v: unknown): v is WorkflowEntry => isRecord(v) && isNum(v.item_id) && isFollowUp(v.follow_up);

const isAttention = (v: unknown): v is AttentionItem =>
  isRecord(v) && isNum(v.item_id) && isNullableStr(v.invoice_id) && isStr(v.invoice_external_id) &&
  isStr(v.reason_code) && isStr(v.detail);

const listOf = <T,>(check: (v: unknown) => v is T) => (v: unknown): v is T[] => Array.isArray(v) && v.every(check);

export function isWorkflow(v: unknown): v is Workflow {
  if (!isRecord(v) || !isRecord(v.run) || !isRecord(v.summary) || !isRecord(v.pending_actions)) return false;
  const run = v.run;
  const actions = v.pending_actions;
  return (
    isNum(v.id) &&
    STATES.includes(v.state as string) &&
    isStr(v.current_step) &&
    isStr(run.trigger) &&
    ["RUNNING", "COMPLETED", "PARTIAL", "FAILED"].includes(run.status as string) &&
    isStr(run.started_at) &&
    isNullableStr(run.completed_at) &&
    isNullableStr(run.finalized_at) &&
    isStr(run.error_summary) &&
    listOf(isStep)(v.steps) &&
    SUMMARY_KEYS.every((key) => isNum((v.summary as R)[key])) &&
    ["approvals", "send_confirmations", "deliveries", "left_unsent"].every((key) => listOf(isEntry)(actions[key])) &&
    listOf(isAttention)(actions.attention) &&
    typeof v.can_finalize === "boolean"
  );
}

const isCurrent = (v: unknown): v is { workflow: Workflow | null } =>
  isRecord(v) && (v.workflow === null || isWorkflow(v.workflow));

export async function getCurrentWorkflow(signal?: AbortSignal): Promise<Workflow | null> {
  return (await backendGet("/automation/workflows/current/", isCurrent, { signal })).workflow;
}

export function getWorkflow(id: number, signal?: AbortSignal): Promise<Workflow> {
  return backendGet(`/automation/workflows/${id}/`, isWorkflow, { signal });
}

export function finalizeWorkflow(id: number): Promise<Workflow> {
  return backendPost(`/automation/workflows/${id}/finalize/`, isWorkflow);
}

export function leaveUnsent(id: number, itemId: number): Promise<Workflow> {
  return backendPost(`/automation/workflows/${id}/items/${itemId}/leave-unsent/`, isWorkflow);
}
