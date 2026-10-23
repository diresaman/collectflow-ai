import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { IntegrationsView } from "@/components/integrations/IntegrationsView";
import { AppShell } from "@/components/layout/AppShell";

export const metadata: Metadata = { title: "Integrations · CollectFlow AI" };

export default function IntegrationsPage() {
  return (
    <SessionGate>
      <AppShell active="/settings/integrations">
        <IntegrationsView />
      </AppShell>
    </SessionGate>
  );
}
