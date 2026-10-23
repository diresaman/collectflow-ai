import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppShell } from "@/components/layout/AppShell";
import { SequencesView } from "@/components/sequences/SequencesView";

export const metadata: Metadata = { title: "Sequences · CollectFlow AI" };

export default function SequencesPage() {
  return (
    <SessionGate>
      <AppShell active="/sequences">
        <SequencesView />
      </AppShell>
    </SessionGate>
  );
}
