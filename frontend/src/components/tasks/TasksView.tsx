"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";

import { TaskCompletion, UserSelect } from "@/components/dashboard/CasePanel";
import {
  EmptyState,
  ErrorMessage,
  Notice,
  PageHeader,
  Section,
  SegmentedFilter,
  Skeleton,
  StatusPill,
  buttonStyles,
  tableStyles,
} from "@/components/dashboard/ui";
import { getUsers, type AppUser } from "@/lib/api/cases";
import { ApiError } from "@/lib/api/client";
import { assignTask, listTasks, type Task, type TaskFilter } from "@/lib/api/tasks";
import { formatShortDateTime } from "@/lib/format";
import { TASK_STATUS, labelFor } from "@/lib/status";
import { links, useUrlParam } from "@/lib/url";

/** "Overdue" narrows the open tasks on this page; every other filter is the API's own. */
type Filter = TaskFilter | "overdue";

const FILTERS: { key: Filter; label: string; api: TaskFilter }[] = [
  { key: "open", label: "To do", api: "open" },
  { key: "overdue", label: "Overdue", api: "open" },
  { key: "mine", label: "Assigned to me", api: "mine" },
  { key: "done", label: "Completed", api: "done" },
  { key: "cancelled", label: "Cancelled", api: "cancelled" },
  { key: "all", label: "All", api: "all" },
];

/** Open tasks first: overdue, then by due time. Done and cancelled after, newest first. */
function ordered(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if ((a.status === "OPEN") !== (b.status === "OPEN")) return a.status === "OPEN" ? -1 : 1;
    if (a.status === "OPEN") return a.due_at.localeCompare(b.due_at) || a.id.localeCompare(b.id);
    return (b.done_at ?? b.cancelled_at ?? "").localeCompare(a.done_at ?? a.cancelled_at ?? "");
  });
}

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function Due({ task }: { task: Task }) {
  if (task.status !== "OPEN") {
    const at = task.done_at ?? task.cancelled_at;
    return <span className="text-sm text-ink-subtle">{at ? formatShortDateTime(at) : "—"}</span>;
  }
  return (
    <span className="text-sm">
      <span className={task.is_overdue ? "font-medium text-danger-700" : "text-ink"}>{formatShortDateTime(task.due_at)}</span>
      {task.is_overdue && <span className="block text-xs font-semibold text-danger-700">Overdue</span>}
    </span>
  );
}

function Status({ task }: { task: Task }) {
  const status = labelFor(TASK_STATUS, task.status);
  return <StatusPill label={task.is_overdue && task.status === "OPEN" ? "Overdue" : status.label} tone={task.is_overdue && task.status === "OPEN" ? "bad" : status.tone} />;
}

