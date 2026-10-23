import type { ReactNode } from "react";

import { SessionGate } from "@/components/auth/SessionGate";
import { AppShell } from "@/components/layout/AppShell";

/**
 * Shared by every signed-in page: the session is checked once and the sidebar stays mounted while
 * the user moves between pages, so only the content area changes (no full-screen reload).
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SessionGate>
      <AppShell>{children}</AppShell>
    </SessionGate>
  );
}
