import { backendGet, backendPost } from "./client";
import type { Task } from "./tasks";

export const REPLY_LABELS = [
  "PROMISE_TO_PAY",
  "DISPUTE",
  "CLAIMS_PAID",
  "REQUESTS_INFO",
  "OUT_OF_OFFICE",
  "WRONG_CONTACT",
  "OTHER",
] as const;
export type ReplyLabel = (typeof REPLY_LABELS)[number];
export type ReplyState = "RECEIVED" | "ACTIONED" | "TRIAGE" | "RESOLVED";

export const LABEL_NAMES: Record<ReplyLabel, string> = {
  PROMISE_TO_PAY: "Promise to pay",
  DISPUTE: "Dispute",
  CLAIMS_PAID: "Claims paid",
  REQUESTS_INFO: "Requests info",
  OUT_OF_OFFICE: "Out of office",
  WRONG_CONTACT: "Wrong contact",
  OTHER: "Other",
};

/** What applying each label does, in operator terms (mirrors backend apps/inbox/actions.py). */
export const LABEL_EFFECTS: Record<ReplyLabel, string> = {
  PROMISE_TO_PAY: "Keeps chasing on hold until an approver resumes it.",
  DISPUTE: "Keeps chasing on hold until an approver resumes it.",
  CLAIMS_PAID: "Keeps chasing on hold and creates a task to check the payment. Never marks the invoice paid.",
  REQUESTS_INFO: "Creates a task to send the information and resumes chasing.",
  OUT_OF_OFFICE: "Postpones the next step until the return date (at most 21 days) and resumes chasing.",
  WRONG_CONTACT: "Keeps chasing on hold and creates a task to update the contact.",
  OTHER: "No action: resumes chasing.",
};

export const TRIAGE_REASONS: Record<string, string> = {
  review_required: "Promises and disputes always get a person's review.",
  low_confidence: "The AI was not confident enough (below 0.8).",
  suspected_injection: "The reply contains text that tries to instruct the AI.",
  unknown_sender: "The sender is not the invoice contact or their domain.",
  several_invoices: "The reply concerns more than this invoice.",
  no_automatic_action: "No automatic action fits this reply.",
  possible_payment_or_dispute: "The wording mentions a payment, promise or dispute.",
  invalid_output: "The AI's answer failed validation.",
  ai_retry_scheduled: "Gemini was unavailable; an automatic retry is scheduled.",
  ai_unavailable: "Gemini was unavailable; classify it manually or retry.",
  ai_quota_exhausted: "Gemini's quota is exhausted; classify it manually or retry later.",
  ai_bad_response: "Gemini returned an unusable answer.",
  ai_not_configured: "Gemini is not configured.",
  empty_reply: "The reply has no text.",
};

export const EXTRACTED_FIELDS = [
  ["promised_date", "Promised date", "date"],
  ["promised_amount", "Promised amount", "text"],
  ["paid_date", "Paid date", "date"],
  ["paid_amount", "Paid amount", "text"],
  ["payment_reference", "Payment reference", "text"],
  ["dispute_reason", "Dispute reason", "text"],
  ["requested_info", "Requested information", "text"],
  ["return_date", "Back in office", "date"],
  ["new_contact_email", "New contact email", "text"],
] as const;

export type ReplyRow = {
  id: string;
  invoice: string;
  invoice_external_id: string;
  client_name: string;
  sender_email: string;
  sender_name: string;
  sender_check: "MATCH" | "SAME_DOMAIN" | "MISMATCH";
  subject: string;
  snippet: string;
  received_at: string;
  state: ReplyState;
  triage_reason: string;
  label: ReplyLabel | "";
  confidence: number | null;
  label_source: "AI" | "HUMAN" | "";
  on_hold: boolean;
  gmail_thread_id: string;
};

export type Classification = {
  id: string;
  source: "AI" | "HUMAN";
  label: ReplyLabel | "";
  confidence: number | null;
  extracted: Record<string, unknown>;
  rationale: string;
  decision: string;
  error_code: string;
  actor: string | null;
  note: string;
  created_at: string;
};

export type ReplyHold = {
  id: string;
  invoice_external_id: string;
  reason: string;
  reason_display: string;
  protected: boolean;
  created_at: string;
  released_at: string | null;
  released_by: string | null;
  release_note: string;
};

export type HistoryEvent = {
  id: string;
  type: string;
  label: string;
  occurred_at: string;
  actor_type: string;
  actor: string | null;
  invoice_external_id: string | null;
};

export type ReplyDetail = ReplyRow & {
  body: string;
  body_truncated: boolean;
  body_purged_at: string | null;
  extracted: Record<string, unknown>;
  rationale: string;
  classify_attempts: number;
  classifications: Classification[];
  holds: ReplyHold[];
  tasks: Task[];
  history: HistoryEvent[];
  replied_to: { follow_up_id: string; subject: string; sent_at: string | null; step: number | null } | null;
  gmail_url: string;
};

export type ReplySummary = { needs_review: number; invoices_on_hold: number };

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null;
const isStr = (v: unknown): v is string => typeof v === "string";

function isRow(v: unknown): v is ReplyRow {
  return (
    isRec(v) &&
    ["id", "invoice", "invoice_external_id", "sender_email", "subject", "snippet", "received_at", "state", "label"].every(
      (key) => isStr(v[key]),
    ) &&
    typeof v.on_hold === "boolean" &&
    (v.confidence === null || typeof v.confidence === "number")
  );
}

function isDetail(v: unknown): v is ReplyDetail {
  return (
    isRow(v) &&
    isStr((v as Rec).body) &&
    Array.isArray((v as Rec).classifications) &&
    Array.isArray((v as Rec).holds) &&
    Array.isArray((v as Rec).tasks) &&
    Array.isArray((v as Rec).history) &&
    isRec((v as Rec).extracted)
  );
}

const isRows = (v: unknown): v is ReplyRow[] => Array.isArray(v) && v.every(isRow);
const isSummary = (v: unknown): v is ReplySummary =>
  isRec(v) && typeof v.needs_review === "number" && typeof v.invoices_on_hold === "number";
const isJobRef = (v: unknown): v is { job_id: string } => isRec(v) && isStr(v.job_id);

export type ReplyFilter = "triage" | "all" | "handled";

export function getReplies(filter: ReplyFilter, signal?: AbortSignal): Promise<ReplyRow[]> {
  const query = filter === "triage" ? "?triage=true" : filter === "handled" ? "?state=ACTIONED" : "";
  return backendGet(`/replies/${query}`, isRows, { signal });
}

export function getReply(id: string, signal?: AbortSignal): Promise<ReplyDetail> {
  return backendGet(`/replies/${id}/`, isDetail, { signal });
}

export function getReplySummary(signal?: AbortSignal): Promise<ReplySummary> {
  return backendGet("/replies/summary/", isSummary, { signal });
}

export function classifyReply(
  id: string,
  label: ReplyLabel,
  extracted: Record<string, string>,
  note: string,
): Promise<ReplyDetail> {
  return backendPost(`/replies/${id}/classify/`, isDetail, { body: { label, extracted, note } });
}

export function releaseHold(id: string, note: string): Promise<ReplyDetail> {
  return backendPost(`/replies/${id}/release-hold/`, isDetail, { body: { note } });
}

export function retryClassification(id: string): Promise<{ job_id: string }> {
  return backendPost(`/replies/${id}/retry-classification/`, isJobRef, { body: {} });
}
