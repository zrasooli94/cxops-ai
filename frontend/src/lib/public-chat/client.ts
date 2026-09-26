import type {
  PublicChatConfigResponse,
  PublicChatSessionCreateResponse,
  PublicChatMessageSendResponse,
  PublicChatStateResponse,
  PublicChatHumanRequestResponse,
  PublicChatCloseResponse,
} from "./types";

// Public web-chat client (Phase 1P.1).
//
// Talks to the same-origin public BFF proxy at /api/public/chat/[...path],
// which forwards to the backend. ``embeddingOrigin`` is derived by the widget
// from ``document.referrer`` so the frontend never needs a browser session.

export const EMBEDDING_ORIGIN_HEADER =
  "X-Embedding-Origin";

export const CHAT_SESSION_STORAGE_KEY =
  "cxops.chat.session.v1";

export interface PublicChatApiErrorBody {
  detail: string;
}

export class PublicChatApiError
  extends Error
{
  readonly status: number;

  constructor(
    status: number,
    detail: string,
  ) {
    super(detail);
    this.name = "PublicChatApiError";
    this.status = status;
  }
}

export function createEmbeddingOrigin(
  referrer: string,
): string | null {
  if (!referrer) {
    return null;
  }
  try {
    return new URL(referrer).origin;
  } catch {
    return null;
  }
}

async function readFailure(response: Response): Promise<never> {
  let detail =
    "An unknown error occurred.";

  try {
    const body: unknown =
      await response.json();
    if (
      body !== null &&
      typeof body === "object" &&
      "detail" in body &&
      typeof (body as PublicChatApiErrorBody).detail ===
        "string"
    ) {
      detail = (body as PublicChatApiErrorBody).detail;
    }
  } catch {
    // Non-JSON error bodies fall back to the default message.
  }

  throw new PublicChatApiError(
    response.status,
    detail,
  );
}

async function parseJson<T>(
  response: Response,
): Promise<T> {
  if (!response.ok) {
    await readFailure(response);
  }
  return (await response.json()) as T;
}

export async function fetchPublicChatConfig(
  widgetKey: string,
  fetcher: typeof fetch = fetch,
): Promise<PublicChatConfigResponse> {
  const url =
    `/api/public/chat/config?key=${encodeURIComponent(widgetKey)}`;
  const response = await fetcher(url, {
    cache: "no-store",
  });
  return parseJson<PublicChatConfigResponse>(response);
}

export async function createPublicChatSession(
  widgetKey: string,
  embeddingOrigin: string | null,
  fetcher: typeof fetch = fetch,
): Promise<PublicChatSessionCreateResponse> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
  };
  if (embeddingOrigin) {
    headers[EMBEDDING_ORIGIN_HEADER] =
      embeddingOrigin;
  }

  const response = await fetcher(
    "/api/public/chat/sessions",
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        public_widget_key: widgetKey,
      }),
      cache: "no-store",
    },
  );
  return parseJson<PublicChatSessionCreateResponse>(response);
}

function sessionHeaders(
  sessionToken: string,
  embeddingOrigin: string | null,
): Record<string, string> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    authorization: `Bearer ${sessionToken}`,
  };
  if (embeddingOrigin) {
    headers[EMBEDDING_ORIGIN_HEADER] =
      embeddingOrigin;
  }
  return headers;
}

export async function sendPublicChatMessage(
  sessionToken: string,
  embeddingOrigin: string | null,
  clientMessageId: string,
  text: string,
  fetcher: typeof fetch = fetch,
): Promise<PublicChatMessageSendResponse> {
  const response = await fetcher(
    "/api/public/chat/messages",
    {
      method: "POST",
      headers: sessionHeaders(
        sessionToken,
        embeddingOrigin,
      ),
      body: JSON.stringify({
        client_message_id: clientMessageId,
        text,
      }),
      cache: "no-store",
    },
  );
  return parseJson<PublicChatMessageSendResponse>(response);
}

export async function fetchPublicChatState(
  sessionToken: string,
  fetcher: typeof fetch = fetch,
): Promise<PublicChatStateResponse> {
  const response = await fetcher(
    "/api/public/chat/sessions/state",
    {
      headers: sessionHeaders(
        sessionToken,
        null,
      ),
      cache: "no-store",
    },
  );
  return parseJson<PublicChatStateResponse>(response);
}

export async function requestPublicChatHuman(
  sessionToken: string,
  fetcher: typeof fetch = fetch,
): Promise<PublicChatHumanRequestResponse> {
  const response = await fetcher(
    "/api/public/chat/sessions/human",
    {
      method: "POST",
      headers: sessionHeaders(
        sessionToken,
        null,
      ),
      cache: "no-store",
    },
  );
  return parseJson<PublicChatHumanRequestResponse>(response);
}

export async function closePublicChatSession(
  sessionToken: string,
  fetcher: typeof fetch = fetch,
): Promise<PublicChatCloseResponse> {
  const response = await fetcher(
    "/api/public/chat/sessions/close",
    {
      method: "POST",
      headers: sessionHeaders(
        sessionToken,
        null,
      ),
      cache: "no-store",
    },
  );
  return parseJson<PublicChatCloseResponse>(response);
}