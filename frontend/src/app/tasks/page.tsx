import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppHeader } from "@/components/layout/AppHeader";
import { TasksView } from "@/components/tasks/TasksView";

export const metadata: Metadata = { title: "Tasks · CollectFlow AI" };

export default function TasksPage() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/tasks" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <TasksView />
        </main>
      </div>
    </SessionGate>
  );
}
