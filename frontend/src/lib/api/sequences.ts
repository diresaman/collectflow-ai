import { backendGet, backendPost } from "./client";
import { isHoldSummary, type EnrollmentStatus, type HoldSummary } from "./invoices";

export type Tone = "FRIENDLY" | "PROFESSIONAL" | "FIRM";
export type Channel = "EMAIL" | "TASK";

export type SequenceStep = {
  order: number;
  offset_days: number;
  channel: Channel;
  /** Empty for TASK steps. */
  tone: Tone | "";
  requires_approver: boolean;
};

export type Sequence = {
  id: string;
  name: string;
  version: number;
  is_default: boolean;
  active: boolean;
  created_at: string;
  steps: SequenceStep[];
  /** Live enrollments by current step, and all enrollments by status. */
  enrollment_counts: { by_step: Record<string, number>; by_status: Record<string, number> };
};

export type StepState = SequenceStep & { state: "done" | "pending" | "upcoming" };

export type Enrollment = {
  id: string;
  invoice_id: string;
  invoice_external_id: string;
  sequence: { id: string; name: string; version: number };
  status: EnrollmentStatus;
  last_completed_step: number;
  pending_step: number | null;
  next_step: number;
  next_action_at: string | null;
  escalation_level: number;
  blocked_reason: string;
  pause_reason: string;
  exit_reason: string;
  review_required: boolean;
  review_reason: string;
  generation_failures: number;
  retry_after: string | null;
  steps: StepState[];
  /** Active chase holds from customer replies; chasing waits while any exists. */
  holds: HoldSummary[];
};

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null;
const isStr = (v: unknown): v is string => typeof v === "string";
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function isStep(v: unknown): v is SequenceStep {
  return (
    isRec(v) &&
    isNum(v.order) &&
    isNum(v.offset_days) &&
    (v.channel === "EMAIL" || v.channel === "TASK") &&
    ["", "FRIENDLY", "PROFESSIONAL", "FIRM"].includes(v.tone as string) &&
    typeof v.requires_approver === "boolean"
  );
}

function isSequence(v: unknown): v is Sequence {
  return (
    isRec(v) &&
    isStr(v.id) &&
    isStr(v.name) &&
    isNum(v.version) &&
    typeof v.is_default === "boolean" &&
    typeof v.active === "boolean" &&
    Array.isArray(v.steps) &&
    v.steps.every(isStep) &&
    isRec(v.enrollment_counts) &&
    isRec(v.enrollment_counts.by_step) &&
    isRec(v.enrollment_counts.by_status)
  );
}

function isEnrollment(v: unknown): v is Enrollment {
  return (
    isRec(v) &&
    isStr(v.id) &&
    isStr(v.invoice_id) &&
    isRec(v.sequence) &&
    ["ACTIVE", "PAUSED", "COMPLETED", "EXITED"].includes(v.status as string) &&
    isNum(v.last_completed_step) &&
    (v.pending_step === null || isNum(v.pending_step)) &&
    isNum(v.next_step) &&
    (v.next_action_at === null || isStr(v.next_action_at)) &&
    ["blocked_reason", "pause_reason", "exit_reason", "review_reason"].every((key) => isStr(v[key])) &&
    typeof v.review_required === "boolean" &&
    isNum(v.generation_failures) &&
    (v.retry_after === null || isStr(v.retry_after)) &&
    Array.isArray(v.steps) &&
    v.steps.every((step) => isStep(step) && ["done", "pending", "upcoming"].includes((step as Rec).state as string)) &&
    Array.isArray(v.holds) &&
    v.holds.every(isHoldSummary)
  );
}

const isSequenceList = (v: unknown): v is Sequence[] => Array.isArray(v) && v.every(isSequence);
const isEnrollmentList = (v: unknown): v is Enrollment[] => Array.isArray(v) && v.every(isEnrollment);

export function getSequences(signal?: AbortSignal): Promise<Sequence[]> {
  return backendGet("/sequences/", isSequenceList, { signal });
}

export type NewStep = { offset_days: number; channel: Channel; tone?: Tone; requires_approver: boolean };

/** ADMIN only: saves a new immutable version (2–5 steps). */
export function createSequence(name: string, steps: NewStep[], makeDefault: boolean): Promise<Sequence> {
  return backendPost("/sequences/", isSequence, { body: { name, steps, make_default: makeDefault } });
}

export async function getEnrollment(invoiceId: string, signal?: AbortSignal): Promise<Enrollment | null> {
  const list = await backendGet(`/enrollments/?invoice=${invoiceId}`, isEnrollmentList, { signal });
  return list[0] ?? null;
}

export function pauseEnrollment(id: string, reason: string): Promise<Enrollment> {
  return backendPost(`/enrollments/${id}/pause/`, isEnrollment, { body: { reason } });
}

export function resumeEnrollment(id: string): Promise<Enrollment> {
  return backendPost(`/enrollments/${id}/resume/`, isEnrollment, { body: {} });
}

/** APPROVER: confirm or correct the position of an invoice whose earlier reminders don't match the sequence. */
export function reviewEnrollment(id: string, lastCompletedStep: number, note: string): Promise<Enrollment> {
  return backendPost(`/enrollments/${id}/review/`, isEnrollment, {
    body: { last_completed_step: lastCompletedStep, note },
  });
}
