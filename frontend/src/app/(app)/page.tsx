import type { Metadata } from "next";

import { Dashboard } from "@/components/dashboard/Dashboard";

export const metadata: Metadata = { title: "Invoices · CollectFlow AI" };

export default function Home() {
  return <Dashboard />;
}
