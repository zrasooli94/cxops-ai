// Wire types for /staff/tenant-config (Phase 1P.3).
//
// The widget key is deliberately absent from every type here. The backend
// returns a raw key only in the immediate response to a rotation, and it is
// never included in a settings read, so the UI has nowhere to persist it by
// accident. `hasWidgetKey` is the only signal that a key exists.

export interface PublicChatSettings {
  id: number;
  displayName: string;
  welcomeMessage: string;
  allowedOrigins: string[];
  themeToken: string | null;
  enabled: boolean;
  maxMessageLength: number;
  maxMessagesPerMinute: number;
  sessionTtlHours: number;
  hasWidgetKey: boolean;
  widgetKeyStatus: string;
  updatedAt: string | null;
}

export interface WidgetKeyRotation {
  publicWidgetKey: string;
  warning: string;
}

export interface BusinessIntegrationState {
  provider: string;
  enabled: boolean;
  providerMode: string | null;
  toolNames: string[];
}

export interface BusinessIntegrationsResponse {
  integrations: BusinessIntegrationState[];
}
