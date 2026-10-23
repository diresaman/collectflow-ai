import type { Metadata } from "next";

import { SequencesView } from "@/components/sequences/SequencesView";

export const metadata: Metadata = { title: "Reminder Schedule · CollectFlow AI" };

export default function SequencesPage() {
  return <SequencesView />;
}
