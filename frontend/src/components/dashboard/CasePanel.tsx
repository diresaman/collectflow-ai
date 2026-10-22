"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import {
  DISPUTE_REASONS,
  addDisputeNote,
  assignDispute,
  cancelPromise,
  getDisputes,
  getInvoiceHistory,
  getPromises,
  getUsers,
  openDispute,
  recordPromise,
  resolveDispute,
  type AppUser,
  type ChasingStatus,
  type DisputeCase,
  type HistoryEvent,
  type PromiseToPay,
} from "@/lib/api/cases";
import { ApiError } from "@/lib/api/client";
import { getInvoice, type Invoice } from "@/lib/api/invoices";
import { assignTask, cancelTask, createTask, getInvoiceTasks, markTaskDone, type Task } from "@/lib/api/tasks";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { links } from "@/lib/url";

import { ErrorMessage, Spinner } from "./ui";

const CASE_EVENTS = new Set([
  "PROMISE_RECORDED", "PROMISE_KEPT", "PROMISE_BROKEN", "PROMISE_CANCELLED", "SEQUENCE_ESCALATED",
  "DISPUTE_OPENED", "DISPUTE_ASSIGNED", "DISPUTE_NOTE_ADDED", "DISPUTE_RESOLVED", "FOLLOW_UP_CANCELLED",
  "CHASE_HOLD_PLACED", "CHASE_HOLD_RELEASED", "TASK_CREATED", "TASK_ASSIGNED", "TASK_DONE", "TASK_CANCELLED",
  "TASK_OVERDUE", "REPLY_RECEIVED", "REPLY_REVIEWED",
]);

const PROMISE_BADGES: Record<PromiseToPay["status"], string> = {
  OPEN: "bg-sky-50 text-sky-800 border-sky-200",
  KEPT: "bg-emerald-50 text-emerald-800 border-emerald-200",
  BROKEN: "bg-rose-50 text-rose-800 border-rose-200",
  CANCELLED: "bg-slate-50 text-slate-600 border-slate-200",
};

const inputClass = "mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900";
const buttonClass =
  "inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50";
const primaryClass =
  "inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function Box({ title, children, badge }: { title: string; children: ReactNode; badge?: ReactNode }) {
  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-900">{title}</h4>
        {badge}
      </div>
      {children}
    </div>
  );
}

export function ChasingNote({ chasing }: { chasing: ChasingStatus | null }) {
  if (!chasing) return null;
  return (
    <p role="status" className="rounded-md bg-slate-50 px-2 py-1.5 text-xs text-slate-700">
      {chasing.resumes
        ? `Chasing resumes. ${chasing.note}`
        : `Chasing stays stopped: ${chasing.blocked_by.map((b) => b.detail).join(" ")}`}
    </p>
  );
}

export function UserSelect({
  users,
  value,
  onChange,
  disabled,
  label,
}: {
  users: AppUser[];
  value: number | null;
  onChange: (id: number | null) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <select
      aria-label={label}
      value={value ?? ""}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900"
    >
      <option value="">Unassigned</option>
      {users.map((user) => (
        <option key={user.id} value={user.id}>
          {user.display_name} ({user.role.toLowerCase()})
        </option>
      ))}
    </select>
  );
}

