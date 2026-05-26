import { NextRequest, NextResponse } from "next/server";

// API-key middleware. Per the plan, the UI is internal/demo and auth is
// a single shared key. Set OBSERVE_API_KEY in the env and pass it via
// `x-observe-api-key` header (or `?key=...` query param for browser GETs).
// /healthz is unauthenticated for liveness probes.

const PUBLIC_PATHS = new Set(["/healthz", "/api/healthz"]);

export function middleware(req: NextRequest) {
  if (PUBLIC_PATHS.has(req.nextUrl.pathname)) {
    return NextResponse.next();
  }

  const expected = process.env.OBSERVE_API_KEY;
  if (!expected) {
    // No key configured — open access. Useful in tests / first run, but the
    // README and CLAUDE.md call this out as a hard requirement for any
    // non-localhost deployment.
    return NextResponse.next();
  }

  const fromHeader = req.headers.get("x-observe-api-key");
  const fromQuery = req.nextUrl.searchParams.get("key");
  if (fromHeader === expected || fromQuery === expected) {
    return NextResponse.next();
  }

  // For HTML routes, redirect to a login-shaped page (Phase 7 — for now,
  // just 401). For API routes, return JSON.
  if (req.nextUrl.pathname.startsWith("/api/")) {
    return new NextResponse(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  }
  return new NextResponse("Unauthorized", { status: 401 });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
