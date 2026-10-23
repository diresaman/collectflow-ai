import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppShell } from "@/components/layout/AppShell";
import { AnalyticsView } from "@/components/analytics/AnalyticsView";

export const metadata: Metadata = { title: "Analytics · CollectFlow AI" };

export default function AnalyticsPage() {
  return (
    <SessionGate>
      <AppShell active="/analytics">
        <AnalyticsView />
      </AppShell>
    </SessionGate>
  );
}
