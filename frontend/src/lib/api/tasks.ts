import { backendGet, backendPost } from "./client";

export type Task = {
  id: string;
  kind: "CALL" | "UPDATE_CONTACT" | "SEND_INFO" | "RECONCILE";
  title: string;
  /** Facts from the customer reply that created the task (M10), e.g. a payment reference. */
  detail?: string;
  invoice: string | null;
  invoice_external_id: string | null;
  client_name: string;
  step: number | null;
  due_at: string;
  done_at: string | null;
  done_by: string | null;
};

function isTask(v: unknown): v is Task {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    ["id", "kind", "title", "client_name", "due_at"].every((key) => typeof r[key] === "string") &&
    (r.invoice === null || typeof r.invoice === "string") &&
    (r.step === null || typeof r.step === "number") &&
    (r.done_at === null || typeof r.done_at === "string")
  );
}

const isTaskList = (v: unknown): v is Task[] => Array.isArray(v) && v.every(isTask);

export function getOpenTasks(signal?: AbortSignal): Promise<Task[]> {
  return backendGet("/tasks/?open=true", isTaskList, { signal });
}

export function markTaskDone(id: string): Promise<Task> {
  return backendPost(`/tasks/${id}/done/`, isTask, { body: {} });
}
