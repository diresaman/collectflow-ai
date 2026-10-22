import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppHeader } from "@/components/layout/AppHeader";
import { SequencesView } from "@/components/sequences/SequencesView";

export const metadata: Metadata = { title: "Sequences · CollectFlow AI" };

export default function SequencesPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/sequences" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <SequencesView />
        </main>
      </div>
    </SessionGate>
  );
}
