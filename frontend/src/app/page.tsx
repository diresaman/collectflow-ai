import { SessionGate } from "@/components/auth/SessionGate";
import { Dashboard } from "@/components/dashboard/Dashboard";
import { AppHeader } from "@/components/layout/AppHeader";

export default function Home() {
  return (
    <SessionGate>
      <div className="min-h-screen bg-slate-50">
        <AppHeader active="/" />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          <Dashboard />
        </main>
      </div>
    </SessionGate>
  );
}
