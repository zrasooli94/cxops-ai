export type StudioConversationMetric = { status: string; needs_response: boolean };
export type StudioHandoffMetric = { status: string };

export function summarizeOwnerStudio(
  conversations: StudioConversationMetric[],
  handoffs: StudioHandoffMetric[],
) {
  return {
    conversations: conversations.length,
    awaitingReply: conversations.filter((item) => item.needs_response).length,
    activeHandoffs: handoffs.filter((item) => item.status !== "closed").length,
    resolved: conversations.filter((item) => ["closed", "resolved", "solved"].includes(item.status)).length,
  };
}
