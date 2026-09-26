// Public web-chat API types (Phase 1P.1).
//
// Mirrors app/schemas/public_chat.py. Message bodies are rendered as plain
// text nodes; no tenant-supplied string is ever injected as HTML.

export interface PublicChatConfigResponse {
  display_name: string;
  welcome_message: string;
  enabled: boolean;
  theme_token: string | null;
  max_message_length: number;
}

export interface PublicChatSessionSummary {
  token: string;
  status: string;
  expires_at: string;
}

export interface PublicChatSessionCreateResponse {
  config: PublicChatConfigResponse;
  session: PublicChatSessionSummary;
}

export interface PublicChatMessageRead {
  id: number;
  direction: string;
  body: string;
  sent_at: string | null;
}

export interface PublicChatStateResponse {
  status: string;
  messages: PublicChatMessageRead[];
  expires_at: string;
}

export interface PublicChatMessageSendResponse {
  message_id: number;
  reply: string | null;
  status: string;
  handoff: boolean;
}

export interface PublicChatHumanRequestResponse {
  status: string;
  reply: string;
}

export interface PublicChatCloseResponse {
  status: string;
}

export type PublicChatSessionStatus =
  | "ai_active"
  | "human_requested"
  | "human_assigned"
  | "closed";