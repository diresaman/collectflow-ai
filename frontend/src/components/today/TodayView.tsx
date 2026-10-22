"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Card, ErrorMessage, Skeleton, Spinner } from "@/components/dashboard/ui";
import { ApiError } from "@/lib/api/client";
import { getToday, type TodayItem, type TodayReport } from "@/lib/api/today";
import { formatDateTime } from "@/lib/format";

const REFRESH_MS = 60_000;
/** Sections shown even when empty, so "nothing to do" is visible rather than ambiguous. */
const ALWAYS_SHOWN = new Set(["replies", "drafts", "tasks"]);

const SECTION_HINTS: Record<string, string> = {
  integrations: "Fix these first: other work may depend on them.",
  replies: "Customer replies stop chasing until someone reviews them.",
  disputes: "No reminder is drafted, approved or sent for these invoices until an approver resolves the case.",
  deliveries: "Never resent automatically. Check Gmail, then reconcile.",
  drafts: "Nothing is sent until a person approves and then sends.",
  approved: "Approved but not sent yet.",
  broken_promises: "The next reminder is one tone firmer; review it when it is drafted, or call.",
  promises_due: "Settled automatically from the sheet within the 2-day grace period.",
  tasks: "Due today or overdue, oldest first.",
  sequence_blocks: "These sequences cannot move until a person acts.",
  held: "Why each invoice is not being chased, and who can end the hold.",
};

function Item({ item }: { item: TodayItem }) {
  return (
    <li className="flex flex-col gap-2 px-5 py-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-0.5">
        <p className="text-sm font-medium text-slate-900">
          {item.title}
          {item.overdue && <span className="ml-2 text-xs font-semibold text-rose-700">overdue</span>}
        </p>
        <p className="text-xs text-slate-500">
          {item.invoice_external_id ? `${item.invoice_external_id} · ${item.client_name} · ` : ""}
          {item.since ? `since ${formatDateTime(item.since)} · ` : ""}
          who can act: {item.who_can_act}
        </p>
        {item.detail && <p className="text-sm text-slate-700">{item.detail}</p>}
        {item.action && <p className="text-xs text-slate-600">What to do: {item.action}</p>}
        {item.blocked && (
          <p className="text-xs text-amber-800">
            <span className="font-semibold">You cannot do this yourself:</span> {item.blocked}
          </p>
        )}
      </div>
      <Link
        href={item.link}
        className="shrink-0 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
      >
        Open
      </Link>
    </li>
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Today</h1>
          <p className="text-sm text-slate-600">
            What needs a person now, in a fixed order. Nothing here is scored or predicted: it comes straight from the
            records.
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          {loading && <Spinner />}
          Refresh
        </button>
      </div>
      {error && (
        <ErrorMessage
          message={`Could not load today's work: ${error}${report ? " Showing the last loaded list." : ""}`}
        />
      )}
      {report === null && !error && (
        <div className="space-y-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      )}
      {report && (
        <>
          <p className="text-sm text-slate-600">
            {report.total === 0 ? "Nothing needs attention right now." : `${report.total} item(s) need attention.`} Updated{" "}
            {formatDateTime(report.generated_at)}; refreshes every minute.
          </p>
          {report.sections
            .filter((section) => section.items.length > 0 || ALWAYS_SHOWN.has(section.key))
            .map((section) => (
              <Card key={section.key} className="overflow-hidden">
                <div className="border-b border-slate-200 px-5 py-3">
                  <h2 className="text-base font-semibold text-slate-900">
                    {section.title} <span className="font-normal text-slate-500">({section.items.length})</span>
                  </h2>
                  <p className="text-xs text-slate-500">{SECTION_HINTS[section.key]}</p>
                </div>
                {section.items.length === 0 ? (
                  <p className="px-5 py-3 text-sm text-slate-500">None.</p>
                ) : (
                  <ul className="divide-y divide-slate-100">
                    {section.items.map((item) => (
                      <Item key={item.key} item={item} />
                    ))}
                  </ul>
                )}
              </Card>
            ))}
        </>
      )}
    </div>
  );
}
