import { backendGet } from "./client";

export type TodayItem = {
  key: string;
  title: string;
  detail: string;
  since: string | null;
  link: string;
  /** Which role can act on it, in plain words. */
  who_can_act: string;
  invoice_id: string | null;
  invoice_external_id: string | null;
  client_name: string | null;
  /** Why the signed-in user cannot act on it themselves (empty if they can). */
  blocked: string;
  action?: string;
  overdue?: boolean;
};

export type TodaySection = { key: string; title: string; items: TodayItem[] };
export type TodayReport = { generated_at: string; role: string; total: number; sections: TodaySection[] };

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

function isReport(v: unknown): v is TodayReport {
  return (
    isRec(v) &&
    typeof v.total === "number" &&
    Array.isArray(v.sections) &&
    v.sections.every((s) => isRec(s) && typeof s.key === "string" && Array.isArray(s.items))
  );
}

export function getToday(signal?: AbortSignal): Promise<TodayReport> {
  return backendGet("/today/", isReport, { signal });
}
