"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { TaskRow } from "@/components/dashboard/CasePanel";
import { Card, ErrorMessage, Skeleton } from "@/components/dashboard/ui";
import { getUsers, type AppUser } from "@/lib/api/cases";
import { ApiError } from "@/lib/api/client";
import { listTasks, type Task, type TaskFilter } from "@/lib/api/tasks";
import { links, useUrlParam } from "@/lib/url";

const FILTERS: { key: TaskFilter; label: string }[] = [
  { key: "open", label: "To do" },
  { key: "mine", label: "Assigned to me" },
  { key: "done", label: "Completed" },
  { key: "cancelled", label: "Cancelled" },
  { key: "all", label: "All" },
];

/** Open tasks first: overdue, then by due time. Done and cancelled after, newest first. */
function ordered(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if ((a.status === "OPEN") !== (b.status === "OPEN")) return a.status === "OPEN" ? -1 : 1;
    if (a.status === "OPEN") return a.due_at.localeCompare(b.due_at) || a.id.localeCompare(b.id);
    return (b.done_at ?? b.cancelled_at ?? "").localeCompare(a.done_at ?? a.cancelled_at ?? "");
  });
}

export function TasksView() {
  const highlight = useUrlParam("task");
  const [filter, setFilter] = useState<TaskFilter>(highlight ? "all" : "open");
  const [seenTaskParam, setSeenTaskParam] = useState(highlight);
  if (highlight !== seenTaskParam) {
    setSeenTaskParam(highlight);
    if (highlight) setFilter("all");
  }
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [users, setUsers] = useState<AppUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getUsers(controller.signal)
      .then(setUsers)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listTasks(filter, controller.signal)
      .then((data) => {
        setTasks(ordered(data));
        setError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof ApiError ? err.message : "Unexpected error.");
      });
    return () => controller.abort();
  }, [filter, reload]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Tasks</h1>
          <p className="mt-1 text-sm text-slate-600">Things to do outside email, such as calls and sending information.</p>
        </div>
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={filter === item.key}
              onClick={() => setFilter(item.key)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                filter === item.key ? "bg-slate-900 text-white" : "bg-white text-slate-600 hover:bg-slate-100"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      {error && <ErrorMessage message={`Could not load tasks: ${error}`} />}
      {notice && (
        <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-900">
          {notice}
        </p>
      )}
      <Card className="px-5 py-2">
        {tasks === null ? (
          <div className="space-y-2 py-3">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : tasks.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">
            {filter === "open" || filter === "mine"
              ? "Nothing to do. Tasks appear when a reply asks for information, a dispute needs review, or a sequence reaches a call step."
              : "No tasks here."}
          </p>
        ) : (
          <div className="divide-y divide-slate-100">
            {tasks.map((task) => (
              <div key={task.id} className={task.id === highlight ? "-mx-5 bg-indigo-50/60 px-5" : ""}>
                <p className="pt-2 text-xs text-slate-500">
                  {task.invoice ? (
                    <Link href={links.invoice(task.invoice)} className="font-medium text-indigo-600 hover:underline">
                      {task.invoice_external_id}
                    </Link>
                  ) : null}
                  {` · ${task.client_name}`}
                  {task.assignee ? ` · assigned to ${task.assignee.display_name}` : " · unassigned"}
                </p>
                <ul>
                  <TaskRow
                    task={task}
                    users={users}
                    onChanged={(message) => {
                      setNotice(message ?? null);
                      setReload((n) => n + 1);
                    }}
                  />
                </ul>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
