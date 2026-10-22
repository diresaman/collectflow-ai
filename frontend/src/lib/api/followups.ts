import { ApiError, backendGet, backendPatch, backendPost } from "./client";
import { isQueued, jobError, waitForJob } from "./jobs";

export type Tone = "FRIENDLY" | "PROFESSIONAL" | "FIRM";

export type FollowUpStatus =
  | "DRAFT"
  | "APPROVED"
  | "REJECTED"
  | "SENDING"
  | "SENT"
  | "FAILED"
  | "DELIVERY_UNKNOWN";

export type FollowUpStep = { order: number; channel: "EMAIL" | "TASK"; tone: string; requires_approver: boolean };

/** Four-eyes policy for this reminder; `blocked_for_you` explains why the signed-in user can't approve. */
export type Approval = {
  required_role: "OPERATOR" | "APPROVER";
  reasons: string[];
  blocked_for_you: { code: "approver_required" | "self_approval"; detail: string } | null;
};

export type FollowUp = {
  id: string;
  invoice_id: string;
  invoice_external_id: string;
  client_name: string;
  /** Recipient captured when the draft was generated; delivery always uses this address. */
  recipient_email: string;
  /** Material invoice changes since generation (DRAFT/APPROVED only); non-empty means "review". */
  invoice_changed_fields: string[];
  /** Invoice facts captured at generation (amount, currency, due_date, ...). */
  invoice_snapshot: Record<string, string>;
  subject: string;
  body: string;
  tone: Tone;
  reasoning: string;
  recommended_action: string;
  status: FollowUpStatus;
  prompt_version: string;
  /** Sequence step this reminder was drafted for (null for reminders from before sequences). */
  step: FollowUpStep | null;
  /** Continues an earlier Gmail thread: the subject is fixed to "Re: …". */
  threaded: boolean;
  approval: Approval;
  /** Usernames; null when the AI draft was never edited / not approved yet. */
  last_edited_by: string | null;
  approved_by: string | null;
  generated_at: string;
  approved_at: string | null;
  sent_at: string | null;
  updated_at: string;
};

/** Mirrors the backend limits in apps/followups/services.py. */
export const SUBJECT_MAX_LENGTH = 150;
export const BODY_MIN_LENGTH = 20;
export const BODY_MAX_LENGTH = 5000;

export const ACTIVE_STATUSES: readonly FollowUpStatus[] = ["DRAFT", "APPROVED", "SENDING", "DELIVERY_UNKNOWN"];

const TONES: readonly string[] = ["FRIENDLY", "PROFESSIONAL", "FIRM"];
const STATUSES: readonly string[] = ["DRAFT", "APPROVED", "REJECTED", "SENDING", "SENT", "FAILED", "DELIVERY_UNKNOWN"];

function isStep(value: unknown): value is FollowUpStep {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.order === "number" &&
    (v.channel === "EMAIL" || v.channel === "TASK") &&
    typeof v.tone === "string" &&
    typeof v.requires_approver === "boolean"
  );
}

function isApproval(value: unknown): value is Approval {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  const blocked = v.blocked_for_you as Record<string, unknown> | null;
  return (
    (v.required_role === "OPERATOR" || v.required_role === "APPROVER") &&
    Array.isArray(v.reasons) &&
    (v.reasons as unknown[]).every((reason) => typeof reason === "string") &&
    (blocked === null ||
      (typeof blocked === "object" &&
        (blocked.code === "approver_required" || blocked.code === "self_approval") &&
        typeof blocked.detail === "string"))
  );
}

