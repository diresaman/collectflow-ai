import type { StepStatus, WorkflowStep } from "@/lib/api/workflows";
import { formatDateTime } from "@/lib/format";

import { Card, Spinner } from "../dashboard/ui";

const STATUS_META: Record<StepStatus, { label: string; dot: string; text: string }> = {
  PENDING: { label: "Pending", dot: "border-2 border-slate-300 bg-white", text: "text-slate-400" },
  RUNNING: { label: "In progress", dot: "bg-info-500", text: "text-info-700" },
  COMPLETED: { label: "Completed", dot: "bg-success-500", text: "text-success-700" },
  FAILED: { label: "Needs attention", dot: "bg-danger-500", text: "text-danger-700" },
  SKIPPED: { label: "Skipped", dot: "bg-slate-300", text: "text-slate-500" },
};

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "RUNNING") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-info-100 text-info-600">
        <Spinner className="h-3.5 w-3.5" />
      </span>
    );
  }
  const symbol = { COMPLETED: "✓", FAILED: "!", SKIPPED: "–", PENDING: "" }[status];
  return (
    <span
      className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold text-white ${STATUS_META[status].dot}`}
      aria-hidden="true"
    >
      {symbol}
    </span>
  );
}

export function StepTracker({ steps, currentStep }: { steps: WorkflowStep[]; currentStep: string }) {
  return (
    <Card className="p-5">
      <h2 className="text-sm font-semibold text-slate-900">Workflow steps</h2>
      <ol className="mt-4">
        {steps.map((step, index) => {
          const meta = STATUS_META[step.status];
          const current = step.key === currentStep && step.status !== "COMPLETED" && step.status !== "SKIPPED";
          return (
            <li key={step.key} className="relative flex gap-3 pb-5 last:pb-0" aria-current={current ? "step" : undefined}>
              {index < steps.length - 1 && (
                <span className="absolute left-3 top-6 h-[calc(100%-1.5rem)] w-px bg-slate-200" aria-hidden="true" />
              )}
              <StepIcon status={step.status} />
              <div className="min-w-0 flex-1 pt-0.5">
                <p className={`text-sm font-medium ${current ? "text-slate-900" : "text-slate-700"}`}>{step.label}</p>
                <p className={`text-xs ${meta.text}`}>{meta.label}</p>
                {step.detail && <p className="mt-0.5 text-xs text-slate-500">{step.detail}</p>}
                {(step.started_at || step.completed_at) && (
                  <p className="mt-0.5 text-[11px] text-slate-400">
                    {step.started_at && `Started ${formatDateTime(step.started_at)}`}
                    {step.completed_at && ` · done ${formatDateTime(step.completed_at)}`}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
