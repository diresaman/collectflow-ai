import type { Metadata } from "next";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppShell } from "@/components/layout/AppShell";
import { TasksView } from "@/components/tasks/TasksView";

export const metadata: Metadata = { title: "Tasks · CollectFlow AI" };

export default function TasksPage() {
  return (
    <SessionGate>
      <AppShell active="/tasks">
        <TasksView />
      </AppShell>
    </SessionGate>
  );
}
