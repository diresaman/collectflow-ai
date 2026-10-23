"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/SessionGate";
import { Card, ErrorMessage, PageHeader, Skeleton, Spinner, buttonStyles } from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { getToday, type TodayItem, type TodayReport, type TodaySection } from "@/lib/api/today";
import { formatDateTime } from "@/lib/format";

const REFRESH_MS = 60_000;

/**
 * The backend's eleven sections (M12, unchanged rules and order) shown as four groups that answer
 * "what needs attention, why, and what can I do next". Empty groups are hidden; nothing is dropped.
 */
const GROUPS: { key: string; title: string; intro: string; sections: string[]; tone: string }[] = [
  {
    key: "attention",
    title: "Needs your attention",
    intro: "Problems and customer messages that hold up collections until a person looks at them.",
    sections: ["integrations", "replies", "deliveries", "disputes"],
    tone: "border-l-rose-500",
  },
  {
    key: "approval",
    title: "Waiting for approval or sending",
    intro: "Reminders written and waiting for a person. Approving never sends: sending is a separate click.",
    sections: ["drafts", "approved"],
    tone: "border-l-indigo-500",
  },
  {
    key: "blocked",
    title: "Blocked or at risk",
    intro: "Invoices that are not being chased right now, and why.",
    sections: ["broken_promises", "sequence_blocks", "held"],
    tone: "border-l-amber-500",
  },
  {
    key: "upcoming",
    title: "Due now and coming up",
    intro: "Tasks that are due and payments customers promised.",
    sections: ["tasks", "promises_due"],
    tone: "border-l-slate-400",
  },
];

/** Per section: the button wording, and what doing it leads to. */
const SECTION_COPY: Record<string, { action: string; next: string }> = {
  integrations: { action: "Check integration", next: "Other work may depend on this; fix it first." },
  replies: { action: "Review reply", next: "Chasing this invoice resumes only after a person reviews the reply." },
  deliveries: { action: "Check delivery", next: "Never resent automatically: check Gmail, then record what happened." },
  disputes: { action: "Review dispute", next: "Nothing is drafted, approved or sent until an approver resolves it." },
  drafts: { action: "Review draft", next: "After approval it still has to be sent." },
  approved: { action: "Review and send", next: "Approved but not sent yet." },
  broken_promises: { action: "Review invoice", next: "The next reminder will be one tone firmer and mention the missed date." },
  sequence_blocks: { action: "Fix sequence", next: "The sequence waits until a person acts." },
  held: { action: "See why", next: "Shows who can end the hold." },
  tasks: { action: "Open task", next: "Mark it complete when done." },
  promises_due: { action: "View invoice", next: "Settled automatically from the sheet within 2 days of the date." },
};

const RANK: Record<string, number> = { OPERATOR: 1, APPROVER: 2, ADMIN: 3 };

/** The role an item needs, read from its "who can act" text ("Approver (resolve), operator (notes)" -> operator). */
function neededRole(who: string): string {
  const text = who.toLowerCase();
  if (text.includes("operator")) return "OPERATOR";
  if (text.startsWith("admin")) return "ADMIN";
  if (text.startsWith("approver")) return "APPROVER";
  return "OPERATOR";
}