function Owner({ task, users, onChanged }: { task: Task; users: AppUser[]; onChanged: (message?: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (task.status !== "OPEN") {
    return (
      <span className="text-sm text-ink-muted">
        {task.status === "DONE" ? `Done by ${task.done_by ?? "system"}` : `Cancelled by ${task.cancelled_by ?? "system"}`}
      </span>
    );
  }
  return (
    <div>
      <UserSelect
        users={users}
        value={task.assignee?.id ?? null}
        disabled={busy}
        label="Assignee"
        onChange={(id) => {
          setBusy(true);
          setError(null);
          assignTask(task.id, id)
            .then(() => onChanged(`"${task.title}" ${id === null ? "is now unassigned" : "was assigned"}.`))
            .catch((err: unknown) => setError(errorText(err)))
            .finally(() => setBusy(false));
        }}
      />
      {error && <p className="mt-1 text-xs text-danger-700">{error}</p>}
    </div>
  );
}

function Record({ task }: { task: Task }) {
  return (
    <>
      {task.invoice ? (
        <Link href={links.invoice(task.invoice)} className="font-mono text-xs font-medium text-brand-700 hover:underline">
          {task.invoice_external_id}
        </Link>
      ) : (
        <span className="text-xs text-ink-subtle">No invoice</span>
      )}
      <p className="text-sm text-ink-muted">{task.client_name}</p>
    </>
  );
}

export function TasksView() {
  const highlight = useUrlParam("task");
  const [filter, setFilter] = useState<Filter>(highlight ? "all" : "open");
  const [expanded, setExpanded] = useState<string | null>(highlight);
  const [seenTaskParam, setSeenTaskParam] = useState(highlight);
  if (highlight !== seenTaskParam) {
    setSeenTaskParam(highlight);
    if (highlight) {
      setFilter("all");
      setExpanded(highlight);
    }
  }
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [users, setUsers] = useState<AppUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const api = FILTERS.find((f) => f.key === filter)?.api ?? "open";

  useEffect(() => {
    const controller = new AbortController();
    getUsers(controller.signal)
      .then(setUsers)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listTasks(api, controller.signal)
      .then((data) => {
        setTasks(ordered(data));
        setError(null);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [api, reload]);

  const shown = (tasks ?? []).filter((task) => filter !== "overdue" || task.is_overdue);
  const current = FILTERS.find((f) => f.key === filter) ?? FILTERS[0]!;
  const changed = (message?: string) => {
    setNotice(message ?? null);
    setExpanded(null);
    setReload((n) => n + 1);
  };
  const toggle = (task: Task) => setExpanded((id) => (id === task.id ? null : task.id));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tasks"
        description="Track follow-ups and actions that need a person, such as calls and sending information."
      />
      {error && <ErrorMessage message={`Could not load tasks: ${error}`} />}
      {notice && (
        <Notice tone="success" role="status">
          {notice}
        </Notice>
      )}
      <Section
        title={current.label}
        count={tasks ? shown.length : undefined}
        flush
        actions={
          <SegmentedFilter
            label="Filter tasks"
            value={filter}
            onChange={(key) => {
              setFilter(key);
              setNotice(null);
            }}
            options={FILTERS.map(({ key, label }) => ({ key, label }))}
          />
        }
      >
        {tasks === null ? (
          <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading tasks">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : shown.length === 0 ? (
          <EmptyState title={filter === "open" || filter === "mine" || filter === "overdue" ? "Nothing to do" : "No tasks here"} icon="check">
            {filter === "open" || filter === "mine"
              ? "Tasks appear when a reply asks for information, a dispute needs review, or a reminder schedule reaches a call step."
              : filter === "overdue"
                ? "No open task is past its due date."
                : "Tasks with this status will appear here."}
          </EmptyState>
        ) : (
          <>
            <ul className="divide-y divide-slate-100 xl:hidden">
              {shown.map((task) => (
                <li key={task.id} className={`space-y-3 px-5 py-4 ${task.id === highlight ? "bg-brand-50/50" : ""}`}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink">{task.title}</p>
                      <p className="text-xs text-ink-subtle">{task.kind_display}</p>
                    </div>
                    <Status task={task} />
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <Record task={task} />
                    </div>
                    <Due task={task} />
                  </div>
                  {task.detail && <p className="text-sm text-ink-muted">{task.detail}</p>}
                  <Owner task={task} users={users} onChanged={changed} />
                  {task.status === "OPEN" && <TaskCompletion task={task} onChanged={changed} />}
                </li>
              ))}
            </ul>
            <div className={`hidden xl:block ${tableStyles.wrap}`}>
              <table className={tableStyles.table}>
                <thead>
                  <tr className={tableStyles.headRow}>
                    <th scope="col" className={tableStyles.th}>Task</th>
                    <th scope="col" className={tableStyles.th}>Invoice and customer</th>
                    <th scope="col" className={tableStyles.th}>Assigned to</th>
                    <th scope="col" className={tableStyles.th}>{filter === "done" || filter === "cancelled" ? "Closed" : "Due"}</th>
                    <th scope="col" className={tableStyles.th}>Status</th>
                    <th scope="col" className={`${tableStyles.th} text-right`}>
                      <span className="sr-only">Action</span>
                    </th>
                  </tr>
                </thead>
                <tbody className={tableStyles.body}>
                  {shown.map((task) => {
                    const open = expanded === task.id;
                    return (
                      <Fragment key={task.id}>
                        <tr className={`${tableStyles.row} ${task.id === highlight ? "bg-brand-50/50" : ""}`} data-task={task.title}>
                          <td className={`${tableStyles.td} max-w-sm`}>
                            <p className="font-medium text-ink">{task.title}</p>
                            <p className="text-xs text-ink-subtle">{task.kind_display}</p>
                            {task.detail && <p className="mt-0.5 text-xs text-ink-muted">{task.detail}</p>}
                            {task.completion_note && <p className="mt-0.5 text-xs text-ink-muted">Note: {task.completion_note}</p>}
                          </td>
                          <td className={tableStyles.td}>
                            <Record task={task} />
                          </td>
                          <td className={tableStyles.td}>
                            <Owner task={task} users={users} onChanged={changed} />
                          </td>
                          <td className={`${tableStyles.td} whitespace-nowrap`}>
                            <Due task={task} />
                          </td>
                          <td className={tableStyles.td}>
                            <Status task={task} />
                          </td>
                          <td className={`${tableStyles.td} text-right`}>
                            {task.status === "OPEN" && (
                              <button
                                type="button"
                                aria-expanded={open}
                                onClick={() => toggle(task)}
                                className={open ? buttonStyles.secondarySm : buttonStyles.primarySm}
                              >
                                {open ? "Close" : "Complete…"}
                              </button>
                            )}
                          </td>
                        </tr>
                        {open && task.status === "OPEN" && (
                          <tr className="bg-slate-50/70" data-task={task.title}>
                            <td colSpan={6} className="px-5 py-4">
                              <TaskCompletion task={task} onChanged={changed} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>
    </div>
  );
}
