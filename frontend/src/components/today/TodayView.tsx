"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import {
  EmptyState,
  ErrorMessage,
  Icon,
  PageHeader,
  Section,
  Skeleton,
  Spinner,
  StatCard,
  buttonStyles,
  type IconName,
} from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { getToday, type TodayItem, type TodayReport, type TodaySection } from "@/lib/api/today";
import { formatShortDateTime, formatTime } from "@/lib/format";
import { SECTION_TITLES } from "@/lib/status";
import { appLink } from "@/lib/url";

const REFRESH_MS = 60_000;

/**
 * The backend's eleven sections (M12, unchanged rules and order) shown as four groups that answer
 * "what needs attention, why, and what can I do next". Empty groups are hidden; nothing is dropped.
 */
const GROUPS: { key: string; title: string; short: string; intro: string; sections: string[]; icon: IconName; tone: "danger" | "neutral" | "warning" | "success" }[] = [
  {
    key: "attention",
    title: "Needs your attention",
    short: "Needs attention",
    intro: "Problems and customer messages that hold up collections until a person looks at them.",
    sections: ["integrations", "replies", "deliveries", "disputes"],
    icon: "warning",
    tone: "danger",
  },
  {
    key: "approval",
    title: "Waiting for approval or sending",
    short: "Approval or sending",
    intro: "Reminders written and waiting for a person. Approving never sends: sending is a separate click.",
    sections: ["drafts", "approved"],
    icon: "mail",
    tone: "neutral",
  },
  {
    key: "blocked",
    title: "Blocked or at risk",
    short: "Blocked or at risk",
    intro: "Invoices that are not being chased right now, and why.",
    sections: ["broken_promises", "sequence_blocks", "held"],
    icon: "pause",
    tone: "warning",
  },
  {
    key: "upcoming",
    title: "Due now and coming up",
    short: "Tasks and promises due",
    intro: "Tasks that are due and payments customers promised.",
    sections: ["tasks", "promises_due"],
    icon: "clock",
    tone: "success",
  },
];

/** Per section: the button wording, and what doing it leads to. */
const SECTION_COPY: Record<string, { action: string; next: string }> = {
  integrations: { action: "Check service", next: "Other work may depend on this; fix it first." },
  replies: { action: "Review reply", next: "Chasing this invoice resumes only after a person reviews the reply." },
  deliveries: { action: "Check delivery", next: "Never resent automatically: check Gmail, then record what happened." },
  disputes: { action: "Review dispute", next: "Nothing is drafted, approved or sent until an approver resolves it." },
  drafts: { action: "Review draft", next: "After approval it still has to be sent." },
  approved: { action: "Review and send", next: "Approved but not sent yet." },
  broken_promises: { action: "Review invoice", next: "The next reminder will be one tone firmer and mention the missed date." },
  sequence_blocks: { action: "Fix schedule", next: "Reminders for this invoice wait until a person acts." },
  held: { action: "See why", next: "Shows who can end the hold." },
  tasks: { action: "Open task", next: "Mark it complete when done." },
  promises_due: { action: "View invoice", next: "Settled automatically from the sheet within 2 days of the date." },
};

const sectionTitle = (section: TodaySection) => SECTION_TITLES[section.key] ?? section.title;

const RANK: Record<string, number> = { OPERATOR: 1, APPROVER: 2, ADMIN: 3 };

/** The role an item needs, read from its "who can act" text ("Approver (resolve), operator (notes)" -> operator). */
function neededRole(who: string): string {
  const text = who.toLowerCase();
  if (text.includes("operator")) return "OPERATOR";
  if (text.startsWith("admin")) return "ADMIN";
  if (text.startsWith("approver")) return "APPROVER";
  return "OPERATOR";
}

/** The kind of work each section holds, as an icon and a short label shown before the record. */
const SECTION_KIND: Record<string, { icon: IconName; label: string }> = {
  integrations: { icon: "warning", label: "Connected service" },
  replies: { icon: "mail", label: "Customer reply" },
  deliveries: { icon: "warning", label: "Delivery unknown" },
  disputes: { icon: "warning", label: "Dispute" },
  drafts: { icon: "document", label: "Draft" },
  approved: { icon: "mail", label: "Approved, not sent" },
  broken_promises: { icon: "clock", label: "Broken promise" },
  sequence_blocks: { icon: "pause", label: "Reminder schedule" },
  held: { icon: "pause", label: "On hold" },
  tasks: { icon: "check", label: "Task" },
  promises_due: { icon: "clock", label: "Promise due" },
};

