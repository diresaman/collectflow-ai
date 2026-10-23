import type { Metadata } from "next";

import { ApprovalsView } from "@/components/approvals/ApprovalsView";
import { SessionGate } from "@/components/auth/SessionGate";
import { AppShell } from "@/components/layout/AppShell";

export const metadata: Metadata = { title: "Approvals · CollectFlow AI" };

export default function ApprovalsPage() {
  return (
    <SessionGate>
      <AppShell active="/approvals">
        <ApprovalsView />
      </AppShell>
    </SessionGate>
  );
}
