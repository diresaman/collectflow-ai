"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { getOpenTasks, markTaskDone, type Task } from "@/lib/api/tasks";
import { formatDate } from "@/lib/format";

import type { Loadable } from "./types";
import { Card, ErrorMessage, Spinner } from "./ui";

/** Open tasks for people, e.g. the call step of a collection sequence. Hidden when there are none. */
export function TasksPanel({ reloadKey, onOpenInvoice }: { reloadKey: number; onOpenInvoice: (id: string) => void }) {
  const [tasks, setTasks] = useState<Loadable<Task[]>>({ kind: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getOpenTasks(controller.signal)
      .then((data) => setTasks({ kind: "ready", data }))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setTasks({ kind: "error", message: err instanceof ApiError ? err.message : "Unexpected error." });
        }
      });
    return () => controller.abort();
  }, [reloadKey]);

  if (tasks.kind === "loading" || (tasks.kind === "ready" && tasks.data.length === 0)) return null;

  const done = async (task: Task) => {
    setBusy(task.id);
    setError(null);
    try {
      await markTaskDone(task.id);
      setTasks((current) => (current.kind === "ready" ? { kind: "ready", data: current.data.filter((t) => t.id !== task.id) } : current));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unexpected error.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-200 px-5 py-4">
        <h2 className="text-base font-semibold text-slate-900">Tasks</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          Follow-ups a person does outside email. Marking a call done completes that sequence step.
        </p>
      </div>
      {tasks.kind === "error" && (
        <div className="p-5">
          <ErrorMessage message={`Could not load tasks: ${tasks.message}`} />
        </div>
      )}
      {tasks.kind === "ready" && (
        <ul className="divide-y divide-slate-100">
          {tasks.data.map((task) => (
            <li key={task.id} className="flex flex-col gap-2 px-5 py-3.5 sm:flex-row sm:items-center sm:gap-4">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">{task.title}</p>
                <p className="text-xs text-slate-500">
                  {task.step ? `Sequence step ${task.step} · ` : ""}due {formatDate(task.due_at.slice(0, 10))}
                </p>
                {task.detail && <p className="text-xs text-slate-600">{task.detail}</p>}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {task.invoice && (
                  <button
                    type="button"
                    onClick={() => onOpenInvoice(task.invoice!)}
                    className="text-xs font-medium text-indigo-600 hover:underline"
                  >
                    Open invoice
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void done(task)}
                  disabled={busy !== null}
                  className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {busy === task.id && <Spinner className="h-3 w-3" />}
                  Mark done
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <div className="px-5 pb-4">
          <ErrorMessage message={error} />
        </div>
      )}
    </Card>
  );
}
