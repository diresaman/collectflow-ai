"use client";

import { useCallback, useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import { Card, ErrorMessage, Skeleton, Spinner } from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { DISPUTE_REASONS, getUsers, openDispute, recordPromise, type AppUser } from "@/lib/api/cases";
import { waitForJob } from "@/lib/api/jobs";
import {
  EXTRACTED_FIELDS,
  LABEL_EFFECTS,
  LABEL_NAMES,
  REPLY_LABELS,
  TRIAGE_REASONS,
  classifyReply,
  getReplies,
  getReply,
  releaseHold,
  retryClassification,
  type ReplyDetail,
  type ReplyFilter,
  type ReplyLabel,
  type ReplyRow,
} from "@/lib/api/replies";
import { formatDateTime } from "@/lib/format";

const FILTERS: { key: ReplyFilter; label: string }[] = [
  { key: "triage", label: "Needs review" },
  { key: "handled", label: "Handled automatically" },
  { key: "all", label: "All replies" },
];

const STATE_BADGES: Record<ReplyRow["state"], string> = {
  RECEIVED: "bg-sky-50 text-sky-800 border-sky-200",
  TRIAGE: "bg-amber-50 text-amber-900 border-amber-300",
  ACTIONED: "bg-emerald-50 text-emerald-800 border-emerald-200",
  RESOLVED: "bg-slate-50 text-slate-700 border-slate-200",
};
const STATE_NAMES: Record<ReplyRow["state"], string> = {
  RECEIVED: "Classifying",
  TRIAGE: "Needs review",
  ACTIONED: "Handled",
  RESOLVED: "Resolved",
};
const SENDER_NOTES: Record<ReplyRow["sender_check"], string> = {
  MATCH: "invoice contact",
  SAME_DOMAIN: "same domain as the contact",
  MISMATCH: "unknown sender",
};

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error.";
}

function confidenceText(value: number | null): string {
  return value === null ? "" : `${Math.round(value * 100)}%`;
}

