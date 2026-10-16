"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { getHealth, type HealthResponse } from "@/lib/api/health";

type HealthState =
  | { kind: "loading" }
  | { kind: "connected"; data: HealthResponse }
  | { kind: "unavailable"; message: string };

const BADGE_STYLES: Record<HealthState["kind"], string> = {
  loading: "bg-slate-100 text-slate-600 ring-slate-200",
  connected: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  unavailable: "bg-rose-50 text-rose-700 ring-rose-200",
};

const DOT_STYLES: Record<HealthState["kind"], string> = {
  loading: "bg-slate-400 animate-pulse",
  connected: "bg-emerald-500",
  unavailable: "bg-rose-500",
};

const LABELS: Record<HealthState["kind"], string> = {
  loading: "Checking API…",
  connected: "API Connected",
  unavailable: "API Unavailable",
};

export function ApiStatus() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  // Incrementing this re-runs the health check effect.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    getHealth(controller.signal)
      .then((data) => setState({ kind: "connected", data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof ApiError ? error.message : "Unexpected error.";
        setState({ kind: "unavailable", message });
      });

    return () => controller.abort();
  }, [attempt]);

  const retry = () => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  };

  const detail =
    state.kind === "connected"
      ? `Service ${state.data.service} is responding.`
      : state.kind === "unavailable"
        ? state.message
        : "Checking connection to the CollectFlow API.";

  return (
    <div className="flex items-center gap-2">
      <span
        role="status"
        aria-live="polite"
        title={detail}
        className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${BADGE_STYLES[state.kind]}`}
      >
        <span className={`h-2 w-2 rounded-full ${DOT_STYLES[state.kind]}`} aria-hidden="true" />
        {LABELS[state.kind]}
        <span className="sr-only">: {detail}</span>
      </span>
      {state.kind === "unavailable" && (
        <button
          type="button"
          onClick={retry}
          className="rounded-md px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
        >
          Retry
        </button>
      )}
    </div>
  );
}
