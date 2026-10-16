import { ApiStatus } from "@/components/ApiStatus";

const UPCOMING_FEATURES = [
  {
    title: "Invoice sync",
    description: "Pull open invoices from Google Sheets automatically.",
  },
  {
    title: "AI follow-ups",
    description: "Draft personalized reminders tailored to each client.",
  },
  {
    title: "Approved sending",
    description: "Review every message, then send it through Gmail.",
  },
] as const;

export default function Home() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-4 sm:px-6">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
            CF
          </div>
          <span className="text-base font-semibold text-slate-900">CollectFlow AI</span>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-12 sm:px-6">
        <div className="max-w-2xl">
          <p className="text-sm font-medium text-indigo-600">Accounts receivable, simplified</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">
            CollectFlow AI
          </h1>
          <p className="mt-4 text-base leading-7 text-slate-600">
            An AI-powered accounts receivable follow-up assistant for small service businesses.
            Track outstanding invoices and send timely, personalized reminders without the manual
            chasing.
          </p>
        </div>

        <div className="mt-10">
          <ApiStatus />
        </div>

        <section className="mt-10">
          <h2 className="text-sm font-semibold text-slate-900">Coming soon</h2>
          <ul className="mt-4 grid gap-4 sm:grid-cols-3">
            {UPCOMING_FEATURES.map((feature) => (
              <li key={feature.title} className="rounded-xl border border-dashed border-slate-300 bg-white p-5">
                <h3 className="text-sm font-medium text-slate-900">{feature.title}</h3>
                <p className="mt-1 text-sm text-slate-500">{feature.description}</p>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}