function ReplyList({
  rows,
  selected,
  onSelect,
}: {
  rows: ReplyRow[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="px-4 py-6 text-sm text-slate-500">No replies here.</p>;
  }
  return (
    <ul className="divide-y divide-slate-100" role="listbox" aria-label="Replies">
      {rows.map((row) => (
        <li key={row.id}>
          <button
            type="button"
            role="option"
            aria-selected={row.id === selected}
            onClick={() => onSelect(row.id)}
            className={`block w-full px-4 py-3 text-left hover:bg-slate-50 ${row.id === selected ? "bg-indigo-50/60" : ""}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-semibold text-slate-900">{row.client_name}</span>
              <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${STATE_BADGES[row.state]}`}>
                {STATE_NAMES[row.state]}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-slate-500">
              {row.invoice_external_id} · {formatDateTime(row.received_at)}
            </p>
            <p className="mt-1 line-clamp-2 text-sm text-slate-700">{row.snippet || row.subject}</p>
            <p className="mt-1 text-xs text-slate-600">
              {row.label ? `${LABEL_NAMES[row.label]}${row.confidence !== null ? ` · ${confidenceText(row.confidence)}` : ""}` : "Not classified"}
              {row.on_hold ? <span className="ml-2 font-medium text-amber-700">· chasing on hold</span> : null}
            </p>
          </button>
        </li>
      ))}
    </ul>
  );
}

function suggestion(reply: ReplyDetail, key: string): string {
  const value = reply.extracted[key];
  return typeof value === "string" ? value : "";
}

/**
 * Turning a promise or dispute reply into the business record (M11). The AI's extracted values only
 * prefill the form; the person confirms or corrects them, and the backend validates them again.
 */
function ReplyCaseActions({
  reply,
  label,
  users,
  onChanged,
}: {
  reply: ReplyDetail;
  label: ReplyLabel | "";
  users: AppUser[];
  onChanged: () => void;
}) {
  const [date, setDate] = useState(suggestion(reply, "promised_date"));
  const [amount, setAmount] = useState(suggestion(reply, "promised_amount").replace(/[^\d.]/g, ""));
  const [reason, setReason] = useState("");
  const [description, setDescription] = useState(suggestion(reply, "dispute_reason"));
  const [owner, setOwner] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (reply.promise) {
    return (
      <p className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
        <span className="font-semibold">Promise recorded</span> ({reply.promise.status.toLowerCase()}): pay{" "}
        {reply.promise.amount} {reply.promise.currency} by {reply.promise.promised_date}. Manage it from invoice{" "}
        {reply.invoice_external_id} on the dashboard.
      </p>
    );
  }
  if (reply.dispute) {
    return (
      <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">
        <span className="font-semibold">Dispute {reply.dispute.status_display.toLowerCase()}</span>:{" "}
        {reply.dispute.reason_display}. Manage it from invoice {reply.invoice_external_id} on the dashboard.
      </p>
    );
  }
  if (label !== "PROMISE_TO_PAY" && label !== "DISPUTE") return null;

  const submit = async (action: () => Promise<unknown>) => {
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
  const field = "mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900";

  if (label === "PROMISE_TO_PAY") {
    return (
      <section className="space-y-2 rounded-lg border border-sky-200 bg-sky-50/50 p-3" aria-label="Confirm the promise">
        <h3 className="text-sm font-semibold text-slate-900">Confirm the promise</h3>
        <p className="text-xs text-slate-600">
          Check the date and amount against the reply (the AI&apos;s values are only suggestions). Recording it pauses
          chasing until the promise is kept or broken (2-day grace), and withdraws unsent reminders.
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-xs font-medium text-slate-600">
            Promised date
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={field} />
          </label>
          <label className="text-xs font-medium text-slate-600">
            Amount (empty = full invoice)
            <input value={amount} onChange={(e) => setAmount(e.target.value)} className={field} />
          </label>
        </div>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Promise note" className={field} />
        <button
          type="button"
          disabled={busy || !date}
          onClick={() =>
            void submit(() =>
              recordPromise({ invoice_id: reply.invoice, reply_id: reply.id, promised_date: date, amount: amount || undefined, note }),
            )
          }
          className="inline-flex items-center gap-2 rounded-md bg-sky-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {busy && <Spinner />}
          Record promise
        </button>
        {error && <ErrorMessage message={error} />}
      </section>
    );
  }
  return (
    <section className="space-y-2 rounded-lg border border-rose-200 bg-rose-50/50 p-3" aria-label="Open a dispute">
      <h3 className="text-sm font-semibold text-slate-900">Open a dispute case</h3>
      <p className="text-xs text-slate-600">
        Blocks drafting, approval and sending for this invoice until an approver resolves the case.
      </p>
      <label className="block text-xs font-medium text-slate-600">
        Reason
        <select value={reason} onChange={(e) => setReason(e.target.value)} className={field}>
          <option value="">Choose…</option>
          {DISPUTE_REASONS.map(([value, name]) => (
            <option key={value} value={value}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} aria-label="Description" className={field} />
      <label className="flex items-center gap-2 text-xs text-slate-600">
        Owner
        <select
          value={owner ?? ""}
          onChange={(e) => setOwner(e.target.value ? Number(e.target.value) : null)}
          className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
        >
          <option value="">Unassigned</option>
          {users.map((user) => (
            <option key={user.id} value={user.id}>
              {user.display_name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={busy || !reason || !description.trim()}
        onClick={() =>
          void submit(() => openDispute({ invoice_id: reply.invoice, reply_id: reply.id, reason, description, owner_id: owner }))
        }
        className="inline-flex items-center gap-2 rounded-md bg-rose-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-rose-600 disabled:opacity-50"
      >
        {busy && <Spinner />}
        Open dispute
      </button>
      {error && <ErrorMessage message={error} />}
    </section>
  );
}

function ReplyPanel({
  reply,
  users,
  onChanged,
}: {
  reply: ReplyDetail;
  users: AppUser[];
  onChanged: (updated: ReplyDetail) => void;
}) {
  const user = useCurrentUser();
  const isApprover = user.role === "APPROVER" || user.role === "ADMIN";
  // The panel is keyed by the reply's revision, so this state starts fresh for each reply and after each change.
  const [label, setLabel] = useState<ReplyLabel | "">(reply.label);
  const [fields, setFields] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      EXTRACTED_FIELDS.map(([key]) => [key, typeof reply.extracted[key] === "string" ? (reply.extracted[key] as string) : ""]),
    ),
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      const picked = REPLY_LABELS[Number(event.key) - 1];
      if (picked) setLabel(picked);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const activeHolds = reply.holds.filter((hold) => !hold.released_at);
  const protectedHold = activeHolds.some((hold) => hold.protected);
  const releasesHold = label === "REQUESTS_INFO" || label === "OUT_OF_OFFICE" || label === "OTHER";
  const needsApprover = protectedHold && releasesHold;
  const humanDone = reply.label_source === "HUMAN";

  const act = async (action: () => Promise<ReplyDetail>) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await action());
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await retryClassification(reply.id);
      await waitForJob(job_id);
      onChanged(await getReply(reply.id));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5 p-5">
      <header className="space-y-1">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold text-slate-900">{reply.subject || "(no subject)"}</h2>
          <a href={reply.gmail_url} target="_blank" rel="noreferrer" className="text-sm font-medium text-indigo-600 hover:underline">
            Open conversation in Gmail
          </a>
        </div>
        <p className="text-sm text-slate-600">
          From {reply.sender_name ? `${reply.sender_name} ` : ""}&lt;{reply.sender_email}&gt;
          <span className={reply.sender_check === "MISMATCH" ? "font-medium text-rose-700" : "text-slate-500"}>
            {" "}
            ({SENDER_NOTES[reply.sender_check]})
          </span>
        </p>
        <p className="text-sm text-slate-600">
          Invoice <span className="font-medium text-slate-900">{reply.invoice_external_id}</span> · {reply.client_name} · received{" "}
          {formatDateTime(reply.received_at)}
          {reply.replied_to ? ` · in reply to step ${reply.replied_to.step ?? "?"} ("${reply.replied_to.subject}")` : ""}
        </p>
      </header>

      {activeHolds.length > 0 && (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <span className="font-semibold">Chasing is on hold</span> for{" "}
          {activeHolds.map((hold) => `${hold.invoice_external_id} (${hold.reason_display.toLowerCase()})`).join(", ")}. No reminder
          is drafted, approved or sent until this is resolved.
          {protectedHold && " Only an approver can resume chasing after a promise, dispute or payment claim."}
        </p>
      )}

      <section aria-label="Reply text">
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-sm text-slate-800">
          {reply.body_purged_at ? "The reply text was removed after the retention period." : reply.body || "(empty)"}
        </pre>
        {reply.body_truncated && <p className="mt-1 text-xs text-slate-500">The text was shortened when it was stored.</p>}
      </section>

      <section className="rounded-lg border border-slate-200 p-3" aria-label="AI suggestion">
        <h3 className="text-sm font-semibold text-slate-900">
          {reply.label_source === "HUMAN" ? "Classified by a person" : "AI suggestion"}
        </h3>
        {reply.label ? (
          <p className="mt-1 text-sm text-slate-700">
            <span className="font-medium">{LABEL_NAMES[reply.label]}</span>
            {reply.confidence !== null && ` · confidence ${confidenceText(reply.confidence)}`}
            {reply.rationale && <span className="block text-slate-600">{reply.rationale}</span>}
          </p>
        ) : (
          <p className="mt-1 text-sm text-slate-600">Not classified yet.</p>
        )}
        {reply.triage_reason && (
          <p className="mt-2 text-sm text-amber-800">
            <span className="font-medium">Why it needs review:</span> {TRIAGE_REASONS[reply.triage_reason] ?? reply.triage_reason}
          </p>
        )}
        {!humanDone && (reply.state === "TRIAGE" || reply.state === "RECEIVED") && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void retry()}
            className="mt-2 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            Ask the AI again
          </button>
        )}
      </section>

      <section className="space-y-3" aria-label="Classify">
        <h3 className="text-sm font-semibold text-slate-900">Your decision</h3>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Label">
          {REPLY_LABELS.map((value, index) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={label === value}
              onClick={() => setLabel(value)}
              className={`rounded-md border px-2.5 py-1 text-sm ${
                label === value
                  ? "border-indigo-500 bg-indigo-600 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              <span className="mr-1 text-xs opacity-70">{index + 1}</span>
              {LABEL_NAMES[value]}
            </button>
          ))}
        </div>
        {label && <p className="text-xs text-slate-600">{LABEL_EFFECTS[label]}</p>}

        <div className="grid gap-2 sm:grid-cols-2">
          {EXTRACTED_FIELDS.map(([key, name, kind]) => (
            <label key={key} className="text-xs font-medium text-slate-600">
              {name}
              <input
                type={kind}
                value={fields[key] ?? ""}
                onChange={(event) => setFields((current) => ({ ...current, [key]: event.target.value }))}
                className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
              />
            </label>
          ))}
        </div>
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder={needsApprover ? "Note (required to resume chasing)" : "Note (optional)"}
          aria-label="Note"
          className="block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy || !label || (needsApprover && (!isApprover || !note.trim()))}
            title={needsApprover && !isApprover ? "Only an approver can resume chasing after this reply" : undefined}
            onClick={() => label && void act(() => classifyReply(reply.id, label, fields, note))}
            className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {busy && <Spinner />}
            {label && label === reply.label && reply.label_source === "AI" ? "Confirm" : "Apply"} {label ? LABEL_NAMES[label] : "label"}
          </button>
          {activeHolds.length > 0 && (
            <button
              type="button"
              disabled={busy || (protectedHold && (!isApprover || !note.trim()))}
              title={protectedHold && !isApprover ? "Only an approver can resume chasing after this reply" : undefined}
              onClick={() => void act(() => releaseHold(reply.id, note))}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Resume chasing
            </button>
          )}
          {needsApprover && !isApprover && (
            <span className="text-xs text-amber-800">An approver must resume chasing after a promise, dispute or payment claim.</span>
          )}
        </div>
        {error && <ErrorMessage message={error} />}
      </section>

      <ReplyCaseActions
        reply={reply}
        label={label || reply.label}
        users={users}
        onChanged={() => void getReply(reply.id).then(onChanged)}
      />

      {reply.tasks.length > 0 && (
        <section aria-label="Tasks">
          <h3 className="text-sm font-semibold text-slate-900">Tasks</h3>
          <ul className="mt-1 space-y-1 text-sm text-slate-700">
            {reply.tasks.map((task) => (
              <li key={task.id}>
                {task.title}
                {task.status === "DONE" ? " · done" : task.status === "CANCELLED" ? " · cancelled" : " · open (Dashboard → Tasks)"}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Processing history">
        <h3 className="text-sm font-semibold text-slate-900">History</h3>
        <ol className="mt-1 space-y-1 text-xs text-slate-600">
          {reply.classifications.map((c) => (
            <li key={c.id}>
              {formatDateTime(c.created_at)} · {c.source === "AI" ? "AI" : c.actor ?? "person"}:{" "}
              {c.label ? LABEL_NAMES[c.label] : "no label"}
              {c.confidence !== null ? ` (${confidenceText(c.confidence)})` : ""} → {c.decision}
              {c.note ? ` · “${c.note}”` : ""}
            </li>
          ))}
          {reply.history.map((event) => (
            <li key={event.id}>
              {formatDateTime(event.occurred_at)} · {event.label}
              {event.invoice_external_id ? ` (${event.invoice_external_id})` : ""}
              {event.actor ? ` by ${event.actor}` : ""}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

export function InboxView() {
  const [filter, setFilter] = useState<ReplyFilter>("triage");
  const [rows, setRows] = useState<ReplyRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReplyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [users, setUsers] = useState<AppUser[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    getUsers(controller.signal)
      .then(setUsers)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    getReplies(filter, controller.signal)
      .then((data) => {
        setRows(data);
        setError(null);
        setSelected((current) => (current && data.some((row) => row.id === current) ? current : data[0]?.id ?? null));
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [filter, refresh]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    getReply(selected, controller.signal)
      .then(setDetail)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [selected]);

  const shown = detail && detail.id === selected ? detail : null;

  const move = useCallback(
    (step: number) => {
      if (!rows || rows.length === 0) return;
      const index = rows.findIndex((row) => row.id === selected);
      const next = rows[Math.min(Math.max(index + step, 0), rows.length - 1)];
      if (next) setSelected(next.id);
    },
    [rows, selected],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "j") move(1);
      if (event.key === "k") move(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Inbox</h1>
          <p className="text-sm text-slate-600">
            Customer replies to CollectFlow reminders. A reply stops chasing at once; the AI suggests a label and a person
            reviews anything uncertain. Keys: j/k to move, 1–7 to pick a label.
          </p>
        </div>
        <div className="flex gap-1" role="tablist" aria-label="Filter">
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
      {error && <ErrorMessage message={error} />}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <Card className="max-h-[75vh] overflow-auto">
          {rows === null ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : (
            <ReplyList rows={rows} selected={selected} onSelect={setSelected} />
          )}
        </Card>
        <Card>
          {shown ? (
            <ReplyPanel
              key={`${shown.id}:${shown.classifications.length}:${shown.state}:${shown.holds.filter((h) => !h.released_at).length}`}
              reply={shown}
              users={users}
              onChanged={(updated) => {
                setDetail(updated);
                setRefresh((n) => n + 1);
              }}
            />
          ) : (
            <p className="p-6 text-sm text-slate-500">{rows && rows.length === 0 ? "Nothing to review." : "Select a reply."}</p>
          )}
        </Card>
      </div>
    </div>
  );
}
