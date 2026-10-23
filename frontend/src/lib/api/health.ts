import { backendGet } from "./client";

export type HealthResponse = {
  status: "ok";
  service: string;
};

function isHealthResponse(data: unknown): data is HealthResponse {
  if (typeof data !== "object" || data === null) return false;
  const record = data as Record<string, unknown>;
  return record.status === "ok" && typeof record.service === "string";
}

/** Through the same-origin proxy, like every other call, so it reflects what the app can actually reach. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return backendGet("/health/", isHealthResponse, { signal });
}
