import type {
  BusinessIntegrationsResponse,
  PublicChatSettings,
  WidgetKeyRotation,
} from "./types.ts";

import { PublicChatApiError } from "../public-chat/client.ts";

// Re-exported so callers import every error they must handle from this module
// rather than reaching into the public-chat client for a shared class.
export { PublicChatApiError };

// Staff tenant-configuration client (Phase 1P.3).
//
// Reached exclusively through the authenticated control-center proxy at
// /api/backend/... , which forwards the session and the active-organization
// selection. The backend is the authorization boundary: these routes are
// capability-gated on integration.manage and tenant-scoped, so no capability or
// tenant identifier is asserted from the browser.
//
// The backend speaks snake_case on this surface; the mapping to the camelCase
// UI types happens here so no component has to remember the wire format.

type WireSettings = {
  id: number;
  display_name: string;
  welcome_message: string;
  allowed_origins: string[];
  theme_token: string | null;
  enabled: boolean;
  max_message_length: number;
  max_messages_per_minute: number;
  session_ttl_hours: number;
  has_widget_key: boolean;
  widget_key_status: string;
  updated_at: string | null;
};

type WireIntegration = {
  provider: string;
  enabled: boolean;
  provider_mode: string | null;
  tool_names: string[];
};

function toSettings(wire: WireSettings): PublicChatSettings {
  return {
    id: wire.id,
    displayName: wire.display_name,
    welcomeMessage: wire.welcome_message,
    allowedOrigins: wire.allowed_origins,
    themeToken: wire.theme_token,
    enabled: wire.enabled,
    maxMessageLength: wire.max_message_length,
    maxMessagesPerMinute: wire.max_messages_per_minute,
    sessionTtlHours: wire.session_ttl_hours,
    hasWidgetKey: wire.has_widget_key,
    widgetKeyStatus: wire.widget_key_status,
    updatedAt: wire.updated_at,
  };
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = "An unknown error occurred.";
    try {
      const body: unknown = await response.json();
      if (
        body !== null &&
        typeof body === "object" &&
        "detail" in body &&
        typeof (body as { detail: unknown }).detail === "string"
      ) {
        detail = (body as { detail: string }).detail;
      } else if (Array.isArray(body)) {
        // FastAPI validation errors: surface the first field message so the
        // operator learns *which* input was rejected.
        const first = body[0] as { msg?: unknown } | undefined;
        if (first && typeof first.msg === "string") {
          detail = first.msg;
        }
      }
    } catch {
      // Non-JSON error bodies fall back to the default message.
    }
    throw new PublicChatApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export async function fetchPublicChatSettings(
  fetcher: typeof fetch = fetch,
): Promise<PublicChatSettings> {
  const response = await fetcher(
    "/api/backend/staff/tenant-config/public-chat",
    { cache: "no-store" },
  );
  return toSettings(await parseJson<WireSettings>(response));
}

export interface PublicChatSettingsUpdate {
  displayName?: string;
  welcomeMessage?: string;
  allowedOrigins?: string[];
  themeToken?: string | null;
  enabled?: boolean;
}

export async function updatePublicChatSettings(
  update: PublicChatSettingsUpdate,
  fetcher: typeof fetch = fetch,
): Promise<PublicChatSettings> {
  const response = await fetcher(
    "/api/backend/staff/tenant-config/public-chat",
    {
      method: "PATCH",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: update.displayName,
        welcome_message: update.welcomeMessage,
        allowed_origins: update.allowedOrigins,
        theme_token: update.themeToken,
        enabled: update.enabled,
      }),
    },
  );
  return toSettings(await parseJson<WireSettings>(response));
}

/** The returned raw key is shown once and is not stored anywhere. */
export async function rotateWidgetKey(
  fetcher: typeof fetch = fetch,
): Promise<WidgetKeyRotation> {
  const response = await fetcher(
    "/api/backend/staff/tenant-config/public-chat/rotate-widget-key",
    { method: "POST", cache: "no-store" },
  );
  const wire = await parseJson<{
    public_widget_key: string;
    warning: string;
  }>(response);
  return {
    publicWidgetKey: wire.public_widget_key,
    warning: wire.warning,
  };
}

export async function fetchBusinessIntegrations(
  fetcher: typeof fetch = fetch,
): Promise<BusinessIntegrationsResponse["integrations"]> {
  const response = await fetcher(
    "/api/backend/staff/tenant-config/business-integrations",
    { cache: "no-store" },
  );
  const wire = await parseJson<{ integrations: WireIntegration[] }>(response);
  return wire.integrations.map((entry) => ({
    provider: entry.provider,
    enabled: entry.enabled,
    providerMode: entry.provider_mode,
    toolNames: entry.tool_names,
  }));
}

export async function setBusinessIntegration(
  provider: string,
  enabled: boolean,
  fetcher: typeof fetch = fetch,
): Promise<{ provider: string; enabled: boolean }> {
  const response = await fetcher(
    `/api/backend/staff/tenant-config/business-integrations/${encodeURIComponent(provider)}`,
    {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  return parseJson<{ provider: string; enabled: boolean }>(response);
}
