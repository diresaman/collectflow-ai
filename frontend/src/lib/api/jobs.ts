import { ApiError, backendGet } from "./client";

export type JobStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED";

/** A background job; provider calls (Sheets, Gemini, Gmail) only ever run inside one. */
export type Job = {
  id: string;
  kind: string;
  status: JobStatus;
  attempts: number;
  max_attempts: number;
  /** Handler output: IDs on success, {code, detail, http_status?} on a reported failure. */
  result: Record<string, unknown>;
  error_code: string;
  created_at: string;
  completed_at: string | null;
  worker_alive: boolean;
};

/** The 202 body of every endpoint that queues work. */
export type Queued = { job_id: string };

export const isQueued = (value: unknown): value is Queued =>
  typeof value === "object" && value !== null && typeof (value as Record<string, unknown>).job_id === "string";

function isJob(value: unknown): value is Job {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    ["id", "kind", "error_code", "created_at"].every((key) => typeof v[key] === "string") &&
    ["PENDING", "RUNNING", "SUCCEEDED", "FAILED"].includes(v.status as string) &&
    typeof v.attempts === "number" &&
    typeof v.max_attempts === "number" &&
    typeof v.result === "object" &&
    v.result !== null &&
    (v.completed_at === null || typeof v.completed_at === "string") &&
    typeof v.worker_alive === "boolean"
  );
}

/** Polling interval while a job is running (design: 1.5 s, only while something is in progress). */
export const JOB_POLL_INTERVAL_MS = 1500;
/** How long a job may stay queued with no live worker before we say so. */
const WORKER_GRACE_MS = 6000;

export const WORKER_DOWN_MESSAGE =
  "The background worker is not running, so the request is queued. Start it with: python manage.py run_worker";

export function getJob(id: string, signal?: AbortSignal): Promise<Job> {
  return backendGet(`/jobs/${id}/`, isJob, { signal });
}

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

/**
 * Polls a job until it finishes (SUCCEEDED or FAILED) and returns it. Rejects with ApiError
 * (status null, body.code "worker_down" or "job_timeout") if it does not finish in time; the
 * job itself keeps its place in the queue either way.
 */
export async function waitForJob(id: string, { timeoutMs = 120_000 }: { timeoutMs?: number } = {}): Promise<Job> {
  const started = Date.now();
  for (;;) {
    const job = await getJob(id);
    if (job.status === "SUCCEEDED" || job.status === "FAILED") return job;
    const waited = Date.now() - started;
    if (job.status === "PENDING" && !job.worker_alive && waited >= WORKER_GRACE_MS) {
      throw new ApiError(WORKER_DOWN_MESSAGE, null, { code: "worker_down" });
    }
    if (waited >= timeoutMs) {
      throw new ApiError("Still in progress. The result will appear once the worker finishes.", null, {
        code: "job_timeout",
      });
    }
    await sleep(JOB_POLL_INTERVAL_MS);
  }
}

/** True when the request is queued but no worker is running to process it. */
export function isWorkerDown(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    typeof error.body === "object" &&
    error.body !== null &&
    (error.body as Record<string, unknown>).code === "worker_down"
  );
}

/** For a FAILED job that reported a domain error: the error to show, with its original HTTP status. */
export function jobError(job: Job, fallbackStatus = 409): ApiError {
  const detail = typeof job.result.detail === "string" ? job.result.detail : `The job failed (${job.error_code}).`;
  const status = typeof job.result.http_status === "number" ? job.result.http_status : fallbackStatus;
  return new ApiError(detail, status, { code: job.error_code, ...job.result });
}
