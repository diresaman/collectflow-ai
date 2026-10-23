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

import { DISPUTE_STATUS, PROMISE_STATUS, labelFor } from "@/lib/status";

import { ErrorMessage, Spinner, StatusPill, buttonStyles, inputStyles } from "./ui";

const CASE_EVENTS = new Set([
  "PROMISE_RECORDED", "PROMISE_KEPT", "PROMISE_BROKEN", "PROMISE_CANCELLED", "SEQUENCE_ESCALATED",
  "DISPUTE_OPENED", "DISPUTE_ASSIGNED", "DISPUTE_NOTE_ADDED", "DISPUTE_RESOLVED", "FOLLOW_UP_CANCELLED",
  "CHASE_HOLD_PLACED", "CHASE_HOLD_RELEASED", "TASK_CREATED", "TASK_ASSIGNED", "TASK_DONE", "TASK_CANCELLED",
  "TASK_OVERDUE", "REPLY_RECEIVED", "REPLY_REVIEWED",
]);



const inputClass = `mt-1.5 ${inputStyles.input}`;
const buttonClass = buttonStyles.secondarySm;
const primaryClass = buttonStyles.primarySm;

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function Box({ title, children, badge }: { title: string; children: ReactNode; badge?: ReactNode }) {
  return (
    <div className="space-y-3 rounded-xl border border-line p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-ink">{title}</h4>
        {badge}
      </div>
      {children}
    </div>
  );
}

