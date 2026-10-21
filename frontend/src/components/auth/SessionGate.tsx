"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { Logo } from "@/components/layout/Logo";
import { ErrorMessage, Spinner } from "@/components/dashboard/ui";
import { getMe, type CurrentUser } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

const SessionContext = createContext<CurrentUser | null>(null);

/** The signed-in user. Only usable below <SessionGate>. */
export function useCurrentUser(): CurrentUser {
  const user = useContext(SessionContext);
  if (user === null) throw new Error("useCurrentUser must be used inside <SessionGate>.");
  return user;
}

type State = { kind: "loading" } | { kind: "ready"; user: CurrentUser } | { kind: "error"; message: string };

/**
 * Renders its children only for a signed-in user. Without a session, the API client
 * redirects to /login (with ?next= so the user returns here after signing in).
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getMe(controller.signal)
      .then((user) => setState({ kind: "ready", user }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) {
          const next = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.replace(`/login?next=${next}`);
          return;
        }
        setState({ kind: "error", message: error instanceof ApiError ? error.message : "Unexpected error." });
      });
    return () => controller.abort();
  }, [attempt]);

  if (state.kind === "ready") {
    return <SessionContext.Provider value={state.user}>{children}</SessionContext.Provider>;
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-50 px-4">
      <Logo className="h-10 w-10" />
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-slate-500" role="status">
          <Spinner /> Checking your session…
        </p>
      ) : (
        <div className="w-full max-w-sm space-y-3">
          <ErrorMessage message={`Could not check your session: ${state.message}`} />
          <button
            type="button"
            onClick={() => {
              setState({ kind: "loading" });
              setAttempt((n) => n + 1);
            }}
            className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Try again
          </button>
        </div>
      )}
    </div>
  );
}
