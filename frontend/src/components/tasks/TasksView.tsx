"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { TaskRow } from "@/components/dashboard/CasePanel";
import { Card, ErrorMessage, Skeleton } from "@/components/dashboard/ui";
import { getUsers, type AppUser } from "@/lib/api/cases";
import { ApiError } from "@/lib/api/client";
import { listTasks, type Task, type TaskFilter } from "@/lib/api/tasks";
import { links, queryParam } from "@/lib/url";

const FILTERS: { key: TaskFilter; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "mine", label: "Mine" },
  { key: "done", label: "Done" },
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
  const [filter, setFilter] = useState<TaskFilter>(() => (queryParam("task") ? "all" : "open"));
  const [highlight] = useState<string | null>(() => queryParam("task"));
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [users, setUsers] = useState<AppUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

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
          <h1 className="text-2xl font-semibold text-slate-900">Tasks</h1>
          <p className="text-sm text-slate-600">
            Work done outside email. Marking a call task done completes its sequence step; finishing any other task never
            resumes chasing on its own. Cancelling needs a note; call tasks cannot be cancelled.
          </p>
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
      <Card className="px-5 py-2">
        {tasks === null ? (
          <div className="space-y-2 py-3">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : tasks.length === 0 ? (
          <p className="py-4 text-sm text-slate-500">No tasks here.</p>
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
                </p>
                <ul>
                  <TaskRow task={task} users={users} onChanged={() => setReload((n) => n + 1)} />
                </ul>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
