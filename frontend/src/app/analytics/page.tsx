import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppHeader } from "@/components/layout/AppHeader";
import { AnalyticsView } from "@/components/analytics/AnalyticsView";

export const metadata: Metadata = { title: "Analytics · CollectFlow AI" };

export default function AnalyticsPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/analytics" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <AnalyticsView />
        </main>
      </div>
    </SessionGate>
  );
}
