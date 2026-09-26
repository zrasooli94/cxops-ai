import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  CHAT_SESSION_STORAGE_KEY,
  createEmbeddingOrigin,
  createPublicChatSession,
  EMBEDDING_ORIGIN_HEADER,
  fetchPublicChatConfig,
  fetchPublicChatState,
  PublicChatApiError,
  requestPublicChatHuman,
  sendPublicChatMessage,
} from "./client.ts";
import type {
  PublicChatConfigResponse,
  PublicChatMessageSendResponse,
  PublicChatStateResponse,
} from "./types.ts";

function jsonResponse(
  payload: unknown,
  status = 200,
): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function recordingFetcher(
  calls: Array<{
    url: RequestInfo | URL;
    init: RequestInit | undefined;
  }>,
  response: Response,
) {
  return async (
    input: RequestInfo | URL, init?: RequestInit,
  ): Promise<Response> => {
    calls.push({ url: input, init });
    return response;
  };
}

const CONFIG: PublicChatConfigResponse = {
  display_name: "Acme Support",
  welcome_message: "Welcome to Acme Support.",
  enabled: true,
  theme_token: "default",
  max_message_length: 4000,
};

describe(
  "createEmbeddingOrigin",
  () => {
    it("returns the exact origin of the referrer", () => {
      assert.equal(
        createEmbeddingOrigin(
          "https://shop.acme.example/checkout",
        ),
        "https://shop.acme.example",
      );
    });

    it("returns null for an empty referrer", () => {
      assert.equal(createEmbeddingOrigin(""), null);
    });

    it("returns null for a malformed referrer", () => {
      assert.equal(
        createEmbeddingOrigin("not a url"),
        null,
      );
    });

    it("keeps a non-default port", () => {
      assert.equal(
        createEmbeddingOrigin(
          "http://localhost:5173/",
        ),
        "http://localhost:5173",
      );
    });

    it("disregards path, query, and fragment", () => {
      assert.equal(
        createEmbeddingOrigin(
          "https://a.example/x?y=1#z",
        ),
        "https://a.example",
      );
    });
  },
);

describe(
  "fetchPublicChatConfig",
  () => {
    it("requests config with no-store and parses the response", async () => {
      const calls: Array<{
        url: RequestInfo | URL;
        init: RequestInit | undefined;
      }> = [];
      const fetcher =
        recordingFetcher(
          calls,
          jsonResponse(CONFIG),
        );

      const result =
        await fetchPublicChatConfig(
          "pk_live_test",
          fetcher,
        );

      assert.deepEqual(result, CONFIG);
      assert.equal(
        String(calls[0]?.url),
        "/api/public/chat/config?key=pk_live_test",
      );
      assert.equal(
        calls[0]?.init?.cache,
        "no-store",
      );
    });

    it("throws PublicChatApiError with the detail on 404", async () => {
      const fetcher = recordingFetcher(
        [],
        jsonResponse(
          { detail: "Widget not found" },
          404,
        ),
      );

      await assert.rejects(
        fetchPublicChatConfig(
          "pk_live_missing",
          fetcher,
        ),
        (error) => {
          assert.ok(error instanceof PublicChatApiError);
          assert.equal(error.status, 404);
          assert.equal(
            error.message,
            "Widget not found",
          );
          return true;
        },
      );
    });
  },
);

