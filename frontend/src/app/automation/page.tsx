import type { Metadata } from "next";

import { AutomationWorkspace } from "@/components/automation/AutomationWorkspace";
import { AppHeader } from "@/components/layout/AppHeader";

export const metadata: Metadata = { title: "Automation · CollectFlow AI" };

export default function AutomationPage() {
  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader active="/automation" />
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
        <AutomationWorkspace />
      </main>
    </div>
  );
}
