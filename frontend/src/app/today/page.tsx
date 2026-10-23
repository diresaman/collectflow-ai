import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppShell } from "@/components/layout/AppShell";
import { TodayView } from "@/components/today/TodayView";

export const metadata: Metadata = { title: "Today · CollectFlow AI" };

export default function TodayPage() {
  return (
    <SessionGate>
      <AppShell active="/today">
        <TodayView />
      </AppShell>
    </SessionGate>
  );
}
