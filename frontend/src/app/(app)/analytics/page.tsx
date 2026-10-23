import type { Metadata } from "next";

import { AnalyticsView } from "@/components/analytics/AnalyticsView";

export const metadata: Metadata = { title: "Reports & Insights · CollectFlow AI" };

export default function AnalyticsPage() {
  return <AnalyticsView />;
}