export function isFollowUp(value: unknown): value is FollowUp {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  const str = (key: string) => typeof v[key] === "string";
  const nullableStr = (key: string) => v[key] === null || typeof v[key] === "string";
  return (
    ["id", "invoice_id", "invoice_external_id", "client_name", "recipient_email", "subject", "body"].every(str) &&
    str("reasoning") &&
    ["recommended_action", "generated_at", "updated_at"].every(str) &&
    Array.isArray(v.invoice_changed_fields) &&
    (v.invoice_changed_fields as unknown[]).every((field) => typeof field === "string") &&
    typeof v.invoice_snapshot === "object" &&
    v.invoice_snapshot !== null &&
    nullableStr("approved_at") &&
    typeof v.prompt_version === "string" &&
    (v.step === null || isStep(v.step)) &&
    typeof v.threaded === "boolean" &&
    isApproval(v.approval) &&
    nullableStr("last_edited_by") &&
    nullableStr("approved_by") &&
    nullableStr("sent_at") &&
    TONES.includes(v.tone as string) &&
    STATUSES.includes(v.status as string)
  );
}

const isFollowUpList = (value: unknown): value is FollowUp[] => Array.isArray(value) && value.every(isFollowUp);

/** Gemini can take a while (the worker gives up after ~30 s, plus queueing). */
const GENERATION_WAIT_MS = 90_000;

export function getFollowUps(signal?: AbortSignal): Promise<FollowUp[]> {
  return backendGet("/follow-ups/", isFollowUpList, { signal });
}

export function getFollowUp(id: string, signal?: AbortSignal): Promise<FollowUp> {
  return backendGet(`/follow-ups/${id}/`, isFollowUp, { signal });
}

/** Queues AI generation and waits for the draft. Domain errors keep their HTTP status (409, 502, 503). */
export async function analyzeInvoice(invoiceId: string, regenerate = false): Promise<FollowUp> {
  const queued = await backendPost(`/invoices/${invoiceId}/analyze/`, isQueued, { body: { regenerate } });
  const job = await waitForJob(queued.job_id, { timeoutMs: GENERATION_WAIT_MS });
  const followUpId = job.result.follow_up_id;
  if (job.status !== "SUCCEEDED" || typeof followUpId !== "string") throw jobError(job, 502);
  return getFollowUp(followUpId);
}

export function updateFollowUp(id: string, changes: { subject?: string; body?: string }): Promise<FollowUp> {
  return backendPatch(`/follow-ups/${id}/`, isFollowUp, { body: changes });
}

export function approveFollowUp(id: string): Promise<FollowUp> {
  return backendPost(`/follow-ups/${id}/approve/`, isFollowUp);
}

export function rejectFollowUp(id: string): Promise<FollowUp> {
  return backendPost(`/follow-ups/${id}/reject/`, isFollowUp);
}

// Delivery ---------------------------------------------------------------------

export type DeliveryAttemptStatus = "SENDING" | "SENT" | "FAILED" | "DELIVERY_UNKNOWN";

export type DeliveryAttempt = {
  id: string;
  status: DeliveryAttemptStatus;
  recipient_email: string;
  requested_by: string | null;
  started_at: string;
  completed_at: string | null;
  gmail_message_id: string;
  gmail_thread_id: string;
  error_code: string;
  error_summary: string;
};

export type SendResult = {
  code: "sent" | "delivery_failed" | "delivery_unknown";
  detail: string;
  requires_reconciliation: boolean;
  follow_up: FollowUp;
  delivery_attempt: DeliveryAttempt;
};

export type GmailStatus = {
  configured: boolean;
  ready: boolean;
  sender_email: string;
  authorized_email: string | null;
  detail: string;
  allowed_recipients: string[];
  recipient_allowlist_enforced: boolean;
};

function isDeliveryAttempt(value: unknown): value is DeliveryAttempt {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    ["id", "recipient_email", "started_at", "gmail_message_id", "gmail_thread_id", "error_code", "error_summary"].every(
      (key) => typeof v[key] === "string",
    ) &&
    (v.completed_at === null || typeof v.completed_at === "string") &&
    (v.requested_by === null || typeof v.requested_by === "string") &&
    ["SENDING", "SENT", "FAILED", "DELIVERY_UNKNOWN"].includes(v.status as string)
  );
}

