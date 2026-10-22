import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { IntegrationsView } from "@/components/integrations/IntegrationsView";
import { AppHeader } from "@/components/layout/AppHeader";

export const metadata: Metadata = { title: "Integrations · CollectFlow AI" };

export default function IntegrationsPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/settings/integrations" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <IntegrationsView />
        </main>
      </div>
    </SessionGate>
  );
}
