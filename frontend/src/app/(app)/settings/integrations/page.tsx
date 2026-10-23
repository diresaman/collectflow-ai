import type { Metadata } from "next";

import { IntegrationsView } from "@/components/integrations/IntegrationsView";

export const metadata: Metadata = { title: "Connected Services · CollectFlow AI" };

export default function IntegrationsPage() {
  return <IntegrationsView />;
}
