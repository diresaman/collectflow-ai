import { backendGet, backendPatch, backendPost } from "./client";

export type Tone = "FRIENDLY" | "PROFESSIONAL" | "FIRM";

export type FollowUpStatus = "DRAFT" | "APPROVED" | "REJECTED" | "SENT" | "FAILED";

export type FollowUp = {
  id: string;
  invoice_id: string;
  invoice_external_id: string;
  client_name: string;
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

export const ACTIVE_STATUSES: readonly FollowUpStatus[] = ["DRAFT", "APPROVED"];

const TONES: readonly string[] = ["FRIENDLY", "PROFESSIONAL", "FIRM"];
const STATUSES: readonly string[] = ["DRAFT", "APPROVED", "REJECTED", "SENT", "FAILED"];

function isFollowUp(value: unknown): value is FollowUp {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  const str = (key: string) => typeof v[key] === "string";
  const nullableStr = (key: string) => v[key] === null || typeof v[key] === "string";
  return (
    ["id", "invoice_id", "invoice_external_id", "client_name", "subject", "body", "reasoning"].every(str) &&
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
