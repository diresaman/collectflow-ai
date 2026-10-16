const DEFAULT_TIMEOUT_MS = 5000;

export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function getApiBaseUrl(): string {
  // Must be referenced statically so Next.js can inline it at build time.
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrl) {
    throw new ApiError("NEXT_PUBLIC_API_BASE_URL is not configured.");
  }
  return baseUrl.replace(/\/+$/, "");
}

type ApiGetOptions = {
  signal?: AbortSignal;
  timeoutMs?: number;
};

/**
 * Performs a JSON GET request against the Django API and validates the payload
 * with the provided type guard.
 */
export async function apiGet<T>(
  path: string,
  isValid: (data: unknown) => data is T,
  { signal, timeoutMs = DEFAULT_TIMEOUT_MS }: ApiGetOptions = {},
): Promise<T> {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const combinedSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;

  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: combinedSignal,
    });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timeoutSignal.aborted) {
      throw new ApiError(`Request timed out after ${timeoutMs}ms.`);
    }
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Network error: unable to reach the API.");
  }

  if (!response.ok) {
    throw new ApiError(`API responded with HTTP ${response.status}.`, response.status);
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError("API returned a non-JSON response.", response.status);
  }

  if (!isValid(data)) {
    throw new ApiError("API returned an unexpected response shape.", response.status);
  }

  return data;
}
