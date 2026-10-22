import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppHeader } from "@/components/layout/AppHeader";
import { DisputesView } from "@/components/disputes/DisputesView";

export const metadata: Metadata = { title: "Disputes · CollectFlow AI" };

export default function DisputesPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/disputes" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <DisputesView />
        </main>
      </div>
    </SessionGate>
  );
}
