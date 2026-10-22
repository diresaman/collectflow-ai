"use client";

import { useEffect, useState } from "react";

import { getReplySummary } from "@/lib/api/replies";

const REFRESH_MS = 60_000;

/** Number of customer replies waiting for a person, next to the Inbox link. */
export function InboxBadge() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      getReplySummary(controller.signal)
        .then((summary) => setCount(summary.needs_review))
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  if (count === 0) return null;
  return (
    <span className="ml-1.5 rounded-full bg-amber-500 px-1.5 py-0.5 text-[11px] font-semibold text-white" aria-label={`${count} to review`}>
      {count}
    </span>
  );
}