export function ChasingNote({ chasing }: { chasing: ChasingStatus | null }) {
  if (!chasing) return null;
  return (
    <p role="status" className="rounded-lg border border-line bg-slate-50 px-3 py-2 text-sm text-ink">
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
      className="h-9 rounded-lg border border-line-strong bg-white px-2.5 text-sm text-ink focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/25 disabled:opacity-60"
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
      badge={<StatusPill label={labelFor(PROMISE_STATUS, promise.status).label} tone={labelFor(PROMISE_STATUS, promise.status).tone} />}
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
  impact,
  bare = false,
}: {
  dispute: DisputeCase;
  users: AppUser[];
  isApprover: boolean;
  onChanged: (chasing: ChasingStatus | null) => void;
  /** Shown before the resolution (the Disputes page puts "collection impact" here). */
  impact?: ReactNode;
  /** Without the outer border, for a page that frames it itself. */
  bare?: boolean;
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

  const status = labelFor(DISPUTE_STATUS, dispute.status);
  const sub = "space-y-2 border-t border-line pt-4";
  const subTitle = "text-sm font-semibold text-ink";
  const body = (
    <div className="space-y-4">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className={subTitle}>Dispute: {dispute.reason_display}</h4>
          {!bare && <StatusPill label={status.label} tone={status.tone} />}
        </div>
        <p className="text-sm text-ink">{dispute.description}</p>
        <p className="text-xs text-ink-subtle">
          Opened {formatDateTime(dispute.created_at)} by {dispute.opened_by}
          {dispute.reply ? " from a customer reply" : ""}.
        </p>
      </div>

      <div className={sub}>
        <h4 className={subTitle}>Owner and internal notes</h4>
        {open ? (
          <label className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
            Owner
            <UserSelect
              users={users}
              value={dispute.owner?.id ?? null}
              disabled={busy}
              label="Dispute owner"
              onChange={(id) => void act(() => assignDispute(dispute.id, id))}
            />
          </label>
        ) : (
          <p className="text-sm text-ink-muted">Owner: {dispute.owner?.display_name ?? "none"}</p>
        )}
        {dispute.notes.length > 0 ? (
          <ul className="space-y-2 border-l-2 border-line pl-3 text-sm text-ink">
            {dispute.notes.map((entry) => (
              <li key={entry.id}>
                <span className="text-xs text-ink-subtle">
                  {formatDateTime(entry.created_at)} · {entry.author}
                </span>
                <span className="block">{entry.text}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-subtle">No notes yet. Notes are internal and never sent to the customer.</p>
        )}
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Add an internal note"
            aria-label="Internal note"
            className={`${inputStyles.input} sm:flex-1`}
          />
          <button
            type="button"
            disabled={busy || !note.trim()}
            onClick={() => void act(() => addDisputeNote(dispute.id, note))}
            className={buttonStyles.secondary}
          >
            Add note
          </button>
        </div>
      </div>

      {impact}

      {open ? (
        (dispute.can_resolve?.allowed ?? isApprover) ? (
          <div className="space-y-3 rounded-xl border border-brand-200 bg-brand-50/40 p-4" aria-label="Resolve the dispute">
            <div>
              <h4 className={subTitle}>Resolution</h4>
              <p className="mt-0.5 text-xs text-ink-muted">
                For approvers. Resolving never sends anything; at most the next step is drafted for review. Other holds on the
                invoice stay in place.
              </p>
            </div>
            <div className="flex flex-col gap-2 text-sm text-ink sm:flex-row sm:gap-6" role="radiogroup" aria-label="Outcome">
              <label className="flex items-center gap-2">
                <input type="radio" checked={outcome === "INVALID"} onChange={() => setOutcome("INVALID")} className="h-4 w-4 accent-brand-600" />
                Invoice stands (chasing may resume)
              </label>
              <label className="flex items-center gap-2">
                <input type="radio" checked={outcome === "VALID"} onChange={() => setOutcome("VALID")} className="h-4 w-4 accent-brand-600" />
                Customer is right (stop reminders)
              </label>
            </div>
            <textarea
              value={resolution}
              onChange={(event) => setResolution(event.target.value)}
              placeholder="What was decided and why (required)"
              aria-label="Resolution"
              rows={2}
              className={inputStyles.textarea}
            />
            <div className="flex justify-end">
              <button
                type="button"
                disabled={busy || !outcome || !resolution.trim()}
                onClick={() => outcome && void act(() => resolveDispute(dispute.id, outcome, resolution))}
                className={buttonStyles.primary}
              >
                {busy && <Spinner className="h-3.5 w-3.5" />}
                Resolve dispute
              </button>
            </div>
          </div>
        ) : (
          <div className={sub}>
            <h4 className={subTitle}>Resolution</h4>
            <p className="text-sm text-ink-muted">{dispute.can_resolve?.reason || "Only an approver can resolve a dispute."}</p>
          </div>
        )
      ) : (
        <div className={sub}>
          <h4 className={subTitle}>Resolution</h4>
          <p className="text-sm text-ink">
            {status.label}. {dispute.resolution}
          </p>
          <p className="text-xs text-ink-subtle">
            Resolved {dispute.resolved_at ? formatDateTime(dispute.resolved_at) : ""} by {dispute.resolved_by}
          </p>
        </div>
      )}
      {error && <ErrorMessage message={error} />}
    </div>
  );
  return bare ? body : <div className="rounded-xl border border-line p-4">{body}</div>;
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

/**
 * Completing or cancelling an open task: one note, with the rule for it stated inline (optional to complete,
 * required to cancel; call tasks of a reminder step cannot be cancelled).
 */
export function TaskCompletion({ task, onChanged }: { task: Task; onChanged: (message?: string) => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noteId = useId();
  const act = async (action: () => Promise<Task>, message?: string) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged(message);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2">
      <label htmlFor={noteId} className="block text-sm font-medium text-ink">
        What was done?{" "}
        <span className="font-normal text-ink-subtle">{task.step ? "(optional)" : "(optional to complete, required to cancel)"}</span>
      </label>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          id={noteId}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Called; they will pay Friday"
          aria-label="Task note"
          className={`${inputStyles.input} sm:flex-1`}
        />
        <div className="flex gap-2">
          {!task.step && (
            <button
              type="button"
              disabled={busy || !note.trim()}
              title={!note.trim() ? "Write why the task is cancelled first" : undefined}
              onClick={() => void act(() => cancelTask(task.id, note), `"${task.title}" cancelled.`)}
              className={buttonStyles.secondary}
            >
              Cancel task
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(
                () => markTaskDone(task.id, note),
                `"${task.title}" marked complete.${task.step ? " The reminder schedule moves on to its next step." : ""}`,
              )
            }
            className={buttonStyles.primary}
          >
            {busy && <Spinner className="h-3.5 w-3.5" />}
            Mark task complete
          </button>
        </div>
      </div>
      <p className="text-xs text-ink-subtle">
        The note is kept in the history.
        {task.step
          ? " Completing this call task completes its reminder step; it cannot be cancelled."
          : " Completing a task never resumes chasing by itself."}
      </p>
      {error && <ErrorMessage message={error} />}
    </div>
  );
}

/** A task inside the invoice drawer: title, owner, due date, then the completion controls. */
export function TaskRow({ task, users, onChanged }: { task: Task; users: AppUser[]; onChanged: (message?: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assign = async (id: number | null) => {
    setBusy(true);
    setError(null);
    try {
      await assignTask(task.id, id);
      onChanged(`"${task.title}" ${id === null ? "is now unassigned" : "was assigned"}.`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const open = task.status === "OPEN";
  return (
    <li className="space-y-3 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink">{task.title}</p>
          <p className="mt-0.5 text-xs text-ink-subtle">
            {task.kind_display}
            {open && ` · due ${formatDateTime(task.due_at)}`}
            {task.is_overdue && <span className="ml-1 font-semibold text-danger-700">· Overdue</span>}
          </p>
        </div>
        {open ? (
          <UserSelect users={users} value={task.assignee?.id ?? null} disabled={busy} label="Assignee" onChange={(id) => void assign(id)} />
        ) : (
          <span className="text-xs text-ink-subtle">
            {task.status === "DONE" ? `Done by ${task.done_by ?? "system"}` : `Cancelled by ${task.cancelled_by ?? "system"}`}
          </span>
        )}
      </div>
      {task.detail && <p className="text-sm text-ink-muted">{task.detail}</p>}
      {task.completion_note && <p className="text-sm text-ink-muted">Note: {task.completion_note}</p>}
      {open && <TaskCompletion task={task} onChanged={onChanged} />}
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
export function CasePanel({
  invoice: initial,
  onChanged,
  compact = false,
}: {
  invoice: Invoice;
  onChanged?: () => void;
  /** Inside the invoice drawer: holds and history are shown elsewhere there. */
  compact?: boolean;
}) {
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
    <section className="space-y-4" aria-label="Promises, disputes and tasks">
      <h3 className="text-sm font-semibold text-ink">Promises, disputes and tasks</h3>
      {error && <ErrorMessage message={`Could not load: ${error}`} />}
      <ChasingNote chasing={chasing} />

      {!compact && invoice.holds.length > 0 && (
        <div className="rounded-lg border border-warning-300 bg-warning-50 px-3 py-2 text-xs text-warning-900">
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

      {!compact && history.length > 0 && (
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
