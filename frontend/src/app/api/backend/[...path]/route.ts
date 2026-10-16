/**
 * Server-side proxy for Django business endpoints.
 *
 * The browser calls /api/backend/<path>; this handler forwards the request to Django
 * with the operator token from server-only environment variables, so the token never
 * reaches the browser bundle. Only the endpoints listed below can be reached.
 */
import { NextResponse, type NextRequest } from "next/server";

type Method = "GET" | "POST";

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

const ALLOWED_ROUTES: Record<Method, RegExp[]> = {
  GET: [/^invoices$/, new RegExp(`^invoices/${UUID}$`), /^dashboard$/, /^automation-runs$/],
  POST: [/^sync$/],
};

const TIMEOUT_MS: Record<Method, number> = { GET: 10_000, POST: 40_000 };

type RouteContext = { params: Promise<{ path: string[] }> };

function errorResponse(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status });
}

function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  // Non-browser clients and same-origin GETs may omit Origin.
  if (origin === null) return request.headers.get("sec-fetch-site") !== "cross-site";
  return origin === request.nextUrl.origin;
}

async function proxy(request: NextRequest, context: RouteContext, method: Method): Promise<NextResponse> {
  const { path } = await context.params;
  const route = path.join("/");

  if (!ALLOWED_ROUTES[method].some((pattern) => pattern.test(route))) {
    return errorResponse(404, "Not found.");
  }
  // State-changing requests must come from this app, not another site in the same browser.
  if (method === "POST" && !isSameOrigin(request)) {
    return errorResponse(403, "Cross-origin requests are not allowed.");
  }

  const baseUrl = process.env.API_BASE_URL;
  const token = process.env.COLLECTFLOW_OPERATOR_TOKEN;
  if (!baseUrl || !token) {
    return errorResponse(500, "Proxy is not configured: set API_BASE_URL and COLLECTFLOW_OPERATOR_TOKEN.");
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/${route}/`, {
      method,
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS[method]),
    });
  } catch {
    return errorResponse(502, "Backend API is unreachable.");
  }

  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
  });
}

export function GET(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxy(request, context, "GET");
}

export function POST(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxy(request, context, "POST");
}
