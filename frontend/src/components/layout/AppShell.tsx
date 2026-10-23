"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { ConnectionNotice } from "@/components/ApiStatus";
import { useCurrentUser } from "@/components/auth/SessionGate";
import { ROLE_LABELS, logout } from "@/lib/api/auth";
import { getToday } from "@/lib/api/today";

import { Logo } from "./Logo";

/**
 * The application frame: a sidebar in four groups named for what people do there (daily work first, then
 * customers and payments, reports and settings), with live counts, and a drawer on small screens. Labels are
 * display wording only; routes and API names stay as they are (e.g. /clients for Customers).
 */
const ICONS = {
  today: "M2.25 12l8.954-8.955a1.126 1.126 0 0 1 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75",
  inbox: "M2.25 13.5h3.86a2.25 2.25 0 0 1 2.012 1.244l.256.512a2.25 2.25 0 0 0 2.013 1.244h3.218a2.25 2.25 0 0 0 2.013-1.244l.256-.512a2.25 2.25 0 0 1 2.013-1.244h3.859M2.25 13.5V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18v-4.5M2.25 13.5l2.394-7.183A2.25 2.25 0 0 1 6.78 4.5h10.44a2.25 2.25 0 0 1 2.136 1.817L21.75 13.5",
  approvals: "M9 12.75 11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 0 1-1.043 3.296 3.745 3.745 0 0 1-3.296 1.043A3.745 3.745 0 0 1 12 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 0 1-3.296-1.043 3.745 3.745 0 0 1-1.043-3.296A3.745 3.745 0 0 1 3 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 0 1 1.043-3.296 3.746 3.746 0 0 1 3.296-1.043A3.746 3.746 0 0 1 12 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 0 1 3.296 1.043 3.746 3.746 0 0 1 1.043 3.296A3.745 3.745 0 0 1 21 12Z",
  tasks: "M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25ZM6.75 12h.008v.008H6.75V12Zm0 3h.008v.008H6.75V15Zm0 3h.008v.008H6.75V18Z",
  disputes: "M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z",
  clients: "M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z",
  invoices: "M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z",
  analytics: "M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z",
  sequences: "M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12",
  automation: "M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75Z",
  integrations: "M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244",
} as const;

type Item = { href: string; label: string; icon: keyof typeof ICONS; count?: string };

export const NAV_GROUPS: { title: string; items: Item[] }[] = [
  {
    title: "Workspace",
    items: [
      { href: "/today", label: "Today's Work", icon: "today", count: "today" },
      { href: "/inbox", label: "Customer Replies", icon: "inbox", count: "replies" },
      { href: "/approvals", label: "Approvals", icon: "approvals", count: "approvals" },
      { href: "/tasks", label: "Tasks", icon: "tasks", count: "tasks" },
      { href: "/disputes", label: "Disputes", icon: "disputes", count: "disputes" },
    ],
  },
  {
    title: "Customers & Payments",
    items: [
      { href: "/clients", label: "Customers", icon: "clients" },
      { href: "/invoices", label: "Invoices", icon: "invoices" },
    ],
  },
  { title: "Reports", items: [{ href: "/analytics", label: "Reports & Insights", icon: "analytics" }] },
  {
    title: "Settings",
    items: [
      { href: "/sequences", label: "Reminder Schedule", icon: "sequences" },
      { href: "/automation", label: "Automation Settings", icon: "automation" },
      { href: "/settings/integrations", label: "Connected Services", icon: "integrations", count: "integrations" },
    ],
  },
];

/** The menu item for the current URL (nested pages match their section). */
function activeHref(pathname: string): string {
  const hrefs = NAV_GROUPS.flatMap((group) => group.items.map((item) => item.href));
  return hrefs.find((href) => pathname === href || pathname.startsWith(`${href}/`)) ?? "";
}

