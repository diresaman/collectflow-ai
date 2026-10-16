import { apiGet } from "./client";

export type HealthResponse = {
  status: "ok";
  service: string;
};

function isHealthResponse(data: unknown): data is HealthResponse {
  if (typeof data !== "object" || data === null) return false;
  const record = data as Record<string, unknown>;
  return record.status === "ok" && typeof record.service === "string";
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiGet("/api/health/", isHealthResponse, { signal });
}
