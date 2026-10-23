import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppShell } from "@/components/layout/AppShell";
import { DisputesView } from "@/components/disputes/DisputesView";

export const metadata: Metadata = { title: "Disputes · CollectFlow AI" };

export default function DisputesPage() {
  return (
    <SessionGate>
      <AppShell active="/disputes">
        <DisputesView />
      </AppShell>
    </SessionGate>
  );
}
