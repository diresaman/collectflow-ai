import type { Metadata } from "next";

import { TodayView } from "@/components/today/TodayView";

export const metadata: Metadata = { title: "Today's Work · CollectFlow AI" };

export default function TodayPage() {
  return <TodayView />;
}
