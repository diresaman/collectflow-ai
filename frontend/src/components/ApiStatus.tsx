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

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Backend status</h2>
          <p className="mt-1 text-sm text-slate-500">
            Connection to the CollectFlow API.
          </p>
        </div>
        <span
          role="status"
          aria-live="polite"
          className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ring-1 ring-inset ${BADGE_STYLES[state.kind]}`}
        >
          <span className={`h-2 w-2 rounded-full ${DOT_STYLES[state.kind]}`} aria-hidden="true" />
          {LABELS[state.kind]}
        </span>
      </div>

      {state.kind === "connected" && (
        <p className="mt-4 text-sm text-slate-600">
          Service <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{state.data.service}</code>{" "}
          is responding.
        </p>
      )}

      {state.kind === "unavailable" && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-rose-50 px-4 py-3">
          <p className="text-sm text-rose-700">{state.message}</p>
          <button
            type="button"
            onClick={retry}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-rose-700 ring-1 ring-inset ring-rose-200 hover:bg-rose-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
          >
            Retry
          </button>
        </div>
      )}
    </section>
  );
}
