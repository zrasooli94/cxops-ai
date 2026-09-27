import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  fetchBusinessIntegrations,
  fetchPublicChatSettings,
  PublicChatApiError,
  rotateWidgetKey,
  setBusinessIntegration,
  updatePublicChatSettings,
} from "./client.ts";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type Call = { url: string; init: RequestInit | undefined };

function recordingFetcher(calls: Call[], response: Response) {
  return async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    calls.push({ url: String(input), init });
    return response;
  };
}

const WIRE_SETTINGS = {
  id: 7,
  display_name: "Acme Support",
  welcome_message: "Hello",
  allowed_origins: ["https://www.example.com"],
  theme_token: null,
  enabled: true,
  max_message_length: 4000,
  max_messages_per_minute: 20,
  session_ttl_hours: 24,
  has_widget_key: true,
  widget_key_status: "key unavailable; rotate to generate a new one",
  updated_at: "2026-01-01T00:00:00+00:00",
};

describe("fetchPublicChatSettings", () => {
  it("reads through the authenticated control-center proxy", async () => {
    const calls: Call[] = [];
    const settings = await fetchPublicChatSettings(
      recordingFetcher(calls, jsonResponse(WIRE_SETTINGS)),
    );

    assert.equal(
      calls[0]?.url,
      "/api/backend/staff/tenant-config/public-chat",
    );
    assert.equal(calls[0]?.init?.method, undefined);
    assert.equal(settings.displayName, "Acme Support");
    assert.deepEqual(settings.allowedOrigins, ["https://www.example.com"]);
    assert.equal(settings.hasWidgetKey, true);
  });

  it("never hands the caller a widget key or a digest", async () => {
    const settings = await fetchPublicChatSettings(
      recordingFetcher([], jsonResponse(WIRE_SETTINGS)),
    );

    assert.equal("publicWidgetKey" in settings, false);
    assert.equal("publicWidgetKeyHash" in settings, false);
  });
});

describe("updatePublicChatSettings", () => {
  it("translates camelCase input to the snake_case wire contract", async () => {
    const calls: Call[] = [];
    await updatePublicChatSettings(
      {
        displayName: "Acme Concierge",
        welcomeMessage: "Ask us anything",
        allowedOrigins: ["https://www.example.com"],
        themeToken: "brand-teal",
      },
      recordingFetcher(calls, jsonResponse(WIRE_SETTINGS)),
    );

    assert.equal(calls[0]?.init?.method, "PATCH");
    assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
      display_name: "Acme Concierge",
      welcome_message: "Ask us anything",
      allowed_origins: ["https://www.example.com"],
      theme_token: "brand-teal",
    });
  });

  it("omits an untouched theme token rather than clearing it", async () => {
    const calls: Call[] = [];
    await updatePublicChatSettings(
      { enabled: false },
      recordingFetcher(calls, jsonResponse(WIRE_SETTINGS)),
    );

    const body = JSON.parse(String(calls[0]?.init?.body)) as Record<
      string,
      unknown
    >;
    // JSON.stringify drops undefined, so an unset theme token is absent from the
    // request and the stored value is left alone.
    assert.equal("theme_token" in body, false);
    assert.equal(body.enabled, false);
  });

  it("sends an explicit null to clear a stored theme token", async () => {
    const calls: Call[] = [];
    await updatePublicChatSettings(
      { themeToken: null },
      recordingFetcher(calls, jsonResponse(WIRE_SETTINGS)),
    );

    const body = JSON.parse(String(calls[0]?.init?.body)) as Record<
      string,
      unknown
    >;
    // null is the wire value that removes the token. Sending undefined would be
    // dropped by JSON.stringify and leave the old token in place, so the
    // operator could never remove it from the UI.
    assert.equal(body.theme_token, null);
    assert.notEqual("theme_token" in body, false);
  });

  it("surfaces a FastAPI validation message", async () => {
    await assert.rejects(
      updatePublicChatSettings(
        { allowedOrigins: ["https://www.example.com/chat"] },
        recordingFetcher(
          [],
          jsonResponse(
            [{ loc: ["body", "allowed_origins"], msg: "must not include a path" }],
            422,
          ),
        ),
      ),
      (error: unknown) => {
        assert.ok(error instanceof PublicChatApiError);
        assert.equal(error.status, 422);
        assert.match(error.message, /must not include a path/);
        return true;
      },
    );
  });
});

describe("rotateWidgetKey", () => {
  it("returns the one-time raw key and a warning", async () => {
    const calls: Call[] = [];
    // A placeholder, not a realistic pk_live_ value: this asserts pass-through,
    // and a real-looking credential literal in a test fixture is what a secret
    // scanner is supposed to flag. Real key format is covered by the backend
    // onboarding tests, which mint one through the real path.
    const rotation = await rotateWidgetKey(
      recordingFetcher(
        calls,
        jsonResponse({
          public_widget_key: "one-time-key-placeholder",
          warning: "shown once",
        }),
      ),
    );

    assert.equal(
      calls[0]?.url,
      "/api/backend/staff/tenant-config/public-chat/rotate-widget-key",
    );
    assert.equal(calls[0]?.init?.method, "POST");
    assert.equal(rotation.publicWidgetKey, "one-time-key-placeholder");
    assert.equal(rotation.warning, "shown once");
  });
});

describe("business integrations", () => {
  it("maps the integrations envelope", async () => {
    const integrations = await fetchBusinessIntegrations(
      recordingFetcher(
        [],
        jsonResponse({
          integrations: [
            {
              provider: "a1_cash_for_cars",
              enabled: true,
              provider_mode: "local_demo",
              tool_names: ["a1.get_quote_status"],
            },
          ],
        }),
      ),
    );

    assert.equal(integrations.length, 1);
    assert.equal(integrations[0]?.provider, "a1_cash_for_cars");
    assert.equal(integrations[0]?.providerMode, "local_demo");
    assert.deepEqual(integrations[0]?.toolNames, ["a1.get_quote_status"]);
  });

  it("url-encodes the provider and posts the enabled flag", async () => {
    const calls: Call[] = [];
    await setBusinessIntegration(
      "a1_cash_for_cars",
      false,
      recordingFetcher(calls, jsonResponse({ provider: "a1_cash_for_cars", enabled: false })),
    );

    assert.equal(
      calls[0]?.url,
      "/api/backend/staff/tenant-config/business-integrations/a1_cash_for_cars",
    );
    assert.equal(calls[0]?.init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), {
      enabled: false,
    });
  });

  it("encodes a provider name that needs escaping", async () => {
    const calls: Call[] = [];
    await setBusinessIntegration(
      "weird provider/../admin",
      true,
      recordingFetcher(calls, jsonResponse({ provider: "x", enabled: true })),
    );

    assert.ok(calls[0]?.url.includes("weird%20provider%2F..%2Fadmin"));
  });
});
