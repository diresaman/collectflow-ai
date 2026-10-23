"use client";

import { useEffect, useState } from "react";

import { getHealth } from "@/lib/api/health";

/**
 * Says nothing while the server answers. When it does not, one plain banner explains the effect and
 * offers a retry (checked through the same proxy as every other request).
 */
export function ConnectionNotice() {
  const [down, setDown] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then(() => setDown(false))
      .catch(() => {
        if (!controller.signal.aborted) setDown(true);
      });
    return () => controller.abort();
  }, [attempt]);

  if (!down) return null;
  return (
    <div role="alert" className="border-b border-danger-200 bg-danger-50">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm text-danger-800 sm:px-6">
        <span>
          <span className="font-semibold">Cannot reach the CollectFlow server.</span> Lists may be out of date and
          actions will not be saved until it is back.
        </span>
        <button type="button" onClick={() => setAttempt((n) => n + 1)} className="rounded-md px-2 py-1 font-medium hover:bg-danger-100">
          Try again
        </button>
      </div>
    </div>
  );
}
