/**
 * Server-side proxy for Django business endpoints.
 *
 * The browser calls /api/backend/<path> on this origin; this handler forwards the request to
 * Django with only the session and CSRF cookies plus the X-CSRFToken header, and relays the
 * cookies Django sets (login, logout, CSRF rotation). Credentials are checked by Django; the
 * proxy holds no secret. Only the endpoints listed below can be reached.
 */
import { NextResponse, type NextRequest } from "next/server";

type Method = "GET" | "POST" | "PATCH";

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

const ALLOWED_ROUTES: Record<Method, RegExp[]> = {
  GET: [
    /^auth\/csrf$/,
    /^me$/,
    /^invoices$/,
    new RegExp(`^invoices/${UUID}$`),
    /^dashboard$/,
    /^automation-runs$/,
    /^follow-ups$/,
    new RegExp(`^follow-ups/${UUID}$`),
    new RegExp(`^follow-ups/${UUID}/delivery-attempts$`),
    /^integrations\/gmail\/status$/,
    /^automation-runs\/\d+$/,
    /^automation\/workflows\/current$/,
    /^automation\/workflows\/\d+(\/steps|\/pending-actions)?$/,
    new RegExp(`^jobs/${UUID}$`),
    /^timeline$/,
  ],
  POST: [
    /^auth\/(login|logout)$/,
    /^sync$/,
    /^automation\/run$/,
    /^automation\/workflows\/\d+\/finalize$/,
    /^automation\/workflows\/\d+\/items\/\d+\/leave-unsent$/,
    new RegExp(`^invoices/${UUID}/analyze$`),
    new RegExp(`^follow-ups/${UUID}/(approve|reject|send)$`),
  ],
  PATCH: [new RegExp(`^follow-ups/${UUID}$`)],
};

// Provider calls run in the backend worker, so every request here is short. The Gmail status
// check is the slowest (token refresh plus identity lookup).
const TIMEOUT_MS: Record<Method, number> = { GET: 20_000, POST: 15_000, PATCH: 10_000 };

const MAX_BODY_BYTES = 20_000;

// The only cookies Django needs; anything else the browser holds for this origin stays here.
const FORWARDED_COOKIES = new Set(["sessionid", "csrftoken"]);

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

function forwardedCookieHeader(request: NextRequest): string {
  return request.cookies
    .getAll()
    .filter((cookie) => FORWARDED_COOKIES.has(cookie.name))
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");
}

async function proxy(request: NextRequest, context: RouteContext, method: Method): Promise<NextResponse> {
  const { path } = await context.params;
  const route = path.join("/");

  if (!ALLOWED_ROUTES[method].some((pattern) => pattern.test(route))) {
    return errorResponse(404, "Not found.");
  }
  // State-changing requests must come from this app, not another site in the same browser.
  // Django additionally requires the CSRF token on every write.
  if (method !== "GET" && !isSameOrigin(request)) {
    return errorResponse(403, "Cross-origin requests are not allowed.");
  }

  const baseUrl = process.env.API_BASE_URL;
  if (!baseUrl) {
    return errorResponse(500, "Proxy is not configured: set API_BASE_URL.");
  }

  let body: string | undefined;
  if (method !== "GET") {
    body = await request.text();
    if (body.length > MAX_BODY_BYTES) return errorResponse(413, "Request body is too large.");
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body) headers["Content-Type"] = "application/json";
  const cookie = forwardedCookieHeader(request);
  if (cookie) headers.Cookie = cookie;
  const csrfToken = request.headers.get("x-csrftoken");
  if (csrfToken) headers["X-CSRFToken"] = csrfToken;

  let upstream: Response;
  try {
    // Query strings are forwarded for reads only (e.g. ?kind=SYNC).
    const search = method === "GET" ? request.nextUrl.search : "";
    upstream = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/${route}/${search}`, {
      method,
      headers,
      body: body || undefined,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(TIMEOUT_MS[method]),
    });
  } catch {
    return errorResponse(502, "Backend API is unreachable.");
  }

  const responseBody = await upstream.text();
  const response = new NextResponse(responseBody, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
  });
  // Session and CSRF cookies set by Django (no Domain attribute, so they bind to this origin).
  for (const setCookie of upstream.headers.getSetCookie()) {
    response.headers.append("Set-Cookie", setCookie);
  }
  return response;
}

export function GET(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxy(request, context, "GET");
}

export function POST(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxy(request, context, "POST");
}

export function PATCH(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxy(request, context, "PATCH");
}
