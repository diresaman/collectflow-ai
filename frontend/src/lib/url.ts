/**
 * Deep links between screens (M12): /?invoice=<id>, /inbox?reply=<id>, /disputes?case=<id>, /tasks?task=<id>,
 * and /clients?client=<id> (M13).
 * Pages render only in the browser after sign-in (SessionGate), so reading the URL at mount is safe.
 */
export function queryParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}

/** Updates (or removes) one query parameter without navigating. */
export function setQueryParam(name: string, value: string | null): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (value) url.searchParams.set(name, value);
  else url.searchParams.delete(name);
  window.history.replaceState(null, "", url.toString());
}

export const links = {
  invoice: (id: string) => `/?invoice=${id}`,
  reply: (id: string) => `/inbox?reply=${id}`,
  dispute: (id: string) => `/disputes?case=${id}`,
  task: (id: string) => `/tasks?task=${id}`,
  client: (id: string) => `/clients?client=${id}`,
};
