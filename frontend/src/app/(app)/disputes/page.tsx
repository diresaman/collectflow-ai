import type { Metadata } from "next";

import { DisputesView } from "@/components/disputes/DisputesView";

export const metadata: Metadata = { title: "Disputes · CollectFlow AI" };

export default function DisputesPage() {
  return <DisputesView />;
}
