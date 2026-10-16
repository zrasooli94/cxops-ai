import { NextRequest, NextResponse } from "next/server";
import { previewGateFromEnvironment } from "@/lib/staging/preview-isolation";

// Public early-access BFF (Phase 1Q).
//
// No session, no cookies, no tenant: the backend endpoint is public, feature-
// flagged (off until release is approved) and rate-limited. This handler only
// forwards a bounded JSON body. It deliberately does NOT forward the visitor's
// headers - the API needs nothing from them, and forwarding nothing keeps
// cookies and client hints out of the backend request.

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";
const MAX_BODY_BYTES = 16 * 1024;

async function forward(path: string, init: RequestInit): Promise<NextResponse> {
  if (previewGateFromEnvironment() === "blocked") {
    return NextResponse.json(
      { detail: "Public API calls from this preview are disabled until staging isolation is verified." },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  try {
    const response = await fetch(`${BACKEND_API_URL}${path}`, { ...init, cache: "no-store" });
    const body = await response.arrayBuffer();
    return new NextResponse(body, {
      status: response.status,
      headers: {
        "content-type": response.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "The request service is temporarily unavailable." },
      { status: 502, headers: { "cache-control": "no-store" } },
    );
  }
}

export async function GET() {
  return forward("/public/leads/availability", {
    method: "GET",
    headers: { accept: "application/json" },
  });
}

export async function POST(request: NextRequest) {
  if (previewGateFromEnvironment() === "blocked") {
    return NextResponse.json({ detail: "Preview lead submission is disabled." }, { status: 503 });
  }
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    return NextResponse.json({ detail: "Expected JSON." }, { status: 415 });
  }
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_BODY_BYTES) {
    return NextResponse.json({ detail: "Request too large." }, { status: 413 });
  }
  return forward("/public/leads", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body,
  });
}
