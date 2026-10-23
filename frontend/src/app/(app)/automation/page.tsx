import type { Metadata } from "next";

import { AutomationWorkspace } from "@/components/automation/AutomationWorkspace";

export const metadata: Metadata = { title: "Automation · CollectFlow AI" };

export default function AutomationPage() {
  return <AutomationWorkspace />;
}
