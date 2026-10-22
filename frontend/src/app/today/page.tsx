import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppHeader } from "@/components/layout/AppHeader";
import { TodayView } from "@/components/today/TodayView";

export const metadata: Metadata = { title: "Today · CollectFlow AI" };

export default function TodayPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/today" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <TodayView />
        </main>
      </div>
    </SessionGate>
  );
}
