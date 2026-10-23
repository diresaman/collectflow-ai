/**
 * One human-readable label for every backend state, used on every screen (UX milestone). These only
 * name existing states; they never add or change one. Approval never means sent.
 */
import type { PillTone } from "@/components/dashboard/ui";

type Label = { label: string; tone: PillTone; hint?: string };

export const REMINDER_STATUS = {
  DRAFT: { label: "Awaiting approval", tone: "info", hint: "Drafted. Nothing is sent until it is approved and then sent." },
  APPROVED: { label: "Approved, not sent", tone: "good", hint: "Ready. Someone still has to click Send." },
  SENDING: { label: "Sending…", tone: "info", hint: "Handed to Gmail; the result is not known yet." },
  SENT: { label: "Sent", tone: "good", hint: "Gmail accepted the email." },
  FAILED: { label: "Not sent", tone: "bad", hint: "Gmail refused it or it could not be sent. Draft it again." },
  DELIVERY_UNKNOWN: {
    label: "Delivery unknown",
    tone: "warn",
    hint: "It may or may not have been sent. Check Gmail before doing anything; it is never resent automatically.",
  },
  REJECTED: { label: "Rejected", tone: "neutral", hint: "Not sent. A person rejected it or it was withdrawn." },
} satisfies Record<string, Label>;

export const INVOICE_STATUS = {
  UNPAID: { label: "Unpaid", tone: "neutral" },
  PAID: { label: "Paid", tone: "good", hint: "Paid according to the Google Sheet." },
} satisfies Record<string, Label>;

export const TASK_STATUS = {
  OPEN: { label: "To do", tone: "info" },
  DONE: { label: "Completed", tone: "good" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
} satisfies Record<string, Label>;

export const DISPUTE_STATUS = {
  OPEN: { label: "Disputed", tone: "bad", hint: "Nothing is drafted, approved or sent until an approver resolves it." },
  RESOLVED_VALID: { label: "Resolved: customer was right", tone: "neutral" },
  RESOLVED_INVALID: { label: "Resolved: invoice stands", tone: "neutral" },
} satisfies Record<string, Label>;

export const PROMISE_STATUS = {
  OPEN: { label: "Promise due", tone: "info", hint: "Chasing is on hold until the promised date plus 2 days." },
  KEPT: { label: "Promise kept", tone: "good" },
  BROKEN: { label: "Promise broken", tone: "bad" },
  CANCELLED: { label: "Promise cancelled", tone: "neutral" },
} satisfies Record<string, Label>;

export const REPLY_STATE = {
  RECEIVED: { label: "Being read", tone: "info" },
  TRIAGE: { label: "Needs review", tone: "warn" },
  ACTIONED: { label: "Handled automatically", tone: "good" },
  RESOLVED: { label: "Reviewed", tone: "neutral" },
} satisfies Record<string, Label>;

export const REPLY_LABEL: Record<string, string> = {
  PROMISE_TO_PAY: "Promise to pay",
  DISPUTE: "Dispute",
  CLAIMS_PAID: "Says it is paid",
  REQUESTS_INFO: "Asks for information",
  OUT_OF_OFFICE: "Out of office",
  WRONG_CONTACT: "Wrong contact",
  OTHER: "Other",
};

export function labelFor(map: Record<string, Label>, state: string): Label {
  return map[state] ?? { label: state.toLowerCase().replace(/_/g, " "), tone: "neutral" };
}
