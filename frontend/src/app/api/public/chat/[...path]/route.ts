import {
  NextRequest,
  NextResponse,
} from "next/server";

// Public web-chat BFF proxy (Phase 1P.1).
//
// Deliberately free of staff auth, cookies, and nhost: the backend resolves the
// tenant from the public widget key and authenticates sessions by their
// end-to-end bearer token. This route only forwards the method/path/query/body
// unchanged and preserves the embedding-origin header so the widget's
// ``document.referrer`` origin reaches the backend allowlist check.

const BACKEND_API_URL =
  process.env.BACKEND_API_URL ??
  "http://127.0.0.1:8000";

const EMBEDDING_ORIGIN_HEADER =
  "X-Embedding-Origin";

async function proxy(
  request: NextRequest,
  context: {
    params: Promise<{
      path: string[];
    }>;
  },
) {
  const { path } = await context.params;

  const backendUrl = new URL(
    `${BACKEND_API_URL}/public/chat/${path.join("/")}`,
  );

  request.nextUrl.searchParams.forEach(
    (value, key) => {
      backendUrl.searchParams.append(key, value);
    },
  );

  const headers = new Headers(request.headers);
  headers.set("accept", "application/json");
  headers.delete("host");

  const embeddingOrigin =
    request.headers.get(EMBEDDING_ORIGIN_HEADER);
  if (embeddingOrigin) {
    headers.set(EMBEDDING_ORIGIN_HEADER, embeddingOrigin);
  }

  let requestBody: ArrayBuffer | undefined;
  if (
    request.method !== "GET" &&
    request.method !== "HEAD"
  ) {
    requestBody = await request.arrayBuffer();
  }

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
    body: requestBody,
  };

  try {
    const response = await fetch(backendUrl, init);

    const contentType =
      response.headers.get("content-type") ??
      "application/json";
    const body = await response.arrayBuffer();

    return new NextResponse(body, {
      status: response.status,
      headers: {
        "content-type": contentType,
        "cache-control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "CXOps backend is unavailable." },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;