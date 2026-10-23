import type { Metadata } from "next";

import { TasksView } from "@/components/tasks/TasksView";

export const metadata: Metadata = { title: "Tasks · CollectFlow AI" };

export default function TasksPage() {
  return <TasksView />;
}