/** Live counts from the Today report (one request a minute), keyed like Item.count. */
function useCounts(): Record<string, number> {
  const [counts, setCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      getToday(controller.signal)
        .then((report) => {
          const n = (key: string) => report.sections.find((s) => s.key === key)?.items.length ?? 0;
          setCounts({
            today: n("integrations") + n("replies") + n("deliveries") + n("disputes") + n("drafts") + n("approved") + n("tasks"),
            replies: n("replies"),
            approvals: n("drafts") + n("approved"),
            tasks: n("tasks"),
            disputes: n("disputes"),
            integrations: n("integrations"),
          });
        })
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);
  return counts;
}

function NavIcon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} className="h-5 w-5 shrink-0" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d={ICONS[name]} />
    </svg>
  );
}

function Sidebar({ active, counts, onNavigate }: { active: string; counts: Record<string, number>; onNavigate?: () => void }) {
  const user = useCurrentUser();
  const [signingOut, setSigningOut] = useState(false);
  const signOut = async () => {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      window.location.replace("/login");
    }
  };
  return (
    <div className="flex h-full flex-col">
      <Link
        href="/today"
        onClick={onNavigate}
        aria-label="CollectFlow AI, Today's Work"
        className="flex items-center gap-2.5 rounded-lg px-5 py-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500"
      >
        <Logo />
        <span className="text-base font-semibold text-slate-900">CollectFlow AI</span>
      </Link>
      <nav aria-label="Main" className="flex-1 space-y-6 overflow-y-auto px-3 pb-4">
        {NAV_GROUPS.map((group) => {
          const headingId = `nav-${group.title.toLowerCase().replace(/[^a-z]+/g, "-")}`;
          return (
            <div key={group.title}>
              <p id={headingId} className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                {group.title}
              </p>
              <ul className="space-y-0.5" aria-labelledby={headingId}>
                {group.items.map((item) => {
                  const isActive = active === item.href;
                  const count = item.count ? (counts[item.count] ?? 0) : 0;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={onNavigate}
                        aria-current={isActive ? "page" : undefined}
                        title={item.label}
                        className={`flex min-h-10 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                          isActive ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                        }`}
                      >
                        <NavIcon name={item.icon} />
                        <span className="min-w-0 flex-1 truncate">{item.label}</span>
                        {count > 0 &&
                          (item.count === "integrations" ? (
                            <span className="rounded-full bg-rose-100 px-1.5 text-xs font-semibold text-rose-700" aria-label={`${count} problem(s)`}>
                              !
                            </span>
                          ) : (
                            <span
                              className={`min-w-5 rounded-full px-1.5 text-center text-xs font-semibold ${
                                isActive ? "bg-indigo-600 text-white" : "bg-slate-200 text-slate-700"
                              }`}
                              aria-label={`${count} waiting`}
                            >
                              {count}
                            </span>
                          ))}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </nav>
      <div className="border-t border-slate-200 px-5 py-4">
        <p className="truncate text-sm font-medium text-slate-900">{user.display_name}</p>
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-slate-500">{ROLE_LABELS[user.role]}</p>
          <button
            type="button"
            onClick={() => void signOut()}
            disabled={signingOut}
            className="rounded px-1 py-0.5 text-xs font-medium text-slate-600 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-60"
          >
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const active = activeHref(pathname);
  const counts = useCounts();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="min-h-screen bg-slate-50">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:block">
        <Sidebar active={active} counts={counts} />
      </aside>

      {/* Small screens: a top bar and a slide-in drawer with the same sidebar. */}
      <div className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 lg:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={open}
          aria-controls="nav-drawer"
          className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5" aria-hidden="true">
            <path strokeLinecap="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
          Menu
          {(counts.today ?? 0) > 0 && (
            <span className="rounded-full bg-indigo-600 px-1.5 text-xs font-semibold text-white">{counts.today}</span>
          )}
        </button>
        <Link href="/today" className="flex items-center gap-2">
          <Logo className="h-7 w-7" />
          <span className="text-sm font-semibold text-slate-900">CollectFlow AI</span>
        </Link>
      </div>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div id="nav-drawer" className="absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-white shadow-xl">
            <Sidebar active={active} counts={counts} onNavigate={() => setOpen(false)} />
          </div>
        </div>
      )}

      <div className="lg:pl-64">
        <ConnectionNotice />
        <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
