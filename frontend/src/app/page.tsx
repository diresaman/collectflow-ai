import { SessionGate } from "@/components/auth/SessionGate";
import { Dashboard } from "@/components/dashboard/Dashboard";
import { AppShell } from "@/components/layout/AppShell";

export default function Home() {
  return (
    <SessionGate>
      <AppShell active="/">
        <Dashboard />
      </AppShell>
    </SessionGate>
  );
}
