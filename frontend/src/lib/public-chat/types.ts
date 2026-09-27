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

// Staff-facing handoff API types (Phase 1P.2).
//
// Mirrors app/schemas/staff_public_chat.py. Consumed through the authenticated
// control-center proxy at /api/backend/staff/public-chat/... — never through
// the public widget routes.

export interface StaffHandoffSession {
  session_id: number;
  conversation_id: number;
  ticket_id: number;
  customer_id: number | null;
  status: PublicChatSessionStatus;
  assigned_to_subject: string | null;
  assigned_at: string | null;
  released_at: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
}

export interface StaffHandoffSessionsResponse {
  sessions: StaffHandoffSession[];
}

export interface StaffBusinessAction {
  id: number;
  request_type: string;
  status: string;
  reference_id: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface StaffBusinessActionsResponse {
  actions: StaffBusinessAction[];
}