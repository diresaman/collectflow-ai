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

import { buttonStyles, ErrorMessage, Icon, inputStyles, Notice, Spinner } from "./ui";

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
  // Four-eyes: the backend refuses these approvals; say why instead of a silently disabled button.
  const approvalBlocked = followUp.approval.blocked_for_you;
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
          readOnly={followUp.threaded}
          aria-describedby={followUp.threaded ? `${fieldId}-subject-note` : undefined}
          className={`mt-1.5 ${inputStyles.input} read-only:bg-slate-50 read-only:text-ink-muted`}
        />
        {followUp.threaded && (
          <p id={`${fieldId}-subject-note`} className="mt-1 text-xs text-slate-500">
            This reminder continues the earlier Gmail conversation, so its subject stays the same. Edit the message
            instead.
          </p>
        )}
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
          className={`mt-1.5 resize-y leading-6 ${inputStyles.textarea}`}
        />
      </div>

      {followUp.approval.required_role === "APPROVER" && (
        <p className="rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-900">
          <span className="font-semibold">Needs an approver</span> other than the last editor:{" "}
          {followUp.approval.reasons.join("; ")}.
        </p>
      )}
      {validationError && <p className="text-sm text-danger-600">{validationError}</p>}
      {error && <ErrorMessage message={error} />}

      {approvalBlocked && !dirty && (
        <Notice tone="info">
          <p id={`${fieldId}-approval-blocked`}>
            <span className="font-semibold">You can&apos;t approve this:</span> {approvalBlocked.detail} An approver can find it
            in Approvals.
          </p>
        </Notice>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
        {dirty ? (
          <>
            <span className="mr-auto text-xs text-ink-subtle">Save your edits before approving.</span>
            <button
              type="button"
              onClick={() => {
                setSubject(followUp.subject);
                setBody(followUp.body);
              }}
              disabled={disabled}
              className={buttonStyles.ghost}
            >
              Discard
            </button>
            <button type="button" onClick={() => void run("save")} disabled={disabled || validationError !== null} className={buttonStyles.primary}>
              {busy === "save" && <Spinner />}
              Save changes
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={onRegenerate} disabled={disabled} className={`${buttonStyles.ghost} mr-auto -ml-3 text-brand-700`}>
              {regenerating ? <Spinner /> : <Icon name="sparkles" className="h-4 w-4" />}
              Rewrite with AI
            </button>
            <button type="button" onClick={() => void run("reject")} disabled={disabled} className={buttonStyles.secondary}>
              {busy === "reject" && <Spinner />}
              Reject draft
            </button>
            <button
              type="button"
              onClick={() => void run("approve")}
              disabled={disabled || approvalBlocked !== null}
              aria-describedby={approvalBlocked ? `${fieldId}-approval-blocked` : undefined}
              className={approvalBlocked ? buttonStyles.secondary : buttonStyles.primary}
            >
              {busy === "approve" && <Spinner />}
              {followUp.kind === "STATEMENT" ? "Approve statement" : "Approve reminder"}
            </button>
          </>
        )}
      </div>
      <p className="text-xs text-ink-subtle">
        Approving marks it as ready. It does <span className="font-medium">not</span> send anything: sending is the next,
        separate step.
      </p>
    </div>
  );
}
