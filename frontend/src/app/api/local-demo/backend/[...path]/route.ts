import { createHmac } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { isAllowedLocalDemoPath, isAuthorizedLocalDemoRequest } from "@/lib/local-demo/security";

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";
const SUBJECT = "cxops-local-owner";
function base64url(value: string) {
  return Buffer.from(value).toString("base64url");
}

function token(secret: string) {
  const now = Math.floor(Date.now() / 1000);
  const header = base64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64url(JSON.stringify({ sub: SUBJECT, iat: now, exp: now + 300 }));
  const body = `${header}.${payload}`;
  const signature = createHmac("sha256", secret).update(body).digest("base64url");
  return `${body}.${signature}`;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const hostname = request.nextUrl.hostname;
  if (!isAuthorizedLocalDemoRequest({
    nodeEnv: process.env.NODE_ENV,
    hostname,
    method: request.method,
    origin: request.headers.get("origin"),
    fetchSite: request.headers.get("sec-fetch-site"),
    localOrigin: request.headers.get("x-cxops-local-origin"),
    requestOrigin: request.nextUrl.origin,
  })) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const secret = process.env.AUTH_JWT_SECRET;
  const organizationId = process.env.LOCAL_DEMO_ORGANIZATION_ID;
  if (!secret || secret.length < 32 || !organizationId) {
    return NextResponse.json({ detail: "Local demo identity is not configured." }, { status: 503 });
  }
  const { path } = await context.params;
  const relative = path.join("/");
  if (!isAllowedLocalDemoPath(relative)) {
    return NextResponse.json({ detail: "Route is not allowed." }, { status: 404 });
  }
  const url = new URL(`${BACKEND_API_URL}/${relative}`);
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.append(key, value));
  const headers = new Headers({
    authorization: `Bearer ${token(secret)}`,
    "x-cxops-organization-id": organizationId,
    "content-type": request.headers.get("content-type") ?? "application/json",
  });
  try {
    const response = await fetch(url, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
    });
    if (response.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    return new NextResponse(await response.arrayBuffer(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "CXOps backend is unavailable." }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
