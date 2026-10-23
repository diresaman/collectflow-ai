"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { InboxBadge } from "./InboxBadge";

/** Everyday work first; configuration under "Setup". Every existing route stays reachable. */
export const PRIMARY_LINKS = [
  { href: "/today", label: "Today" },
  { href: "/inbox", label: "Inbox" },
  { href: "/", label: "Invoices" },
  { href: "/clients", label: "Clients" },
  { href: "/tasks", label: "Tasks" },
  { href: "/disputes", label: "Disputes" },
  { href: "/analytics", label: "Analytics" },
] as const;

export const SETUP_LINKS = [
  { href: "/sequences", label: "Sequences", hint: "Reminder steps and timing" },
  { href: "/automation", label: "Automation", hint: "Sync the sheet and draft due reminders" },
  { href: "/settings/integrations", label: "Integrations", hint: "Google Sheets, Gmail, Gemini, worker" },
] as const;

export type NavHref = (typeof PRIMARY_LINKS)[number]["href"] | (typeof SETUP_LINKS)[number]["href"];

function linkClass(active: boolean): string {
  return `inline-flex items-center rounded-md px-3 py-1.5 text-sm font-medium ${
    active ? "bg-slate-100 text-slate-900" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
  }`;
}

export function MainNav({ active }: { active: NavHref }) {
  const [setupOpen, setSetupOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const setupRef = useRef<HTMLDivElement>(null);
  const setupActive = SETUP_LINKS.some((link) => link.href === active);

  useEffect(() => {
    if (!setupOpen) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !setupRef.current?.contains(event.target as Node)) {
        setSetupOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [setupOpen]);

  return (
    <>
      <nav className="hidden items-center gap-0.5 lg:flex" aria-label="Main">
        {PRIMARY_LINKS.map((link) => (
          <Link key={link.href} href={link.href} aria-current={active === link.href ? "page" : undefined} className={linkClass(active === link.href)}>
            {link.label}
            {link.href === "/inbox" && <InboxBadge />}
          </Link>
        ))}
        <div className="relative" ref={setupRef}>
          <button
            type="button"
            aria-expanded={setupOpen}
            aria-haspopup="true"
            onClick={() => setSetupOpen((open) => !open)}
            className={linkClass(setupActive)}
          >
            Setup
            <span aria-hidden className="ml-1 text-xs">▾</span>
          </button>
          {setupOpen && (
            <div className="absolute left-0 z-40 mt-1 w-64 rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
              {SETUP_LINKS.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active === link.href ? "page" : undefined}
                  className={`block rounded-md px-3 py-2 ${active === link.href ? "bg-slate-100" : "hover:bg-slate-50"}`}
                >
                  <span className="block text-sm font-medium text-slate-900">{link.label}</span>
                  <span className="block text-xs text-slate-500">{link.hint}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </nav>

      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 lg:hidden"
        aria-expanded={mobileOpen}
        aria-controls="mobile-nav"
        onClick={() => setMobileOpen((open) => !open)}
      >
        Menu
        <InboxBadge />
      </button>
      {mobileOpen && (
        <nav id="mobile-nav" aria-label="Main" className="absolute inset-x-0 top-full z-40 border-b border-slate-200 bg-white px-4 py-3 shadow-lg lg:hidden">
          <div className="grid grid-cols-2 gap-1">
            {PRIMARY_LINKS.map((link) => (
              <Link key={link.href} href={link.href} aria-current={active === link.href ? "page" : undefined} className={linkClass(active === link.href)}>
                {link.label}
                {link.href === "/inbox" && <InboxBadge />}
              </Link>
            ))}
          </div>
          <p className="mt-3 px-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Setup</p>
          <div className="grid grid-cols-2 gap-1">
            {SETUP_LINKS.map((link) => (
              <Link key={link.href} href={link.href} aria-current={active === link.href ? "page" : undefined} className={linkClass(active === link.href)}>
                {link.label}
              </Link>
            ))}
          </div>
        </nav>
      )}
    </>
  );
}
