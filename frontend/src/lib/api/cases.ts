import { backendGet, backendPost } from "./client";
import type { UserRef } from "./tasks";

export type PromiseStatus = "OPEN" | "KEPT" | "BROKEN" | "CANCELLED";
export type DisputeStatus = "OPEN" | "RESOLVED_VALID" | "RESOLVED_INVALID";

export const DISPUTE_REASONS = [
  ["AMOUNT", "Amount is wrong"],
  ["WORK_NOT_DONE", "Work or goods not delivered"],
  ["QUALITY", "Quality of the work"],
  ["ALREADY_PAID", "Already paid"],
  ["NOT_ORDERED", "Not ordered or not agreed"],
  ["OTHER", "Other"],
] as const;

export type PromiseToPay = {
  id: string;
  invoice: string;
  invoice_external_id: string;
  reply: string | null;
  promised_date: string;
  amount: string;
  currency: string;
  status: PromiseStatus;
  note: string;
  owner: UserRef | null;
  /** The last day a payment counts as on time (promised date + 2 days). */
  grace_until: string;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_note: string;
};

export type DisputeNote = { id: string; author: string; text: string; created_at: string };

export type DisputeCase = {
  id: string;
  invoice: string;
  invoice_external_id: string;
  client_name: string;
  reply: string | null;
  reason: string;
  reason_display: string;
  description: string;
  status: DisputeStatus;
  status_display: string;
  owner: UserRef | null;
  opened_by: string;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution: string;
  notes: DisputeNote[];
  invoice_amount?: string;
  invoice_currency?: string;
  invoice_status?: string;
  invoice_due_date?: string;
  reply_summary?: { id: string; subject: string; sender_email: string; received_at: string; ai_label: string } | null;
  /** Whether the invoice is chased now, and every reason it is not. */
  chasing?: ChasingStatus;
  /** Whether the signed-in user may resolve it, and why not. */
  can_resolve?: { allowed: boolean; reason: string };
};

export type ChasingStatus = {
  resumes: boolean;
  blocked_by: { code: string; detail: string }[];
  next_action_at: string | null;
  note: string;
};

export type AppUser = UserRef & { role: string };

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null;
const isStr = (v: unknown): v is string => typeof v === "string";

const isPromise = (v: unknown): v is PromiseToPay =>
  isRec(v) && ["id", "invoice", "promised_date", "amount", "currency", "status", "grace_until"].every((k) => isStr(v[k]));
const isDispute = (v: unknown): v is DisputeCase =>
  isRec(v) && ["id", "invoice", "reason", "description", "status"].every((k) => isStr(v[k])) && Array.isArray(v.notes);
const isList =
  <T>(check: (v: unknown) => v is T) =>
  (v: unknown): v is T[] =>
    Array.isArray(v) && v.every(check);
const isUser = (v: unknown): v is AppUser => isRec(v) && typeof v.id === "number" && isStr(v.username);
const withChasing =
  <T>(check: (v: unknown) => v is T) =>
  (v: unknown): v is T & { chasing?: ChasingStatus } =>
    check(v);

export function getPromises(invoiceId: string, signal?: AbortSignal): Promise<PromiseToPay[]> {
  return backendGet(`/promises/?invoice=${invoiceId}`, isList(isPromise), { signal });
}

export function recordPromise(body: {
  invoice_id: string;
  promised_date: string;
  amount?: string;
  reply_id?: string;
  note?: string;
}): Promise<PromiseToPay> {
  return backendPost("/promises/", isPromise, { body });
}

export function cancelPromise(id: string, note: string): Promise<PromiseToPay & { chasing?: ChasingStatus }> {
  return backendPost(`/promises/${id}/cancel/`, withChasing(isPromise), { body: { note } });
}

export function getDisputes(invoiceId: string, signal?: AbortSignal): Promise<DisputeCase[]> {
  return backendGet(`/disputes/?invoice=${invoiceId}`, isList(isDispute), { signal });
}

export type DisputeFilter = "open" | "resolved" | "mine" | "unassigned" | "all";

export function listDisputes(filter: DisputeFilter, signal?: AbortSignal): Promise<DisputeCase[]> {
  const query = {
    open: "?status=OPEN",
    resolved: "?status=RESOLVED",
    mine: "?owner=me&status=OPEN",
    unassigned: "?unassigned=true&status=OPEN",
    all: "",
  }[filter];
  return backendGet(`/disputes/${query}`, isList(isDispute), { signal });
}

export function getDispute(id: string, signal?: AbortSignal): Promise<DisputeCase> {
  return backendGet(`/disputes/${id}/`, isDispute, { signal });
}

export function openDispute(body: {
  invoice_id: string;
  reason: string;
  description: string;
  reply_id?: string;
  owner_id?: number | null;
}): Promise<DisputeCase> {
  return backendPost("/disputes/", isDispute, { body });
}

export function assignDispute(id: string, ownerId: number | null): Promise<DisputeCase> {
  return backendPost(`/disputes/${id}/assign/`, isDispute, { body: { owner_id: ownerId } });
}

export function addDisputeNote(id: string, note: string): Promise<DisputeCase> {
  return backendPost(`/disputes/${id}/notes/`, isDispute, { body: { note } });
}

export function resolveDispute(
  id: string,
  outcome: "VALID" | "INVALID",
  resolution: string,
): Promise<DisputeCase & { chasing?: ChasingStatus }> {
  return backendPost(`/disputes/${id}/resolve/`, withChasing(isDispute), { body: { outcome, resolution } });
}

export function getUsers(signal?: AbortSignal): Promise<AppUser[]> {
  return backendGet("/users/", isList(isUser), { signal });
}

export type HistoryEvent = {
  id: string;
  /** The invoice the event belongs to (null for customer-level events). */
  invoice?: string | null;
  type: string;
  type_label: string;
  actor_type: string;
  actor_username: string | null;
  occurred_at: string;
};

const isHistory = (v: unknown): v is { results: HistoryEvent[] } =>
  isRec(v) && Array.isArray(v.results) && v.results.every((e) => isRec(e) && isStr(e.type) && isStr(e.occurred_at));

/** Recent timeline events for an invoice, newest first. */
export async function getInvoiceHistory(invoiceId: string, signal?: AbortSignal): Promise<HistoryEvent[]> {
  const page = await backendGet(`/timeline/?invoice=${invoiceId}&limit=50`, isHistory, { signal });
  return page.results;
}

/** Recent timeline events for a customer (every invoice of the client), newest first. */
export async function getClientHistory(clientId: string, signal?: AbortSignal): Promise<HistoryEvent[]> {
  const page = await backendGet(`/timeline/?client=${clientId}&limit=100`, isHistory, { signal });
  return page.results;
}
