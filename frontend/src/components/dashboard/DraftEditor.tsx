import { useId, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  approveFollowUp,
  BODY_MAX_LENGTH,
  BODY_MIN_LENGTH,
  rejectFollowUp,
  SUBJECT_MAX_LENGTH,
  updateFollowUp,
  type FollowUp,
} from "@/lib/api/followups";

import { ErrorMessage, Icon, Spinner } from "./ui";

type Action = "save" | "approve" | "reject";

function validate(subject: string, body: string): string | null {
  if (!subject.trim()) return "Subject is required.";
  if (subject.includes("\n")) return "Subject must be a single line.";
  if (subject.trim().length > SUBJECT_MAX_LENGTH) return `Subject must be at most ${SUBJECT_MAX_LENGTH} characters.`;
  if (body.trim().length < BODY_MIN_LENGTH) return `Body must be at least ${BODY_MIN_LENGTH} characters.`;
  if (body.trim().length > BODY_MAX_LENGTH) return `Body must be at most ${BODY_MAX_LENGTH} characters.`;
  return null;
}

/** Editable view of a DRAFT follow-up. Re-mount (via `key`) to reset local edits. */
export function DraftEditor({
  followUp,
  regenerating,
  onChanged,
  onRegenerate,
}: {
  followUp: FollowUp;
  regenerating: boolean;
  onChanged: (followUp: FollowUp) => void;
  onRegenerate: () => void;
}) {
  // Unique per editor: the automation workspace shows several drafts at once.
  const fieldId = useId();
  const [subject, setSubject] = useState(followUp.subject);
  const [body, setBody] = useState(followUp.body);
  const [busy, setBusy] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);

  const dirty = subject !== followUp.subject || body !== followUp.body;
  const validationError = dirty ? validate(subject, body) : null;
  const disabled = busy !== null || regenerating;

  const run = async (action: Action) => {
    setBusy(action);
    setError(null);
    try {
      const updated =
        action === "save"
          ? await updateFollowUp(followUp.id, { subject, body })
          : action === "approve"
            ? await approveFollowUp(followUp.id)
            : await rejectFollowUp(followUp.id);
      onChanged(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unexpected error.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor={`${fieldId}-subject`} className="text-sm font-medium text-slate-700">
          Subject
        </label>
        <input
          id={`${fieldId}-subject`}
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          maxLength={SUBJECT_MAX_LENGTH}
          disabled={disabled}
          className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:bg-slate-50"
        />
      </div>

      <div>
        <div className="flex items-baseline justify-between">
          <label htmlFor={`${fieldId}-body`} className="text-sm font-medium text-slate-700">
            Message
          </label>
          <span className="text-xs tabular-nums text-slate-400">
            {body.trim().length} / {BODY_MAX_LENGTH}
          </span>
        </div>
        <textarea
          id={`${fieldId}-body`}
          value={body}
          onChange={(event) => setBody(event.target.value)}
          rows={12}
          maxLength={BODY_MAX_LENGTH}
          disabled={disabled}
          className="mt-1.5 block w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm leading-6 text-slate-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:bg-slate-50"
        />
      </div>

      {validationError && <p className="text-sm text-rose-600">{validationError}</p>}
      {error && <ErrorMessage message={error} />}

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
        {dirty ? (
          <>
            <button
              type="button"
              onClick={() => void run("save")}
              disabled={disabled || validationError !== null}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy === "save" && <Spinner />}
              Save changes
            </button>
            <button
              type="button"
              onClick={() => {
                setSubject(followUp.subject);
                setBody(followUp.body);
              }}
              disabled={disabled}
              className="rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-60"
            >
              Discard
            </button>
            <span className="text-xs text-slate-500">Save your edits before approving.</span>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => void run("approve")}
              disabled={disabled}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy === "approve" && <Spinner />}
              Approve
            </button>
            <button
              type="button"
              onClick={() => void run("reject")}
              disabled={disabled}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy === "reject" && <Spinner />}
              Reject
            </button>
            <button
              type="button"
              onClick={onRegenerate}
              disabled={disabled}
              className="ml-auto inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {regenerating ? <Spinner /> : <Icon name="sparkles" className="h-4 w-4" />}
              Regenerate
            </button>
          </>
        )}
      </div>
      <p className="text-xs text-slate-500">
        Approving marks this reminder as ready. It does <span className="font-medium">not</span> send an email.
      </p>
    </div>
  );
}
