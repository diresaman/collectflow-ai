import { backendGet, backendPost } from "./client";

export type TaskKind = "CALL" | "SEND_INFO" | "UPDATE_CONTACT" | "RECONCILE" | "REVIEW_DISPUTE" | "FOLLOW_UP";
export type TaskStatus = "OPEN" | "DONE" | "CANCELLED";
export type UserRef = { id: number; username: string; display_name: string };

export type Task = {
  id: string;
  kind: TaskKind;
  kind_display: string;
  status: TaskStatus;
  title: string;
  /** Facts from the reply or dispute that created the task, e.g. a payment reference. */
  detail: string;
  invoice: string | null;
  invoice_external_id: string | null;
  client_name: string;
  step: number | null;
  dispute: string | null;
  assignee: UserRef | null;
  due_at: string;
  is_overdue: boolean;
  done_at: string | null;
  done_by: string | null;
  completion_note: string;
  cancelled_at: string | null;
  cancelled_by: string | null;
};

function isTask(v: unknown): v is Task {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    ["id", "kind", "status", "title", "client_name", "due_at"].every((key) => typeof r[key] === "string") &&
    (r.invoice === null || typeof r.invoice === "string") &&
    (r.step === null || typeof r.step === "number") &&
    (r.done_at === null || typeof r.done_at === "string") &&
    typeof r.is_overdue === "boolean" &&
    (r.assignee === null || typeof r.assignee === "object")
  );
}

const isTaskList = (v: unknown): v is Task[] => Array.isArray(v) && v.every(isTask);

export function getOpenTasks(signal?: AbortSignal): Promise<Task[]> {
  return backendGet("/tasks/?open=true", isTaskList, { signal });
}

export function getInvoiceTasks(invoiceId: string, signal?: AbortSignal): Promise<Task[]> {
  return backendGet(`/tasks/?invoice=${invoiceId}`, isTaskList, { signal });
}

export function markTaskDone(id: string, note = ""): Promise<Task> {
  return backendPost(`/tasks/${id}/done/`, isTask, { body: { note } });
}

export function cancelTask(id: string, note: string): Promise<Task> {
  return backendPost(`/tasks/${id}/cancel/`, isTask, { body: { note } });
}

export function assignTask(id: string, assigneeId: number | null): Promise<Task> {
  return backendPost(`/tasks/${id}/assign/`, isTask, { body: { assignee_id: assigneeId } });
}

export function createTask(invoiceId: string, title: string, dueAt: string, assigneeId: number | null): Promise<Task> {
  return backendPost("/tasks/", isTask, {
    body: { invoice_id: invoiceId, title, due_at: dueAt || null, assignee_id: assigneeId },
  });
}