function ItemRow({ item, section }: { item: TodayItem; section: string }) {
  const user = useCurrentUser();
  const copy = SECTION_COPY[section];
  const kind = SECTION_KIND[section] ?? { icon: "info" as IconName, label: "Item" };
  const needed = neededRole(item.who_can_act);
  const forOthers = Boolean(item.blocked) || (RANK[user.role] ?? 0) < (RANK[needed] ?? 1);
  const forWhom = needed === "ADMIN" ? "an admin" : needed === "APPROVER" ? "an approver" : item.who_can_act;
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 px-5 py-3.5 sm:px-6 lg:grid-cols-[auto_minmax(0,1fr)_11rem_auto] lg:items-center">
      <span className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-ink-muted lg:mt-0" aria-hidden="true">
        <Icon name={kind.icon} className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <p className="text-xs font-medium text-ink-subtle">
          {kind.label}
          {item.invoice_external_id && (
            <>
              {" · "}
              <span className="font-mono text-ink-muted">{item.invoice_external_id}</span>
              {item.client_name ? ` · ${item.client_name}` : ""}
            </>
          )}
        </p>
        <p className="mt-0.5 text-sm font-medium text-ink">{item.title}</p>
        {item.detail && <p className="mt-0.5 text-sm text-ink-muted">{item.detail}</p>}
        {item.action && <p className="mt-0.5 text-xs text-ink-muted">What to do: {item.action}</p>}
      </div>
      <div className="col-start-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-subtle lg:col-start-auto lg:flex-col lg:items-start lg:gap-1">
        {item.overdue && <span className="font-semibold text-danger-700">Overdue</span>}
        {item.since && <span>Since {formatShortDateTime(item.since)}</span>}
      </div>
      <div className="col-start-2 flex flex-wrap items-center gap-x-3 gap-y-2 lg:col-start-auto lg:justify-end">
        <span className={`text-xs ${forOthers ? "font-medium text-brand-800" : "text-ink-subtle"}`}>
          {forOthers ? `For ${forWhom}` : `Who can act: ${item.who_can_act}`}
        </span>
        <Link href={appLink(item.link)} className={forOthers ? buttonStyles.secondarySm : buttonStyles.primarySm}>
          {forOthers ? "View" : copy?.action ?? "View"}
        </Link>
      </div>
      {forOthers && (
        <p className="col-start-2 text-xs text-ink-muted lg:col-span-3">
          {item.blocked || `Your role cannot do this; let ${forWhom} know.`}
        </p>
      )}
    </li>
  );
}

function SectionHeading({ section, extra }: { section: TodaySection; extra?: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-y border-slate-100 bg-slate-50/70 px-5 py-2 sm:px-6">
      <h3 className="text-sm font-semibold text-ink">
        {sectionTitle(section)} <span className="font-normal text-ink-subtle tabular-nums">{section.items.length}</span>
      </h3>
      <span className="text-xs text-ink-subtle">{extra ?? SECTION_COPY[section.key]?.next}</span>
    </div>
  );
}

function SectionBlock({ section }: { section: TodaySection }) {
  const user = useCurrentUser();
  // Integration problems only an admin can fix: one summary line for everyone else.
  if (section.key === "integrations" && user.role !== "ADMIN") {
    return (
      <div>
        <SectionHeading section={section} extra="Scheduled reminders and reply checks may be delayed until an admin fixes this." />
        <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 px-5 py-3.5 sm:px-6 lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:items-center">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-ink-muted" aria-hidden="true">
            <Icon name="warning" className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium text-ink">{section.items.length} connected service problem(s)</p>
            <p className="mt-0.5 text-sm text-ink-muted">{section.items.map((i) => i.title).join(" · ")}</p>
          </div>
          <div className="col-start-2 flex items-center gap-3 lg:col-start-auto">
            <span className="text-xs font-medium text-brand-800">For an admin</span>
            <Link href="/settings/integrations" className={buttonStyles.secondarySm}>
              View details
            </Link>
          </div>
        </div>
      </div>
    );
  }
  // Holds repeat items already listed above (replies, disputes, promises): folded by default.
  if (section.key === "held") {
    return (
      <details className="group border-t border-slate-100">
        <summary className="flex cursor-pointer list-none flex-wrap items-baseline justify-between gap-x-4 gap-y-1 bg-slate-50/70 px-5 py-2 hover:bg-slate-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 sm:px-6">
          <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
            <Icon name="chevron" className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
            {sectionTitle(section)} <span className="font-normal text-ink-subtle tabular-nums">{section.items.length}</span>
          </span>
          <span className="text-xs text-ink-subtle">Most are listed above too · why each one is paused and who can end it</span>
        </summary>
        <ul className="divide-y divide-slate-100 border-t border-slate-100">
          {section.items.map((item) => (
            <ItemRow key={item.key} item={item} section={section.key} />
          ))}
        </ul>
      </details>
    );
  }
  return (
    <div>
      <SectionHeading section={section} />
      <ul className="divide-y divide-slate-100">
        {section.items.map((item) => (
          <ItemRow key={item.key} item={item} section={section.key} />
        ))}
      </ul>
    </div>
  );
}

