"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import {
  Card,
  EmptyState,
  ErrorMessage,
  Icon,
  Notice,
  PageHeader,
  SegmentedFilter,
  Skeleton,
  Spinner,
  StatusPill,
  buttonStyles,
  inputStyles,
  type PillTone,
} from "@/components/dashboard/ui";
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
import { formatDateTime, formatShortDateTime } from "@/lib/format";
import { REPLY_STATE } from "@/lib/status";
import { links, useUrlParam } from "@/lib/url";

const FILTERS: { key: ReplyFilter; label: string }[] = [
  { key: "triage", label: "Needs review" },
  { key: "handled", label: "Handled automatically" },
  { key: "all", label: "All" },
];

const STATE_TONES: Record<ReplyRow["state"], PillTone> = {
  RECEIVED: "info",
  TRIAGE: "warn",
  ACTIONED: "good",
  RESOLVED: "neutral",
};
const STATE_NAMES: Record<ReplyRow["state"], string> = {
  RECEIVED: REPLY_STATE.RECEIVED.label,
  TRIAGE: REPLY_STATE.TRIAGE.label,
  ACTIONED: REPLY_STATE.ACTIONED.label,
  RESOLVED: REPLY_STATE.RESOLVED.label,
};
/** Only the fields that matter for the chosen label (the same ones the backend keeps for it). */
const LABEL_FIELDS: Record<ReplyLabel, string[]> = {
  PROMISE_TO_PAY: [],
  DISPUTE: [],
  CLAIMS_PAID: ["paid_date", "paid_amount", "payment_reference"],
  REQUESTS_INFO: ["requested_info"],
  OUT_OF_OFFICE: ["return_date"],
  WRONG_CONTACT: ["new_contact_email"],
  OTHER: [],
};
/** Plain words for the decision codes stored in the classification log. */
function decisionText(decision: string): string {
  if (decision.startsWith("triage:")) {
    const reason = decision.slice("triage:".length);
    const why = (TRIAGE_REASONS[reason] ?? reason).replace(/\.$/, "");
    return `sent to a person: ${why.charAt(0).toLowerCase()}${why.slice(1)}`;
  }
  if (decision.startsWith("failed:")) return "the AI could not classify it; a person decides";
  return (
    {
      applied: "applied automatically",
      retry_scheduled: "the AI was unavailable; it will try again",
      human: "decision saved",
      promise_recorded: "promise recorded",
      dispute_opened: "dispute opened",
    }[decision] ?? decision.replace(/_/g, " ")
  );
}
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
  filtered,
}: {
  rows: ReplyRow[];
  selected: string | null;
  onSelect: (id: string) => void;
  filtered: boolean;
}) {
  if (rows.length === 0) {
    return filtered ? (
      <EmptyState compact title="No replies match your search">Try a customer name or an invoice number.</EmptyState>
    ) : (
      <EmptyState title="No replies here" icon="mail">
        New customer replies to CollectFlow emails appear here within about 5 minutes when they need your attention.
      </EmptyState>
    );
  }
  return (
    <ul className="divide-y divide-slate-100" role="listbox" aria-label="Replies">
      {rows.map((row) => {
        const active = row.id === selected;
        return (
          <li key={row.id}>
            <button
              type="button"
              role="option"
              aria-selected={active}
              onClick={() => onSelect(row.id)}
              className={`relative block w-full px-4 py-3.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 ${
                active ? "bg-brand-50/70" : "hover:bg-slate-50"
              }`}
            >
              {active && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand-600" aria-hidden="true" />}
              <div className="flex items-start justify-between gap-3">
                <span className="truncate text-sm font-semibold text-ink">{row.client_name}</span>
                <span className="shrink-0 text-xs text-ink-subtle">{formatShortDateTime(row.received_at)}</span>
              </div>
              <p className="mt-0.5 font-mono text-xs text-ink-subtle">{row.invoice_external_id}</p>
              <p className="mt-1 line-clamp-2 text-sm text-ink-muted">{row.snippet || row.subject}</p>
              <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
                <StatusPill label={STATE_NAMES[row.state]} tone={STATE_TONES[row.state]} />
                <span className="text-xs text-ink-subtle">
                  {row.label
                    ? `AI: ${LABEL_NAMES[row.label]}${row.confidence !== null ? ` · ${confidenceText(row.confidence)}` : ""}`
                    : "Not classified"}
                </span>
                {row.on_hold && <span className="text-xs font-medium text-warning-800">· chasing paused</span>}
              </div>
            </button>
          </li>
        );
      })}
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
      <Notice tone="info" title="Promise recorded">
        Status: {reply.promise.status.toLowerCase()}. Pay {reply.promise.amount} {reply.promise.currency} by {reply.promise.promised_date}. Further reminders are paused
        until it is kept or broken.{" "}
        <Link href={links.invoice(reply.invoice)} className="font-medium underline">
          Manage it on invoice {reply.invoice_external_id}
        </Link>
      </Notice>
    );
  }
  if (reply.dispute) {
    return (
      <Notice tone="danger" title={`Dispute ${reply.dispute.status_display.toLowerCase()}`}>
        {reply.dispute.reason_display}.{" "}
        <Link href={links.dispute(reply.dispute.id)} className="font-medium underline">
          Open the dispute case
        </Link>
      </Notice>
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
  const field = `mt-1.5 ${inputStyles.input}`;

  if (label === "PROMISE_TO_PAY") {
    return (
      <section className="space-y-4 rounded-xl border border-line bg-slate-50/60 p-4" aria-label="Record the promise to pay">
        <div>
          <h4 className="text-sm font-semibold text-ink">Record the promise to pay</h4>
          <p className="mt-0.5 text-sm text-ink-muted">
            This creates the promise CollectFlow tracks; choosing the label alone does not. Check the date and amount
            against the reply (the AI&apos;s values are only suggestions). Chasing stays paused until the promise is kept
            or broken (2-day grace), and unsent reminders are withdrawn.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-medium text-ink">
            Promised date <span className="text-danger-700">*</span>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={field} />
          </label>
          <label className="text-sm font-medium text-ink">
            Amount <span className="font-normal text-ink-subtle">(empty = full invoice)</span>
            <input value={amount} onChange={(e) => setAmount(e.target.value)} className={field} />
          </label>
        </div>
        <label className="block text-sm font-medium text-ink">
          Note <span className="font-normal text-ink-subtle">(optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Promise note" className={field} />
        </label>
        {error && <ErrorMessage message={error} />}
        <div className="flex flex-wrap items-center justify-end gap-3">
          {!date && <p className="mr-auto text-xs text-ink-subtle">Enter the date the customer promised to pay.</p>}
          <button
            type="button"
            disabled={busy || !date}
            onClick={() =>
              void submit(() =>
                recordPromise({ invoice_id: reply.invoice, reply_id: reply.id, promised_date: date, amount: amount || undefined, note }),
              )
            }
            className={buttonStyles.primary}
          >
            {busy && <Spinner />}
            Record promise to pay
          </button>
        </div>
      </section>
    );
  }
  return (
    <section className="space-y-4 rounded-xl border border-line bg-slate-50/60 p-4" aria-label="Open a dispute">
      <div>
        <h4 className="text-sm font-semibold text-ink">Open a dispute case</h4>
        <p className="mt-0.5 text-sm text-ink-muted">
          This creates the case an approver resolves. Until then nothing is drafted, approved or sent for this invoice.
        </p>
      </div>
      <label className="block text-sm font-medium text-ink">
        Reason <span className="text-danger-700">*</span>
        <select value={reason} onChange={(e) => setReason(e.target.value)} className={`mt-1.5 ${inputStyles.select}`}>
          <option value="">Choose…</option>
          {DISPUTE_REASONS.map(([value, name]) => (
            <option key={value} value={value}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm font-medium text-ink">
        What the customer disputes <span className="text-danger-700">*</span>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} placeholder="e.g. Says 4 of the billed hours were not agreed" className={`mt-1.5 ${inputStyles.textarea}`} />
      </label>
      <label className="block text-sm font-medium text-ink sm:max-w-xs">
        Owner
        <select
          value={owner ?? ""}
          onChange={(e) => setOwner(e.target.value ? Number(e.target.value) : null)}
          className={`mt-1.5 ${inputStyles.select}`}
        >
          <option value="">Unassigned</option>
          {users.map((user) => (
            <option key={user.id} value={user.id}>
              {user.display_name}
            </option>
          ))}
        </select>
      </label>
      {error && <ErrorMessage message={error} />}
      <div className="flex justify-end">
        <button
          type="button"
          disabled={busy || !reason || !description.trim()}
          onClick={() =>
            void submit(() => openDispute({ invoice_id: reply.invoice, reply_id: reply.id, reason, description, owner_id: owner }))
          }
          className={buttonStyles.primary}
        >
          {busy && <Spinner />}
          Open dispute case
        </button>
      </div>
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
  // A promise or dispute reply is finished by recording the promise or opening the case (M11).
  const recordsCase = (label === "PROMISE_TO_PAY" || label === "DISPUTE") && !reply.promise && !reply.dispute;

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

  const extracted = EXTRACTED_FIELDS.filter(([key]) => typeof reply.extracted[key] === "string" && reply.extracted[key]);
  const block = "space-y-3 border-t border-line px-5 py-5 sm:px-6";
  const heading = "flex items-center gap-2 text-sm font-semibold text-ink";

  return (
    <div>
      <header className="space-y-2 px-5 py-5 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-ink">
              {reply.client_name}{" "}
              <Link href={links.invoice(reply.invoice)} className="font-mono text-base font-medium text-brand-700 hover:underline">
                {reply.invoice_external_id}
              </Link>
            </h2>
            <p className="mt-0.5 text-sm text-ink-muted">{reply.subject || "(no subject)"}</p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <StatusPill label={STATE_NAMES[reply.state]} tone={STATE_TONES[reply.state]} />
            <a href={reply.gmail_url} target="_blank" rel="noreferrer" className={buttonStyles.secondarySm}>
              Open in Gmail
            </a>
          </div>
        </div>
        <p className="text-xs text-ink-subtle">
          From {reply.sender_name ? `${reply.sender_name} ` : ""}&lt;{reply.sender_email}&gt;
          <span className={reply.sender_check === "MISMATCH" ? "font-semibold text-danger-700" : ""}> ({SENDER_NOTES[reply.sender_check]})</span>
          {" · "}received {formatDateTime(reply.received_at)}
          {reply.replied_to
            ? ` · in reply to ${reply.replied_to.step ? `reminder step ${reply.replied_to.step}` : "our email"} ("${reply.replied_to.subject}")`
            : ""}
        </p>
        {activeHolds.length > 0 && (
          <Notice tone="warning" role="status" title="Chasing is on hold">
            For {activeHolds.map((hold) => `${hold.invoice_external_id} (${hold.reason_display.toLowerCase()})`).join(", ")}. No reminder
            is drafted, approved or sent until this is resolved.
            {protectedHold && " Only an approver can resume chasing after a promise, dispute or payment claim."}
          </Notice>
        )}
      </header>

      <section aria-label="Reply text" className={block}>
        <h3 className={heading}>
          <Icon name="mail" className="h-4 w-4 text-ink-subtle" />
          What the customer wrote
        </h3>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-white p-4 font-sans text-sm leading-6 text-ink">
          {reply.body_purged_at ? "The reply text was removed after the retention period." : reply.body || "(empty)"}
        </pre>
        {reply.body_truncated && <p className="text-xs text-ink-subtle">The text was shortened when it was stored.</p>}
      </section>

      <section aria-label="AI suggestion" className={`${block} bg-brand-50/30`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className={heading}>
            <Icon name="sparkles" className="h-4 w-4 text-brand-600" />
            {reply.label_source === "HUMAN" ? "Decided by a person" : "What the AI suggests"}
          </h3>
          {!humanDone && (reply.state === "TRIAGE" || reply.state === "RECEIVED") && (
            <button type="button" disabled={busy} onClick={() => void retry()} className={buttonStyles.secondarySm}>
              Ask the AI again
            </button>
          )}
        </div>
        {reply.label_source !== "HUMAN" && (
          <p className="text-xs text-ink-subtle">AI suggestion only. Nothing happens until a person saves a decision below.</p>
        )}
        {reply.label ? (
          <div className="text-sm text-ink">
            <p>
              <span className="font-semibold">{LABEL_NAMES[reply.label]}</span>
              {reply.confidence !== null && <span className="text-ink-muted"> · confidence {confidenceText(reply.confidence)}</span>}
            </p>
            {reply.rationale && <p className="mt-0.5 text-ink-muted">{reply.rationale}</p>}
          </div>
        ) : (
          <p className="text-sm text-ink-muted">Not classified yet.</p>
        )}
        {extracted.length > 0 && (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {extracted.map(([key, name]) => (
              <div key={key}>
                <dt className="text-xs text-ink-subtle">{name} (suggested)</dt>
                <dd className="font-medium text-ink">{String(reply.extracted[key])}</dd>
              </div>
            ))}
          </dl>
        )}
        {reply.triage_reason && (
          <p className="text-sm text-warning-900">
            <span className="font-semibold">Why it needs review:</span> {TRIAGE_REASONS[reply.triage_reason] ?? reply.triage_reason}
          </p>
        )}
      </section>

      <section aria-label="Classify" className={block}>
        <h3 className={heading}>
          <Icon name="user" className="h-4 w-4 text-ink-subtle" />
          Your decision
        </h3>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Label">
          {REPLY_LABELS.map((value, index) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={label === value}
              onClick={() => setLabel(value)}
              className={`inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                label === value
                  ? "border-brand-600 bg-brand-600 text-white"
                  : "border-line-strong bg-white text-ink hover:bg-slate-50"
              }`}
            >
              <span className={`text-xs ${label === value ? "text-white/70" : "text-ink-subtle"}`}>{index + 1}</span>
              {LABEL_NAMES[value]}
            </button>
          ))}
        </div>
        {label && (
          <p className="text-sm text-ink-muted">
            <span className="font-medium text-ink">What happens:</span> {LABEL_EFFECTS[label]}
          </p>
        )}
        {recordsCase ? (
          <>
            <ReplyCaseActions
              reply={reply}
              label={label}
              users={users}
              onChanged={() => void getReply(reply.id).then(onChanged)}
            />
            <details className="group rounded-lg border border-line px-4 py-3 text-sm text-ink-muted">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium text-ink">
                <Icon name="chevron" className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
                Save the label only, without a {label === "DISPUTE" ? "dispute case" : "promise"}
              </summary>
              <p className="mt-2">
                Chasing stays paused, but {label === "DISPUTE" ? "no approver is asked to resolve anything" : "nothing tracks the promised date"}.
                Use this only if you will record it later.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => void act(() => classifyReply(reply.id, label, fields, note))}
                className={`mt-3 ${buttonStyles.secondarySm}`}
              >
                Save label only
              </button>
            </details>
          </>
        ) : (
          <>
            {label && LABEL_FIELDS[label].length > 0 && (
              <div className="grid gap-4 sm:grid-cols-2">
                {EXTRACTED_FIELDS.filter(([key]) => LABEL_FIELDS[label].includes(key)).map(([key, name, kind]) => (
                  <label key={key} className="text-sm font-medium text-ink">
                    {name}
                    <input
                      type={kind}
                      value={fields[key] ?? ""}
                      onChange={(event) => setFields((current) => ({ ...current, [key]: event.target.value }))}
                      className={`mt-1.5 ${inputStyles.input}`}
                    />
                  </label>
                ))}
              </div>
            )}
            <label className="block text-sm font-medium text-ink">
              Note{" "}
              {needsApprover ? (
                <span className="font-normal text-danger-700">(required: say why chasing can resume)</span>
              ) : (
                <span className="font-normal text-ink-subtle">(optional)</span>
              )}
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder={needsApprover ? "e.g. Checked with the customer by phone; they will pay on the 30th" : ""}
                className={`mt-1.5 ${inputStyles.input}`}
              />
            </label>
            {needsApprover && !isApprover && (
              <Notice tone="warning">
                Only an approver can resume chasing after a promise, dispute or payment claim. Ask an approver to review this reply.
              </Notice>
            )}
            <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
              {activeHolds.length > 0 && (
                <button
                  type="button"
                  disabled={busy || (protectedHold && (!isApprover || !note.trim()))}
                  title={protectedHold && !isApprover ? "Only an approver can resume chasing after this reply" : undefined}
                  onClick={() => void act(() => releaseHold(reply.id, note))}
                  className={buttonStyles.secondary}
                >
                  Resume chasing without a label
                </button>
              )}
              <button
                type="button"
                disabled={busy || !label || (needsApprover && (!isApprover || !note.trim()))}
                title={needsApprover && !isApprover ? "Only an approver can resume chasing after this reply" : undefined}
                onClick={() => label && void act(() => classifyReply(reply.id, label, fields, note))}
                className={buttonStyles.primary}
              >
                {busy && <Spinner />}
                {label ? `Save decision: ${LABEL_NAMES[label]}` : "Choose a label first"}
              </button>
            </div>
          </>
        )}
        {error && <ErrorMessage message={error} />}
      </section>

      {!recordsCase && (reply.promise || reply.dispute) && (
        <div className={block}>
          <ReplyCaseActions
            reply={reply}
            label={label || reply.label}
            users={users}
            onChanged={() => void getReply(reply.id).then(onChanged)}
          />
        </div>
      )}

      {reply.tasks.length > 0 && (
        <section aria-label="Tasks" className={block}>
          <h3 className={heading}>Tasks from this reply</h3>
          <ul className="divide-y divide-slate-100 rounded-lg border border-line text-sm">
            {reply.tasks.map((task) => (
              <li key={task.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <span className="text-ink">{task.title}</span>
                <span className="flex items-center gap-3">
                  <StatusPill
                    label={task.status === "DONE" ? "Done" : task.status === "CANCELLED" ? "Cancelled" : "Open"}
                    tone={task.status === "DONE" ? "good" : task.status === "CANCELLED" ? "neutral" : "warn"}
                  />
                  <Link href={links.task(task.id)} className={buttonStyles.link}>
                    Open
                  </Link>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Processing history" className={block}>
        <h3 className={heading}>History</h3>
        <ol className="space-y-2 border-l border-line pl-4 text-xs text-ink-muted">
          {reply.classifications.map((c) => (
            <li key={c.id}>
              <span className="text-ink-subtle">{formatDateTime(c.created_at)}</span> · {c.source === "AI" ? "AI" : c.actor ?? "person"}:{" "}
              {c.label ? LABEL_NAMES[c.label] : "no label"}
              {c.confidence !== null ? ` (${confidenceText(c.confidence)})` : ""}: {decisionText(c.decision)}
              {c.note ? ` · “${c.note}”` : ""}
            </li>
          ))}
          {reply.history.map((event) => (
            <li key={event.id}>
              <span className="text-ink-subtle">{formatDateTime(event.occurred_at)}</span> · {event.label}
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
  // Other screens link here with ?reply=<id>; that reply may already be handled, so show all replies.
  const replyParam = useUrlParam("reply");
  const [filter, setFilter] = useState<ReplyFilter>(replyParam ? "all" : "triage");
  const [rows, setRows] = useState<ReplyRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(replyParam);
  // Phones show the list or one reply; a deep link opens the reply directly.
  const [mobileDetail, setMobileDetail] = useState(Boolean(replyParam));
  const [query, setQuery] = useState("");
  const [seenReplyParam, setSeenReplyParam] = useState(replyParam);
  if (replyParam !== seenReplyParam) {
    setSeenReplyParam(replyParam);
    if (replyParam) {
      setSelected(replyParam);
      setFilter("all");
      setMobileDetail(true);
    }
  }
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
        // A reply that was just handled leaves this filter; keep it open so its result stays visible.
        setSelected((current) => current ?? data[0]?.id ?? null);
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

  const q = query.trim().toLowerCase();
  const visible = (rows ?? []).filter(
    (row) =>
      !q ||
      row.client_name.toLowerCase().includes(q) ||
      row.invoice_external_id.toLowerCase().includes(q) ||
      (row.snippet || row.subject || "").toLowerCase().includes(q),
  );
  const open = (id: string) => {
    setSelected(id);
    setMobileDetail(true);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Customer Replies"
        description="Review what customers wrote back and decide what happens next. Each reply pauses chasing of its invoice until it is handled."
      />
      {error && <ErrorMessage message={error} />}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,38fr)_minmax(0,62fr)] lg:items-start">
        <Card className={`overflow-hidden lg:sticky lg:top-6 ${mobileDetail ? "max-lg:hidden" : ""}`}>
          <div className="space-y-3 border-b border-line p-4">
            <label className="relative block">
              <span className="sr-only">Search replies</span>
              <Icon name="search" className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-ink-subtle" />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search customer or invoice"
                className={`${inputStyles.input} pl-9`}
              />
            </label>
            <SegmentedFilter
              fill
              label="Filter replies"
              options={FILTERS}
              value={filter}
              onChange={(key) => {
                setFilter(key);
                setRows(null);
                setSelected(null);
              }}
            />
          </div>
          <div className="lg:max-h-[calc(100vh-14rem)] lg:overflow-y-auto">
            {rows === null ? (
              <div className="space-y-3 p-4" aria-busy="true" aria-label="Loading replies">
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
              </div>
            ) : (
              <ReplyList rows={visible} selected={selected} onSelect={open} filtered={Boolean(q) && rows.length > 0} />
            )}
          </div>
        </Card>
        <div className={mobileDetail ? "" : "max-lg:hidden"}>
          <button
            type="button"
            onClick={() => setMobileDetail(false)}
            className={`mb-3 lg:hidden ${buttonStyles.ghost} -ml-3`}
          >
            <Icon name="chevron" className="h-4 w-4 rotate-180" />
            Back to replies
          </button>
          {shown && rows && filter !== "all" && !rows.some((row) => row.id === shown.id) && (
            <div className="mb-4">
              <Notice tone="success" role="status" title="Saved">
                This reply is no longer in &quot;{FILTERS.find((f) => f.key === filter)?.label}&quot;. Choose the next one on the left.
              </Notice>
            </div>
          )}
          <Card className="overflow-hidden">
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
            ) : selected && rows && rows.length > 0 ? (
              <div className="space-y-3 p-6" aria-busy="true">
                <Skeleton className="h-6 w-1/2" />
                <Skeleton className="h-24" />
                <Skeleton className="h-32" />
              </div>
            ) : (
              <EmptyState title={rows && rows.length === 0 ? "No replies need review" : "Select a reply"} icon="mail">
                {rows && rows.length === 0
                  ? "New customer replies will appear here when they need your attention."
                  : "Choose a reply on the left to see what the customer wrote and decide what happens next."}
              </EmptyState>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
