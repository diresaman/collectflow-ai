import type { Metadata } from "next";

import { IntegrationsView } from "@/components/integrations/IntegrationsView";

export const metadata: Metadata = { title: "Integrations · CollectFlow AI" };

export default function IntegrationsPage() {
  return <IntegrationsView />;
}
