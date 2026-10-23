import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { InboxView } from "@/components/inbox/InboxView";
import { AppShell } from "@/components/layout/AppShell";

export const metadata: Metadata = { title: "Inbox · CollectFlow AI" };

export default function InboxPage() {
  return (
    <SessionGate>
      <AppShell active="/inbox">
        <InboxView />
      </AppShell>
    </SessionGate>
  );
}
