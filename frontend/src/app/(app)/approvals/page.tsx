import type { Metadata } from "next";

import { ApprovalsView } from "@/components/approvals/ApprovalsView";

export const metadata: Metadata = { title: "Approvals · CollectFlow AI" };

export default function ApprovalsPage() {
  return <ApprovalsView />;
}