const isDeliveryAttemptList = (value: unknown): value is DeliveryAttempt[] =>
  Array.isArray(value) && value.every(isDeliveryAttempt);

type SendQueued = { job_id: string; follow_up: FollowUp; delivery_attempt: DeliveryAttempt };

function isSendQueued(value: unknown): value is SendQueued {
  if (!isQueued(value)) return false;
  const v = value as Record<string, unknown>;
  return isFollowUp(v.follow_up) && isDeliveryAttempt(v.delivery_attempt);
}

function isGmailStatus(value: unknown): value is GmailStatus {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.configured === "boolean" &&
    typeof v.ready === "boolean" &&
    typeof v.sender_email === "string" &&
    (v.authorized_email === null || typeof v.authorized_email === "string") &&
    typeof v.detail === "string" &&
    Array.isArray(v.allowed_recipients) &&
    typeof v.recipient_allowlist_enforced === "boolean"
  );
}

/** The worker checks Gmail readiness and sends (bounded at ~35 s), plus queueing. */
const SEND_WAIT_MS = 60_000;

const OUTCOMES: Partial<Record<FollowUpStatus, Pick<SendResult, "code" | "detail">>> = {
  SENT: { code: "sent", detail: "Gmail accepted the email." },
  FAILED: { code: "delivery_failed", detail: "Gmail did not accept the email. It was not sent." },
  DELIVERY_UNKNOWN: {
    code: "delivery_unknown",
    detail:
      "It is unknown whether Gmail accepted the email. Do not resend. Check the sender's Gmail Sent folder, " +
      "then reconcile with: python manage.py reconcile_delivery",
  },
};

export function getDeliveryAttempts(followUpId: string, signal?: AbortSignal): Promise<DeliveryAttempt[]> {
  return backendGet(`/follow-ups/${followUpId}/delivery-attempts/`, isDeliveryAttemptList, { signal });
}

export function getGmailStatus(signal?: AbortSignal): Promise<GmailStatus> {
  return backendGet("/integrations/gmail/status/", isGmailStatus, { signal, timeoutMs: 20_000 });
}

/**
 * Confirms the send: the backend reserves the reminder and queues one delivery job, then this
 * waits for the worker's outcome. Resolves with the recorded outcome (sent, failed, unknown).
 * Rejects with a 4xx/503 ApiError when nothing was submitted (refused at request time, or the
 * worker found Gmail not ready / the invoice changed), and with status null when the outcome is
 * not known yet. Callers must never retry automatically.
 */
export async function sendFollowUp(followUpId: string): Promise<SendResult> {
  const queued = await backendPost(`/follow-ups/${followUpId}/send/`, isSendQueued);
  const job = await waitForJob(queued.job_id, { timeoutMs: SEND_WAIT_MS });
  if (job.status === "FAILED") {
    if (job.error_code === "abandoned") {
      throw new ApiError("The worker stopped during delivery; the outcome is unknown. Do not resend.", null);
    }
    // Released before anything reached Gmail: the reminder is APPROVED again.
    throw jobError(job, job.error_code === "gmail_not_ready" ? 503 : 409);
  }
  const [followUp, attempts] = await Promise.all([getFollowUp(followUpId), getDeliveryAttempts(followUpId)]);
  const attempt = attempts.find((item) => item.id === queued.delivery_attempt.id) ?? queued.delivery_attempt;
  const outcome = OUTCOMES[followUp.status] ?? OUTCOMES.DELIVERY_UNKNOWN!;
  const detail = attempt.error_summary && outcome.code !== "sent" ? `${outcome.detail} (${attempt.error_summary})` : outcome.detail;
  return {
    ...outcome,
    detail,
    requires_reconciliation: outcome.code === "delivery_unknown",
    follow_up: followUp,
    delivery_attempt: attempt,
  };
}
