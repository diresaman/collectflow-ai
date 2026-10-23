import { backendGet, backendPost } from "./client";

/** One scored factor: points out of its maximum, a sentence, and the records it came from. */
export type RiskFactor = {
  key: string;
  label: string;
  points: number;
  max: number;
  detail: string;
  facts: Record<string, unknown>;
};

export type NextAction = {
  code: string;
  title: string;
  detail: string;
  link: string;
  who_can_act: string;
  /** Why the signed-in user cannot act on it themselves (empty if they can). */
  blocked: string;
  invoice_external_id: string | null;
};

export type Blocker = { invoice_id: string; invoice_external_id: string; code: string; detail: string };

export type WorklistRow = {
  rank: number;
  client_id: string;
  client_name: string;
  client_email: string;
  invoice_names: string[];
  currency: string;
  risk_score: number | null;
  low_confidence: boolean;
  outstanding: string;
  priority: string;
  oldest_days_overdue: number;
  top_factors: RiskFactor[];
  computed_at: string | null;
  next_action: NextAction;
  blockers: Blocker[];
  invoices: { id: string; external_id: string; amount: string; due_date: string; days_overdue: number; link: string; chasing_blocked: boolean }[];
  link: string;
};

export type Worklist = {
  generated_at: string;
  scores_computed_at: string | null;
  urgent: { key: string; title: string; count: number }[];
  groups: { currency: string; rows: WorklistRow[] }[];
  unscored: { client_id: string; client_name: string; currency: string }[];
};

export type ClientDetail = {
  id: string;
  display_name: string;
  email: string;
  invoice_names: string[];
  risk_score: number | null;
  low_confidence: boolean;
  factors: RiskFactor[];
  outstanding: Record<string, string>;
  oldest_days_overdue: number | null;
  avg_days_late: string | null;
  promise_kept_rate: string | null;
  response_rate: string | null;
  computed_at: string | null;
  invoices: {
    id: string;
    external_id: string;
    amount: string;
    currency: string;
    due_date: string;
    status: string;
    in_sheet: boolean;
    link: string;
    blockers: string[];
    open_promise_id: string | null;
    open_dispute_id: string | null;
  }[];
  promises: { id: string; invoice_external_id: string; status: string; promised_date: string; link: string }[];
  disputes: { id: string; invoice_external_id: string; status: string; reason: string; link: string }[];
  replies: { id: string; invoice_external_id: string; received_at: string; state: string; label: string; link: string }[];
  open_tasks: { id: string; title: string; due_at: string; invoice_external_id: string | null; link: string }[];
};

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

const isWorklist = (v: unknown): v is Worklist =>
  isRec(v) && Array.isArray(v.groups) && Array.isArray(v.urgent) && v.groups.every((g) => isRec(g) && Array.isArray(g.rows));

const isClient = (v: unknown): v is ClientDetail =>
  isRec(v) && typeof v.id === "string" && Array.isArray(v.factors) && Array.isArray(v.invoices);

export function getWorklist(signal?: AbortSignal): Promise<Worklist> {
  return backendGet("/worklist/", isWorklist, { signal });
}

export function getClient(id: string, signal?: AbortSignal): Promise<ClientDetail> {
  return backendGet(`/clients/${id}/`, isClient, { signal });
}

export function requestRecompute(): Promise<{ job_id: string; status: string }> {
  return backendPost("/clients/recompute/", (v): v is { job_id: string; status: string } => isRec(v) && typeof v.job_id === "string");
}
