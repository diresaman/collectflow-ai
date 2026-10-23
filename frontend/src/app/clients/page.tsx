import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { ClientsView } from "@/components/clients/ClientsView";
import { AppHeader } from "@/components/layout/AppHeader";

export const metadata: Metadata = { title: "Clients · CollectFlow AI" };

export default function ClientsPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/clients" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <ClientsView />
        </main>
      </div>
    </SessionGate>
  );
}
