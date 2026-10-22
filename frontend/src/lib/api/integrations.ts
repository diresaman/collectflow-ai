import { backendGet } from "./client";

export type HealthStatus = "not_configured" | "awaiting_authorization" | "healthy" | "stale" | "failed";

export type IntegrationHealth = {
  key: string;
  name: string;
  status: HealthStatus;
  detail: string;
  action: string;
  last_success_at: string | null;
  last_attempt_at: string | null;
  facts: Record<string, unknown>;
};

export type ScheduledJob = {
  kind: string;
  name: string;
  status: HealthStatus;
  every_minutes: number;
  last_completed_at: string | null;
  last_status: string | null;
  last_error_code: string;
  failed_24h: number;
};

export type IntegrationReport = {
  generated_at: string;
  integrations: IntegrationHealth[];
  scheduled_jobs: ScheduledJob[];
};

const STATUSES = ["not_configured", "awaiting_authorization", "healthy", "stale", "failed"];
type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null;

function isHealth(v: unknown): v is IntegrationHealth {
  return (
    isRec(v) &&
    ["key", "name", "detail", "action"].every((key) => typeof v[key] === "string") &&
    STATUSES.includes(v.status as string) &&
    isRec(v.facts)
  );
}

function isJob(v: unknown): v is ScheduledJob {
  return isRec(v) && typeof v.kind === "string" && STATUSES.includes(v.status as string);
}

function isReport(v: unknown): v is IntegrationReport {
  return (
    isRec(v) &&
    Array.isArray(v.integrations) &&
    v.integrations.every(isHealth) &&
    Array.isArray(v.scheduled_jobs) &&
    v.scheduled_jobs.every(isJob)
  );
}

export function getIntegrationStatus(signal?: AbortSignal): Promise<IntegrationReport> {
  return backendGet("/integrations/status/", isReport, { signal });
}