describe(
  "createPublicChatSession",
  () => {
    it("sends the widget key and embedding-origin header", async () => {
      const calls: Array<{
        url: RequestInfo | URL;
        init: RequestInit | undefined;
      }> = [];
      const fetcher =
        recordingFetcher(
          calls,
          jsonResponse({
            config: CONFIG,
            session: {
              token: "session-token",
              status: "ai_active",
              expires_at: "2026-09-27T00:00:00Z",
            },
          }),
        );

      const result =
        await createPublicChatSession(
          "pk_live_test",
          "https://shop.acme.example",
          fetcher,
        );

      assert.equal(result.session.token, "session-token");
      assert.equal(result.config.display_name, "Acme Support");
      assert.equal(
        String(calls[0]?.url),
        "/api/public/chat/sessions",
      );
      assert.equal(
        calls[0]?.init?.method,
        "POST",
      );
      const body = JSON.parse(
        String(calls[0]?.init?.body),
      ) as { public_widget_key: string };
      assert.equal(
        body.public_widget_key,
        "pk_live_test",
      );
      const headers = new Headers(
        calls[0]?.init?.headers,
      );
      assert.equal(
        headers.get(EMBEDDING_ORIGIN_HEADER),
        "https://shop.acme.example",
      );
    });

    it("omits the embedding-origin header when none is derivable", async () => {
      const calls: Array<{
        url: RequestInfo | URL;
        init: RequestInit | undefined;
      }> = [];
      const fetcher =
        recordingFetcher(
          calls,
          jsonResponse({
            config: CONFIG,
            session: {
              token: "session-token",
              status: "ai_active",
              expires_at: "2026-09-27T00:00:00Z",
            },
          }),
        );

      await createPublicChatSession(
        "pk_live_test",
        null,
        fetcher,
      );

      const headers = new Headers(
        calls[0]?.init?.headers,
      );
      assert.equal(
        headers.get(EMBEDDING_ORIGIN_HEADER),
        null,
      );
    });
  },
);

describe(
  "sendPublicChatMessage",
  () => {
    it("sends bearer token, origin, and body", async () => {
      const calls: Array<{
        url: RequestInfo | URL;
        init: RequestInit | undefined;
      }> = [];
      const sendResult: PublicChatMessageSendResponse = {
        message_id: 7,
        reply: "A support team member needs to review this request.",
        status: "human_requested",
        handoff: true,
      };
      const fetcher =
        recordingFetcher(
          calls,
          jsonResponse(sendResult),
        );

      const result =
        await sendPublicChatMessage(
          "session-token",
          "https://shop.acme.example",
          "msg-1",
          "I need help",
          fetcher,
        );

      assert.deepEqual(result, sendResult);
      assert.equal(
        String(calls[0]?.url),
        "/api/public/chat/messages",
      );
      const headers = new Headers(
        calls[0]?.init?.headers,
      );
      assert.equal(
        headers.get("authorization"),
        "Bearer session-token",
      );
      assert.equal(
        headers.get(EMBEDDING_ORIGIN_HEADER),
        "https://shop.acme.example",
      );
    });
  },
);

describe(
  "fetchPublicChatState",
  () => {
    it("authenticates with the bearer token and parses state", async () => {
      const calls: Array<{
        url: RequestInfo | URL;
        init: RequestInit | undefined;
      }> = [];
      const state: PublicChatStateResponse = {
        status: "ai_active",
        messages: [
          {
            id: 1,
            direction: "outbound",
            body: "Your request has been received by the CXOps team.",
            sent_at: "2026-09-26T12:00:00Z",
          },
        ],
        expires_at: "2026-09-27T00:00:00Z",
      };
      const fetcher =
        recordingFetcher(
          calls,
          jsonResponse(state),
        );

      const result =
        await fetchPublicChatState(
          "session-token",
          fetcher,
        );

      assert.deepEqual(result, state);
      const headers = new Headers(
        calls[0]?.init?.headers,
      );
      assert.equal(
        headers.get("authorization"),
        "Bearer session-token",
      );
      assert.equal(
        headers.get(EMBEDDING_ORIGIN_HEADER),
        null,
      );
    });
  },
);

describe(
  "requestPublicChatHuman",
  () => {
    it("posts and returns the new status and reply", async () => {
      const calls: Array<{
        url: RequestInfo | URL;
        init: RequestInit | undefined;
      }> = [];
      const fetcher =
        recordingFetcher(
          calls,
          jsonResponse({
            status: "human_requested",
            reply: "A support team member needs to review this request.",
          }),
        );

      const result =
        await requestPublicChatHuman(
          "session-token",
          fetcher,
        );

      assert.equal(
        result.status,
        "human_requested",
      );
      assert.equal(
        String(calls[0]?.url),
        "/api/public/chat/sessions/human",
      );
      assert.equal(
        calls[0]?.init?.method,
        "POST",
      );
    });
  },
);

describe(
  "constants",
  () => {
    it("exposes a storage key for widget session persistence", () => {
      assert.equal(
        CHAT_SESSION_STORAGE_KEY,
        "cxops.chat.session.v1",
      );
    });
  },
);