function ItemRow({ item, section }: { item: TodayItem; section: string }) {
  const user = useCurrentUser();
  const copy = SECTION_COPY[section];
  const needed = neededRole(item.who_can_act);
  const forOthers = Boolean(item.blocked) || (RANK[user.role] ?? 0) < (RANK[needed] ?? 1);
  return (
    <li className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between sm:px-5">
      <div className="min-w-0 space-y-0.5">
        <p className="text-sm font-medium text-slate-900">
          {item.title}
          {item.overdue && <span className="ml-2 text-xs font-semibold text-rose-700">Overdue</span>}
          {forOthers && (
            <span className="ml-2 rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-800">
              For {needed === "ADMIN" ? "an admin" : needed === "APPROVER" ? "an approver" : item.who_can_act}
            </span>
          )}
        </p>
        <p className="text-xs text-slate-500">
          {item.invoice_external_id ? `${item.invoice_external_id} · ${item.client_name}` : ""}
          {item.invoice_external_id && item.since ? " · " : ""}
          {item.since ? `since ${formatDateTime(item.since)}` : ""}
        </p>
        {item.detail && <p className="text-sm text-slate-700">{item.detail}</p>}
        {item.action && <p className="text-xs text-slate-600">What to do: {item.action}</p>}
        {item.blocked ? (
          <p className="text-xs text-violet-900">{item.blocked}</p>
        ) : forOthers ? (
          <p className="text-xs text-violet-900">Your role cannot do this; let {needed === "ADMIN" ? "an admin" : "an approver"} know.</p>
        ) : (
          <p className="text-xs text-slate-500">Who can act: {item.who_can_act}</p>
        )}
      </div>
      <Link href={item.link} className={`${forOthers ? buttonStyles.secondary : buttonStyles.primary} shrink-0 !px-3 !py-1.5 text-xs`}>
        {forOthers ? "View" : copy?.action ?? "View"}
      </Link>
    </li>
  );
}

function SectionBlock({ section }: { section: TodaySection }) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 border-b border-slate-100 bg-slate-50/70 px-4 py-2 sm:px-5">
        <h3 className="text-sm font-semibold text-slate-800">
          {section.title} <span className="font-normal text-slate-500">({section.items.length})</span>
        </h3>
        <span className="hidden text-xs text-slate-500 sm:inline">{SECTION_COPY[section.key]?.next}</span>
      </div>
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
        title="Today"
        description="Everything that needs a person, most urgent first. It comes straight from your records and refreshes every minute."
        actions={
          <button type="button" onClick={refresh} disabled={loading} className={buttonStyles.secondary}>
            {loading && <Spinner />}
            Refresh
          </button>
        }
      />
      {error && (
        <ErrorMessage message={`Could not load today's work: ${error}${report ? " Showing the last loaded list." : ""}`} />
      )}
      {report === null && !error && (
        <div className="space-y-3">
          <Skeleton className="h-16" />
          <Skeleton className="h-40" />
        </div>
      )}
      {report && (
        <>
          {groups.length > 0 ? (
            <nav aria-label="Summary" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {GROUPS.map((group) => {
                const found = groups.find((g) => g.key === group.key);
                const n = found ? count(found) : 0;
                return (
                  <a
                    key={group.key}
                    href={found ? `#${group.key}` : undefined}
                    className={`rounded-xl border border-l-4 border-slate-200 bg-white p-3 shadow-sm ${group.tone} ${found ? "hover:bg-slate-50" : "opacity-60"}`}
                  >
                    <span className="block text-2xl font-semibold text-slate-900">{n}</span>
                    <span className="block text-xs font-medium text-slate-600">{group.title}</span>
                  </a>
                );
              })}
            </nav>
          ) : (
            <Card className="p-6 text-center">
              <p className="text-base font-semibold text-slate-900">All clear</p>
              <p className="mt-1 text-sm text-slate-600">
                Nothing needs a person right now. New replies, drafts and tasks will appear here.
              </p>
            </Card>
          )}
          {groups.map((group) => (
            <section key={group.key} id={group.key} aria-labelledby={`${group.key}-title`} className="scroll-mt-4">
              <Card className={`overflow-hidden border-l-4 ${group.tone}`}>
                <div className="px-4 py-3 sm:px-5">
                  <h2 id={`${group.key}-title`} className="text-base font-semibold text-slate-900">
                    {group.title} <span className="font-normal text-slate-500">({count(group)})</span>
                  </h2>
                  <p className="text-xs text-slate-500">{group.intro}</p>
                </div>
                {group.sections.map((section) => (
                  <SectionBlock key={section.key} section={section} />
                ))}
              </Card>
            </section>
          ))}
          {other.map((section) => (
            <Card key={section.key} className="overflow-hidden">
              <SectionBlock section={section} />
            </Card>
          ))}
          <p className="text-xs text-slate-500">Updated {formatDateTime(report.generated_at)}.</p>
        </>
      )}
    </div>
  );
}
