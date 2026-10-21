"use client";

import { useSearchParams } from "next/navigation";
import { useId, useState, type FormEvent } from "react";

import { Card, ErrorMessage, Spinner } from "@/components/dashboard/ui";
import { Logo } from "@/components/layout/Logo";
import { login } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

/** Only same-app paths are allowed as a post-login destination. */
function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/login") ? value : "/";
}

export function LoginForm() {
  const next = safeNext(useSearchParams().get("next"));
  const ids = { username: useId(), password: useId() };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      window.location.assign(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unexpected error.");
      setPassword("");
      setSubmitting(false);
    }
  };

  const inputClass =
    "mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30";

  return (
    <Card className="w-full max-w-sm p-6 sm:p-8">
      <div className="mb-6 flex flex-col items-center gap-3 text-center">
        <Logo className="h-10 w-10" />
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Sign in to CollectFlow AI</h1>
          <p className="mt-1 text-sm text-slate-500">Use the account your admin created for you.</p>
        </div>
      </div>
      <form onSubmit={(event) => void submit(event)} className="space-y-4">
        <div>
          <label htmlFor={ids.username} className="text-sm font-medium text-slate-700">
            Username
          </label>
          <input
            id={ids.username}
            name="username"
            autoComplete="username"
            required
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor={ids.password} className="text-sm font-medium text-slate-700">
            Password
          </label>
          <input
            id={ids.password}
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={inputClass}
          />
        </div>
        {error && <ErrorMessage message={error} />}
        <button
          type="submit"
          disabled={submitting}
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting && <Spinner />}
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </Card>
  );
}
