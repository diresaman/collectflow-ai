import type { FollowUpStatus, Tone } from "@/lib/api/followups";
import { REMINDER_STATUS } from "@/lib/status";

const STATUS_META: Record<FollowUpStatus, { label: string; style: string; dot: string }> = {
  DRAFT: { label: REMINDER_STATUS.DRAFT.label, style: "bg-indigo-50 text-indigo-700 ring-indigo-600/20", dot: "bg-indigo-500" },
  APPROVED: { label: REMINDER_STATUS.APPROVED.label, style: "bg-emerald-50 text-emerald-700 ring-emerald-600/20", dot: "bg-emerald-500" },
  REJECTED: { label: REMINDER_STATUS.REJECTED.label, style: "bg-slate-100 text-slate-600 ring-slate-500/20", dot: "bg-slate-400" },
  SENDING: { label: REMINDER_STATUS.SENDING.label, style: "bg-sky-50 text-sky-700 ring-sky-600/20", dot: "bg-sky-500 animate-pulse" },
  SENT: { label: REMINDER_STATUS.SENT.label, style: "bg-teal-50 text-teal-700 ring-teal-600/20", dot: "bg-teal-500" },
  FAILED: { label: REMINDER_STATUS.FAILED.label, style: "bg-rose-50 text-rose-700 ring-rose-600/20", dot: "bg-rose-500" },
  DELIVERY_UNKNOWN: {
    label: REMINDER_STATUS.DELIVERY_UNKNOWN.label,
    style: "bg-amber-50 text-amber-800 ring-amber-600/30",
    dot: "bg-amber-500",
  },
};

const TONE_STYLE: Record<Tone, string> = {
  FRIENDLY: "bg-sky-50 text-sky-700",
  PROFESSIONAL: "bg-violet-50 text-violet-700",
  FIRM: "bg-amber-50 text-amber-800",
};

export function FollowUpStatusBadge({ status }: { status: FollowUpStatus }) {
  const meta = STATUS_META[status];
  return (
    <span
      title={REMINDER_STATUS[status].hint}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${meta.style}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

export function ToneBadge({ tone }: { tone: Tone }) {
  return (
    <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ${TONE_STYLE[tone]}`}>
      {tone.charAt(0) + tone.slice(1).toLowerCase()} tone
    </span>
  );
}
