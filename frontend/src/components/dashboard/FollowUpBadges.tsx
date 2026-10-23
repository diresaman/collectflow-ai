import type { FollowUpStatus, Tone } from "@/lib/api/followups";
import { REMINDER_STATUS } from "@/lib/status";

import { StatusPill, type PillTone } from "./ui";

const STATUS_TONES: Record<FollowUpStatus, PillTone> = {
  DRAFT: "warn",
  APPROVED: "info",
  REJECTED: "neutral",
  SENDING: "info",
  SENT: "good",
  FAILED: "bad",
  DELIVERY_UNKNOWN: "warn",
};

const TONE_STYLE: Record<Tone, string> = {
  FRIENDLY: "border-success-200 text-success-800",
  PROFESSIONAL: "border-info-200 text-info-800",
  FIRM: "border-warning-300 text-warning-900",
};

export function FollowUpStatusBadge({ status }: { status: FollowUpStatus }) {
  return <StatusPill label={REMINDER_STATUS[status].label} tone={STATUS_TONES[status]} title={REMINDER_STATUS[status].hint} />;
}

export function ToneBadge({ tone }: { tone: Tone }) {
  return (
    <span className={`inline-flex whitespace-nowrap rounded-md border bg-white px-2 py-0.5 text-xs font-medium ${TONE_STYLE[tone]}`}>
      {tone.charAt(0) + tone.slice(1).toLowerCase()} tone
    </span>
  );
}
