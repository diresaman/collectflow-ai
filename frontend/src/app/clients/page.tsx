import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { ClientsView } from "@/components/clients/ClientsView";
import { AppShell } from "@/components/layout/AppShell";

export const metadata: Metadata = { title: "Clients · CollectFlow AI" };

export default function ClientsPage() {
  return (
    <SessionGate>
      <AppShell active="/clients">
        <ClientsView />
      </AppShell>
    </SessionGate>
  );
}
