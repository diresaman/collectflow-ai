import { ApiStatus } from "@/components/ApiStatus";
import { Dashboard } from "@/components/dashboard/Dashboard";

export default function Home() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
              CF
            </div>
            <span className="text-base font-semibold text-slate-900">CollectFlow AI</span>
          </div>
          <ApiStatus />
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
        <Dashboard />
      </main>
    </div>
  );
}
