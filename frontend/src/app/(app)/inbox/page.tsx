import type { Metadata } from "next";

import { InboxView } from "@/components/inbox/InboxView";

export const metadata: Metadata = { title: "Customer Replies · CollectFlow AI" };

export default function InboxPage() {
  return <InboxView />;
}
