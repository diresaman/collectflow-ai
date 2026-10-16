import Link from "next/link";

import { ApiStatus } from "@/components/ApiStatus";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/automation", label: "Automation" },
] as const;

export function AppHeader({ active }: { active: "/" | "/automation" }) {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-6">
          <Link href="/" className="flex items-center gap-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">CF</span>
            <span className="hidden text-base font-semibold text-slate-900 sm:inline">CollectFlow AI</span>
          </Link>
          <nav className="flex gap-1" aria-label="Main">
            {LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active === link.href ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                  active === link.href ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:text-slate-800"
                }`}
              >
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
        <ApiStatus />
      </div>
    </header>
  );
}