function PromiseCard({
  promise,
  isApprover,
  onChanged,
}: {
  promise: PromiseToPay;
  isApprover: boolean;
  onChanged: (chasing: ChasingStatus | null) => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancel = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await cancelPromise(promise.id, note);
      onChanged(result.chasing ?? null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Box
      title={`Promise to pay ${formatMoney(promise.amount, promise.currency)} by ${formatDate(promise.promised_date)}`}
      badge={<span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${PROMISE_BADGES[promise.status]}`}>{promise.status}</span>}
    >
      <p className="text-xs text-slate-600">
        Recorded {formatDateTime(promise.created_at)}
        {promise.owner ? ` by ${promise.owner.display_name}` : ""}
        {promise.reply ? " from a customer reply" : ""}. Counts as kept if the sheet shows the invoice paid by{" "}
        {formatDate(promise.grace_until)} (2-day grace).
      </p>
      {promise.note && <p className="text-xs text-slate-700">{promise.note}</p>}
      {promise.status === "OPEN" ? (
        <>
          <p className="text-xs text-slate-600">
            Chasing is paused while it is open. Checked every hour: if it is broken, the next reminder is one tone firmer
            and mentions the missed date. It is never sent without approval.
          </p>
          {isApprover ? (
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Why cancel? (required)"
                aria-label="Reason for cancelling the promise"
                className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-900"
              />
              <button type="button" disabled={busy || !note.trim()} onClick={() => void cancel()} className={buttonClass}>
                {busy && <Spinner className="h-3 w-3" />}
                Cancel promise
              </button>
            </div>
          ) : (
            <p className="text-xs text-slate-500">Only an approver can cancel an open promise.</p>
          )}
        </>
      ) : (
        <p className="text-xs text-slate-600">
          {promise.status === "KEPT" && "Paid on time."}
          {promise.status === "BROKEN" && "Not paid within the grace period; the next reminder is escalated."}
          {promise.status === "CANCELLED" && `Cancelled by ${promise.resolved_by ?? "an approver"}.`}{" "}
          {promise.resolution_note}
        </p>
      )}
      {error && <ErrorMessage message={error} />}
    </Box>
  );
}

export function DisputeCard({
  dispute,
  users,
  isApprover,
  onChanged,
}: {
  dispute: DisputeCase;
  users: AppUser[];
  isApprover: boolean;
  onChanged: (chasing: ChasingStatus | null) => void;
}) {
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<"VALID" | "INVALID" | "">("");
  const [resolution, setResolution] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = dispute.status === "OPEN";

  const act = async (action: () => Promise<DisputeCase & { chasing?: ChasingStatus }>) => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      setNote("");
      onChanged(result.chasing ?? null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box
      title={`Dispute: ${dispute.reason_display}`}
      badge={
        <span
          className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${
            open ? "border-rose-200 bg-rose-50 text-rose-800" : "border-slate-200 bg-slate-50 text-slate-600"
          }`}
        >
          {dispute.status_display}
        </span>
      }
    >
      <p className="text-sm text-slate-700">{dispute.description}</p>
      <p className="text-xs text-slate-600">
        Opened {formatDateTime(dispute.created_at)} by {dispute.opened_by}
        {dispute.reply ? " from a customer reply" : ""}.
        {open && " Nothing is drafted, approved or sent for this invoice until an approver resolves it."}
      </p>
      {!open && (
        <p className="text-xs text-slate-700">
          Resolved {dispute.resolved_at ? formatDateTime(dispute.resolved_at) : ""} by {dispute.resolved_by}: {dispute.resolution}
        </p>
      )}
      {open && (
        <label className="flex items-center gap-2 text-xs text-slate-600">
          Owner
          <UserSelect
            users={users}
            value={dispute.owner?.id ?? null}
            disabled={busy}
            label="Dispute owner"
            onChange={(id) => void act(() => assignDispute(dispute.id, id))}
          />
        </label>
      )}
      {dispute.notes.length > 0 && (
        <ul className="space-y-1 border-l-2 border-slate-200 pl-2 text-xs text-slate-700">
          {dispute.notes.map((entry) => (
            <li key={entry.id}>
              <span className="text-slate-500">
                {formatDateTime(entry.created_at)} · {entry.author}:
              </span>{" "}
              {entry.text}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Internal note"
          aria-label="Internal note"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-900"
        />
        <button
          type="button"
          disabled={busy || !note.trim()}
          onClick={() => void act(() => addDisputeNote(dispute.id, note))}
          className={buttonClass}
        >
          Add note
        </button>
      </div>
      {open &&
        ((dispute.can_resolve?.allowed ?? isApprover) ? (
          <div className="space-y-2 rounded-md bg-slate-50 p-2">
            <div className="flex flex-wrap gap-3 text-xs text-slate-700" role="radiogroup" aria-label="Outcome">
              <label className="flex items-center gap-1">
                <input type="radio" checked={outcome === "INVALID"} onChange={() => setOutcome("INVALID")} />
                Invoice stands (chasing may resume)
              </label>
              <label className="flex items-center gap-1">
                <input type="radio" checked={outcome === "VALID"} onChange={() => setOutcome("VALID")} />
                Customer is right (stop the sequence)
              </label>
            </div>
            <textarea
              value={resolution}
              onChange={(event) => setResolution(event.target.value)}
              placeholder="Resolution (required)"
              aria-label="Resolution"
              rows={2}
              className="block w-full rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-900"
            />
            <button
              type="button"
              disabled={busy || !outcome || !resolution.trim()}
              onClick={() => outcome && void act(() => resolveDispute(dispute.id, outcome, resolution))}
              className={primaryClass}
            >
              {busy && <Spinner className="h-3 w-3" />}
              Resolve dispute
            </button>
            <p className="text-[11px] text-slate-500">Resolving never sends anything; at most the next step is drafted for review.</p>
          </div>
        ) : (
          <p className="text-xs text-slate-500">{dispute.can_resolve?.reason || "Only an approver can resolve a dispute."}</p>
        ))}
      {error && <ErrorMessage message={error} />}
    </Box>
  );
}

function NewPromiseForm({ invoice, onDone }: { invoice: Invoice; onDone: () => void }) {
  const id = useId();
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState(invoice.amount);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await recordPromise({ invoice_id: invoice.id, promised_date: date, amount, note });
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Box title="Record a promise to pay">
      <div className="grid gap-2 sm:grid-cols-2">
        <label htmlFor={`${id}-date`} className="text-xs font-medium text-slate-600">
          Promised date (today to 60 days)
          <input id={`${id}-date`} type="date" value={date} onChange={(e) => setDate(e.target.value)} className={inputClass} />
        </label>
        <label htmlFor={`${id}-amount`} className="text-xs font-medium text-slate-600">
          Amount ({invoice.currency}, at most {invoice.amount})
          <input id={`${id}-amount`} value={amount} onChange={(e) => setAmount(e.target.value)} className={inputClass} />
        </label>
      </div>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Note" className={inputClass} />
      <p className="text-[11px] text-slate-500">Withdraws unsent reminders and pauses chasing; the current step stays pending.</p>
      <button type="button" disabled={busy || !date} onClick={() => void submit()} className={primaryClass}>
        {busy && <Spinner className="h-3 w-3" />}
        Record promise
      </button>
      {error && <ErrorMessage message={error} />}
    </Box>
  );
}

function NewDisputeForm({ invoice, users, onDone }: { invoice: Invoice; users: AppUser[]; onDone: () => void }) {
  const id = useId();
  const [reason, setReason] = useState("");
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await openDispute({ invoice_id: invoice.id, reason, description, owner_id: owner });
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Box title="Open a dispute">
      <label htmlFor={`${id}-reason`} className="block text-xs font-medium text-slate-600">
        Reason
        <select id={`${id}-reason`} value={reason} onChange={(e) => setReason(e.target.value)} className={inputClass}>
          <option value="">Choose…</option>
          {DISPUTE_REASONS.map(([value, name]) => (
            <option key={value} value={value}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="What does the customer dispute?"
        aria-label="Description"
        rows={2}
        className={inputClass}
      />
      <label className="flex items-center gap-2 text-xs text-slate-600">
        Owner
        <UserSelect users={users} value={owner} onChange={setOwner} label="Dispute owner" />
      </label>
      <p className="text-[11px] text-slate-500">Blocks drafting, approval and sending until an approver resolves it.</p>
      <button type="button" disabled={busy || !reason || !description.trim()} onClick={() => void submit()} className={primaryClass}>
        {busy && <Spinner className="h-3 w-3" />}
        Open dispute
      </button>
      {error && <ErrorMessage message={error} />}
    </Box>
  );
}

export function TaskRow({ task, users, onChanged }: { task: Task; users: AppUser[]; onChanged: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (action: () => Promise<Task>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const open = task.status === "OPEN";
  return (
    <li className="space-y-1.5 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-900">
          {task.title}{" "}
          <span className="text-xs text-slate-500">
            · {task.kind_display}
            {open && ` · due ${formatDateTime(task.due_at)}`}
          </span>
          {task.is_overdue && <span className="ml-1 text-xs font-medium text-rose-700">overdue</span>}
        </p>
        {open ? (
          <UserSelect
            users={users}
            value={task.assignee?.id ?? null}
            disabled={busy}
            label="Assignee"
            onChange={(id) => void act(() => assignTask(task.id, id))}
          />
        ) : (
          <span className="text-xs text-slate-500">
            {task.status === "DONE" ? `Done by ${task.done_by ?? "system"}` : `Cancelled by ${task.cancelled_by ?? "system"}`}
          </span>
        )}
      </div>
      {task.detail && <p className="text-xs text-slate-600">{task.detail}</p>}
      {task.completion_note && <p className="text-xs text-slate-600">Note: {task.completion_note}</p>}
      {open && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={task.step ? "Note (optional)" : "Note (required to cancel)"}
            aria-label="Task note"
            className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-900"
          />
          <button type="button" disabled={busy} onClick={() => void act(() => markTaskDone(task.id, note))} className={buttonClass}>
            {busy && <Spinner className="h-3 w-3" />}
            Mark done
          </button>
          {!task.step && (
            <button type="button" disabled={busy || !note.trim()} onClick={() => void act(() => cancelTask(task.id, note))} className={buttonClass}>
              Cancel task
            </button>
          )}
        </div>
      )}
      {error && <ErrorMessage message={error} />}
    </li>
  );
}

function NewTaskForm({ invoice, users, onDone }: { invoice: Invoice; users: AppUser[]; onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [assignee, setAssignee] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await createTask(invoice.id, title, due, assignee);
      setTitle("");
      setDue("");
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="New follow-up task"
        aria-label="New task title"
        className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-900"
      />
      <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date" className="rounded-md border border-slate-300 px-2 py-1 text-xs" />
      <UserSelect users={users} value={assignee} onChange={setAssignee} label="Assignee" />
      <button type="button" disabled={busy || !title.trim()} onClick={() => void submit()} className={buttonClass}>
        Add task
      </button>
      {error && <ErrorMessage message={error} />}
    </div>
  );
}

/** Promises, disputes, holds, tasks and their history for one invoice (M11). */
export function CasePanel({ invoice: initial, onChanged }: { invoice: Invoice; onChanged?: () => void }) {
  const user = useCurrentUser();
  // Re-read after every change so holds and open cases are current, not the dashboard's snapshot.
  const [invoice, setInvoice] = useState<Invoice>(initial);
  const isApprover = user.role === "APPROVER" || user.role === "ADMIN";
  const [promises, setPromises] = useState<PromiseToPay[]>([]);
  const [disputes, setDisputes] = useState<DisputeCase[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [users, setUsers] = useState<AppUser[]>([]);
  const [history, setHistory] = useState<HistoryEvent[]>([]);
  const [form, setForm] = useState<"promise" | "dispute" | null>(null);
  const [chasing, setChasing] = useState<ChasingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    Promise.all([
      getInvoice(invoice.id, signal),
      getPromises(invoice.id, signal),
      getDisputes(invoice.id, signal),
      getInvoiceTasks(invoice.id, signal),
      getUsers(signal),
      getInvoiceHistory(invoice.id, signal),
    ])
      .then(([fresh, p, d, t, u, h]) => {
        setInvoice(fresh);
        setPromises(p);
        setDisputes(d);
        setTasks(t);
        setUsers(u);
        setHistory(h.filter((event) => CASE_EVENTS.has(event.type)).slice(0, 15));
        setError(null);
      })
      .catch((err: unknown) => {
        if (!signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [invoice.id, reload]);

  const changed = useCallback(
    (result: ChasingStatus | null = null) => {
      setChasing(result);
      setForm(null);
      setReload((n) => n + 1);
      onChanged?.();
    },
    [onChanged],
  );

  const openPromise = promises.find((p) => p.status === "OPEN");
  const openDisputeCase = disputes.find((d) => d.status === "OPEN");
  const chaseable = invoice.status === "UNPAID" && invoice.in_sheet;
  const visiblePromises = promises.filter((p) => p.status === "OPEN" || p === promises[0]);
  const visibleDisputes = disputes.filter((d) => d.status === "OPEN" || d === disputes[0]);

  return (
    <section className="space-y-3" aria-label="Promises, disputes and tasks">
      <h3 className="text-sm font-semibold text-slate-900">Promises, disputes and tasks</h3>
      {error && <ErrorMessage message={`Could not load: ${error}`} />}
      <ChasingNote chasing={chasing} />

      {invoice.holds.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <p className="font-semibold">Chasing is stopped by {invoice.holds.length === 1 ? "a hold" : `${invoice.holds.length} holds`}:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {invoice.holds.map((hold) => (
              <li key={hold.id}>
                {hold.reason_display} ({hold.source}). {hold.release_rule}{" "}
                {hold.reply_id && (
                  <Link href={links.reply(hold.reply_id)} className="font-medium underline">
                    Review the reply
                  </Link>
                )}
                {hold.dispute_id && (
                  <Link href={links.dispute(hold.dispute_id)} className="font-medium underline">
                    Open the dispute
                  </Link>
                )}
              </li>
            ))}
          </ul>
          {invoice.holds.length > 1 && <p className="mt-1">Chasing resumes only when every hold has ended.</p>}
        </div>
      )}

      {visibleDisputes.map((dispute) => (
        <DisputeCard key={dispute.id} dispute={dispute} users={users} isApprover={isApprover} onChanged={changed} />
      ))}
      {visiblePromises.map((promise) => (
        <PromiseCard key={promise.id} promise={promise} isApprover={isApprover} onChanged={changed} />
      ))}

      {chaseable && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={Boolean(openPromise || openDisputeCase)}
            title={openPromise ? "An open promise already exists" : openDisputeCase ? "The invoice is disputed" : undefined}
            onClick={() => setForm(form === "promise" ? null : "promise")}
            className={buttonClass}
          >
            Record a promise
          </button>
          <button
            type="button"
            disabled={Boolean(openDisputeCase)}
            title={openDisputeCase ? "A dispute is already open" : undefined}
            onClick={() => setForm(form === "dispute" ? null : "dispute")}
            className={buttonClass}
          >
            Open a dispute
          </button>
        </div>
      )}
      {form === "promise" && <NewPromiseForm invoice={invoice} onDone={() => changed()} />}
      {form === "dispute" && <NewDisputeForm invoice={invoice} users={users} onDone={() => changed()} />}

      <Box title="Tasks">
        {tasks.length === 0 ? (
          <p className="text-xs text-slate-500">No tasks for this invoice.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} users={users} onChanged={() => changed()} />
            ))}
          </ul>
        )}
        <NewTaskForm invoice={invoice} users={users} onDone={() => changed()} />
      </Box>

      {history.length > 0 && (
        <details className="rounded-lg border border-slate-200 px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-slate-700">Recent history</summary>
          <ol className="mt-2 space-y-1 text-xs text-slate-600">
            {history.map((event) => (
              <li key={event.id}>
                {formatDateTime(event.occurred_at)} · {event.type_label}
                {event.actor_username ? ` by ${event.actor_username}` : event.actor_type !== "USER" ? ` (${event.actor_type.toLowerCase()})` : ""}
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
