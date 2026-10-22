import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { InboxView } from "@/components/inbox/InboxView";
import { AppHeader } from "@/components/layout/AppHeader";

export const metadata: Metadata = { title: "Inbox · CollectFlow AI" };

export default function InboxPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/inbox" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <InboxView />
        </main>
      </div>
    </SessionGate>
  );
}
