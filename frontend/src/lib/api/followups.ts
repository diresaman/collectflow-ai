import { ApiError, backendGet, backendPatch, backendPost } from "./client";

export type Tone = "FRIENDLY" | "PROFESSIONAL" | "FIRM";

export type FollowUpStatus =
  | "DRAFT"
  | "APPROVED"
  | "REJECTED"
  | "SENDING"
  | "SENT"
  | "FAILED"
  | "DELIVERY_UNKNOWN";

export type FollowUp = {
  id: string;
  invoice_id: string;
  invoice_external_id: string;
  client_name: string;
  /** Recipient captured when the draft was generated; delivery always uses this address. */
  recipient_email: string;
  subject: string;
  body: string;
  tone: Tone;
  reasoning: string;
  recommended_action: string;
  status: FollowUpStatus;
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

function isFollowUp(value: unknown): value is FollowUp {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  const str = (key: string) => typeof v[key] === "string";
  const nullableStr = (key: string) => v[key] === null || typeof v[key] === "string";
  return (
    ["id", "invoice_id", "invoice_external_id", "client_name", "recipient_email", "subject", "body"].every(str) &&
    str("reasoning") &&
    ["recommended_action", "generated_at", "updated_at"].every(str) &&
    nullableStr("approved_at") &&
    nullableStr("sent_at") &&
    TONES.includes(v.tone as string) &&
    STATUSES.includes(v.status as string)
  );
}

const isFollowUpList = (value: unknown): value is FollowUp[] => Array.isArray(value) && value.every(isFollowUp);

/** Gemini can take a while; the backend gives up after 30s. */
const GENERATION_TIMEOUT_MS = 45_000;

export function getFollowUps(signal?: AbortSignal): Promise<FollowUp[]> {
  return backendGet("/follow-ups/", isFollowUpList, { signal });
}

export function analyzeInvoice(invoiceId: string, regenerate = false): Promise<FollowUp> {
  return backendPost(`/invoices/${invoiceId}/analyze/`, isFollowUp, {
    body: { regenerate },
    timeoutMs: GENERATION_TIMEOUT_MS,
  });
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
    ["SENDING", "SENT", "FAILED", "DELIVERY_UNKNOWN"].includes(v.status as string)
  );
}

const isDeliveryAttemptList = (value: unknown): value is DeliveryAttempt[] =>
  Array.isArray(value) && value.every(isDeliveryAttempt);

function isSendResult(value: unknown): value is SendResult {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    ["sent", "delivery_failed", "delivery_unknown"].includes(v.code as string) &&
    typeof v.detail === "string" &&
    typeof v.requires_reconciliation === "boolean" &&
    isFollowUp(v.follow_up) &&
    isDeliveryAttempt(v.delivery_attempt)
  );
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

/** Gmail readiness check plus send; the backend bounds both well below this. */
const SEND_TIMEOUT_MS = 50_000;

export function getDeliveryAttempts(followUpId: string, signal?: AbortSignal): Promise<DeliveryAttempt[]> {
  return backendGet(`/follow-ups/${followUpId}/delivery-attempts/`, isDeliveryAttemptList, { signal });
}

export function getGmailStatus(signal?: AbortSignal): Promise<GmailStatus> {
  return backendGet("/integrations/gmail/status/", isGmailStatus, { signal, timeoutMs: 20_000 });
}

/**
 * Submits an approved follow-up to Gmail. Resolves with the recorded outcome for sent,
 * failed and unknown deliveries (HTTP 200 / 502 / 202). Rejects for errors raised before
 * anything was submitted (409, 503, ...). Callers must never retry automatically.
 */
export async function sendFollowUp(followUpId: string): Promise<SendResult> {
  try {
    return await backendPost(`/follow-ups/${followUpId}/send/`, isSendResult, { timeoutMs: SEND_TIMEOUT_MS });
  } catch (error) {
    if (error instanceof ApiError && isSendResult(error.body)) return error.body;
    throw error;
  }
}
