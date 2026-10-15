import type { PublicChatPilotSummary } from "../public-chat/types.ts";

export type HandoffOverview = {
  waiting: number;
  assigned: number;
  oldestWaitingMinutes: number | null;
  needsAttention: boolean;
};

export function toHandoffOverview(summary: PublicChatPilotSummary): HandoffOverview {
  const { human_requested, human_assigned } = summary.queue;
  return {
    waiting: human_requested,
    assigned: human_assigned,
    oldestWaitingMinutes: summary.queue_health.oldest_waiting_minutes,
    needsAttention: summary.queue_health.health === "attention",
  };
}
