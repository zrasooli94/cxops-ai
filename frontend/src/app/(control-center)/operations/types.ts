export type SLAState = "not_configured" | "on_track" | "due_soon" | "breached" | "met";

export type QueueSummary = {
  id: number;
  name: string;
  key: string;
  count: number;
};

export type OperationsSummary = {
  open: number;
  unassigned: number;
  needs_response: number;
  response_breaches: number;
  resolution_breaches: number;
  due_soon: number;
  urgent: number;
  high: number;
  by_queue: QueueSummary[];
};

export type AIRoutingSuggestion = {
  queue_id: number;
  queue_key: string;
  queue_name: string;
};

export type OperationsQueueItem = {
  ticket_id: number;
  conversation_id: number | null;
  subject: string;
  status: string;
  priority: string;
  category: string | null;
  service_queue_id: number | null;
  service_queue_name: string | null;
  assigned_subject: string | null;
  is_assigned_to_me: boolean;
  created_at: string;
  updated_at: string;
  needs_response: boolean;
  first_response_due_at: string | null;
  first_response_at: string | null;
  first_response_sla_state: SLAState;
  resolution_due_at: string | null;
  resolved_at: string | null;
  resolution_sla_state: SLAState;
  overall_sla_state: SLAState;
  routing_source: string | null;
  ai_routing_suggestion: AIRoutingSuggestion | null;
};

export type ServiceQueue = {
  id: number;
  key: string;
  name: string;
  active: boolean;
  is_default: boolean;
  sla_policy_id: number | null;
};

export type SLAPolicy = {
  id: number;
  name: string;
  enabled: boolean;
  is_default: boolean;
  first_response_low_minutes: number;
  first_response_normal_minutes: number;
  first_response_high_minutes: number;
  first_response_urgent_minutes: number;
  resolution_low_minutes: number;
  resolution_normal_minutes: number;
  resolution_high_minutes: number;
  resolution_urgent_minutes: number;
};
