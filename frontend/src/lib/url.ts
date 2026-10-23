import { useSearchParams } from "next/navigation";

/**
 * Deep links between screens (M12): /invoices?invoice=<id>, /inbox?reply=<id>, /disputes?case=<id>,
 * /tasks?task=<id>, and /clients?client=<id> (M13). The older /?invoice=<id>, which the API still returns, is
 * redirected to /invoices (next.config.ts).
 */

/**
 * The current value of a deep-link parameter, kept up to date when the user moves between pages inside the
 * app (the shared layout stays mounted, so a page cannot read the URL only once at mount).
 */
export function useUrlParam(name: string): string | null {
  return useSearchParams().get(name);
}

/** Updates (or removes) one query parameter without navigating. */
export function setQueryParam(name: string, value: string | null): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (value) url.searchParams.set(name, value);
  else url.searchParams.delete(name);
  window.history.replaceState(null, "", url.toString());
}

/** An app link from the API, with the older invoice form (/?invoice=<id>) mapped to the Invoices page. */
export function appLink(link: string): string {
  return link.startsWith("/?") ? `/invoices${link.slice(1)}` : link;
}

export const links = {
  invoice: (id: string) => `/invoices?invoice=${id}`,
  reply: (id: string) => `/inbox?reply=${id}`,
  dispute: (id: string) => `/disputes?case=${id}`,
  task: (id: string) => `/tasks?task=${id}`,
  client: (id: string) => `/clients?client=${id}`,
};
