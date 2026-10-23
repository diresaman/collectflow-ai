import Link from "next/link";

import { ConnectionNotice } from "@/components/ApiStatus";
import { UserMenu } from "@/components/auth/UserMenu";

import { Logo } from "./Logo";
import { MainNav, type NavHref } from "./MainNav";

export function AppHeader({ active }: { active: NavHref }) {
  return (
    <>
      <header className="relative border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-5">
            <Link href="/today" className="flex shrink-0 items-center gap-2.5">
              <Logo />
              <span className="hidden text-base font-semibold text-slate-900 sm:inline">CollectFlow AI</span>
            </Link>
            <MainNav active={active} />
          </div>
          <UserMenu />
        </div>
      </header>
      <ConnectionNotice />
    </>
  );
}
