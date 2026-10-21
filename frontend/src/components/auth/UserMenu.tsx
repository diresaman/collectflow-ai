"use client";

import { useState } from "react";

import { logout, ROLE_LABELS } from "@/lib/api/auth";

import { useCurrentUser } from "./SessionGate";

export function UserMenu() {
  const user = useCurrentUser();
  const [signingOut, setSigningOut] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      // Leave even if the request failed: a full page load drops all in-memory state.
      window.location.replace("/login");
    }
  };

  return (
    <div className="flex items-center gap-3">
      <div className="hidden text-right sm:block">
        <p className="text-sm font-medium leading-tight text-slate-900">{user.display_name}</p>
        <p className="text-xs leading-tight text-slate-500">{ROLE_LABELS[user.role]}</p>
      </div>
      <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-200 sm:hidden">
        {ROLE_LABELS[user.role]}
      </span>
      <button
        type="button"
        onClick={() => void signOut()}
        disabled={signingOut}
        className="rounded-md px-2.5 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:opacity-60"
      >
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
