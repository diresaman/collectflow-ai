import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AutomationWorkspace } from "@/components/automation/AutomationWorkspace";
import { AppShell } from "@/components/layout/AppShell";

export const metadata: Metadata = { title: "Automation · CollectFlow AI" };

export default function AutomationPage() {
  return (
    <SessionGate>
      <AppShell active="/automation">
        <AutomationWorkspace />
      </AppShell>
    </SessionGate>
  );
}
