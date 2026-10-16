const DEFAULT_TIMEOUT_MS = 5000;

/** Same-origin Next.js route that forwards business requests to Django with the operator token. */
const BACKEND_PROXY_PREFIX = "/api/backend";

export class ApiError extends Error {
  readonly status: number | null;
  /** Parsed JSON error body, when the server returned one. */
  readonly body: unknown;

  constructor(message: string, status: number | null = null, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
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

type RequestOptions = {
  signal?: AbortSignal;
  timeoutMs?: number;
  /** JSON-serializable request body (POST/PATCH only). */
  body?: unknown;
};

type Validator<T> = (data: unknown) => data is T;

function errorMessage(status: number, body: unknown): string {
  if (typeof body === "object" && body !== null) {
    const detail = (body as Record<string, unknown>).detail;
    if (typeof detail === "string") return detail;
  }
  return `API responded with HTTP ${status}.`;
}

async function request<T>(
  url: string,
  method: "GET" | "POST" | "PATCH",
  isValid: Validator<T>,
  { signal, timeoutMs = DEFAULT_TIMEOUT_MS, body }: RequestOptions,
): Promise<T> {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const combinedSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers:
        body === undefined
          ? { Accept: "application/json" }
          : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: combinedSignal,
    });
  } catch (error) {
    if (timeoutSignal.aborted) {
      throw new ApiError(`Request timed out after ${timeoutMs}ms.`);
    }
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Network error: unable to reach the API.");
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    if (!response.ok) throw new ApiError(`API responded with HTTP ${response.status}.`, response.status);
    throw new ApiError("API returned a non-JSON response.", response.status);
  }

  if (!response.ok) {
    throw new ApiError(errorMessage(response.status, data), response.status, data);
  }

  if (!isValid(data)) {
    throw new ApiError("API returned an unexpected response shape.", response.status);
  }

  return data;
}

/**
 * Performs a JSON GET request directly against the Django API. Only for public
 * endpoints such as the health check: no credentials are attached.
 */
export async function apiGet<T>(path: string, isValid: Validator<T>, options: RequestOptions = {}): Promise<T> {
  return request(`${getApiBaseUrl()}${path}`, "GET", isValid, options);
}

/** GET a business endpoint through the server-side proxy, e.g. backendGet("/invoices/", ...). */
export async function backendGet<T>(path: string, isValid: Validator<T>, options: RequestOptions = {}): Promise<T> {
  return request(`${BACKEND_PROXY_PREFIX}${path}`, "GET", isValid, options);
}

/** POST to a business endpoint through the server-side proxy. */
export async function backendPost<T>(path: string, isValid: Validator<T>, options: RequestOptions = {}): Promise<T> {
  return request(`${BACKEND_PROXY_PREFIX}${path}`, "POST", isValid, options);
}

/** PATCH a business endpoint through the server-side proxy. */
export async function backendPatch<T>(path: string, isValid: Validator<T>, options: RequestOptions = {}): Promise<T> {
  return request(`${BACKEND_PROXY_PREFIX}${path}`, "PATCH", isValid, options);
}