export function TodayView() {
  const [report, setReport] = useState<TodayReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const receive = useCallback((promise: Promise<TodayReport>, signal?: AbortSignal) => {
    promise
      .then((data) => {
        setReport(data);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!signal?.aborted) setError(err instanceof ApiError ? err.message : "Unexpected error.");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    receive(getToday(controller.signal), controller.signal);
    const timer = window.setInterval(() => receive(getToday(controller.signal), controller.signal), REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [receive]);

  const refresh = () => {
    setLoading(true);
    receive(getToday());
  };

  const sections = new Map((report?.sections ?? []).map((s) => [s.key, s]));
  const groups = GROUPS.map((group) => ({
    ...group,
    sections: group.sections.map((key) => sections.get(key)).filter((s): s is TodaySection => Boolean(s && s.items.length)),
  })).filter((group) => group.sections.length > 0);
  const count = (group: (typeof groups)[number]) => group.sections.reduce((n, s) => n + s.items.length, 0);
  // Any section the backend adds later still shows (never silently dropped).
  const known = new Set(GROUPS.flatMap((g) => g.sections));
  const other = (report?.sections ?? []).filter((s) => !known.has(s.key) && s.items.length > 0);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Today's Work"
        description="Everything that needs your attention, in one place. Most urgent first; it refreshes every minute."
        actions={
          <>
            {report && (
              <span className="text-xs text-ink-subtle max-sm:hidden" aria-live="polite">
                Updated {formatTime(report.generated_at)}
              </span>
            )}
            <button type="button" onClick={refresh} disabled={loading} className={buttonStyles.secondary}>
              {loading ? <Spinner /> : <Icon name="sync" className="h-4 w-4" />}
              Refresh
            </button>
          </>
        }
      />
      {error && (
        <ErrorMessage message={`Could not load today's work: ${error}${report ? " Showing the last loaded list." : ""}`} />
      )}
      {report === null && !error && (
        <div className="space-y-4" aria-busy="true" aria-label="Loading today's work">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-64 rounded-xl" />
        </div>
      )}
      {report && (
        <>
          <nav aria-label="Summary" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {GROUPS.map((group) => {
              const found = groups.find((g) => g.key === group.key);
              const n = found ? count(found) : 0;
              const card = (
                <StatCard
                  label={group.short}
                  value={n}
                  icon={group.icon}
                  tone={n > 0 ? group.tone : "neutral"}
                  detail={n > 0 ? "Jump to the list" : "Nothing here"}
                />
              );
              return found ? (
                <a
                  key={group.key}
                  href={`#${group.key}`}
                  className="rounded-xl transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  {card}
                </a>
              ) : (
                <div key={group.key} className="opacity-70">
                  {card}
                </div>
              );
            })}
          </nav>
          {groups.length === 0 && (
            <Section title="All clear">
              <EmptyState title="Nothing needs a person right now">
                New customer replies, drafts to approve and tasks will appear here as soon as they need you.
              </EmptyState>
            </Section>
          )}
          {groups.map((group) => (
            <div key={group.key} id={group.key} className="scroll-mt-20">
              <Section
                title={group.title}
                count={count(group)}
                description={group.intro}
                flush
                actions={
                  group.key === "approval" ? (
                    <Link href="/approvals" className={buttonStyles.secondarySm}>
                      Open Approvals
                    </Link>
                  ) : undefined
                }
              >
                {group.sections.map((section) => (
                  <SectionBlock key={section.key} section={section} />
                ))}
              </Section>
            </div>
          ))}
          {other.map((section) => (
            <Section key={section.key} title={sectionTitle(section)} count={section.items.length} flush>
              <SectionBlock section={section} />
            </Section>
          ))}
        </>
      )}
    </div>
  );
}
