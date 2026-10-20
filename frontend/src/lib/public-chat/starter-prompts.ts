/**
 * Safe, tenant-neutral first-message suggestions for the embeddable chatbot.
 *
 * A suggestion fills the editable composer ONLY. It must never send a message,
 * initiate a model call, create a ticket or request human support by itself.
 * No business facts, response-time guarantees, provider IDs, or tenant
 * configuration are embedded here.
 */
export interface ChatStarterPrompt {
  readonly id: "business" | "support";
  readonly label: string;
  readonly draft: string;
}

export const CHAT_STARTER_PROMPTS: readonly ChatStarterPrompt[] = [
  {
    id: "business",
    label: "Ask about the business",
    draft: "What products or services do you offer?",
  },
  {
    id: "support",
    label: "Get help with an issue",
    draft: "I need help with an issue.",
  },
] as const;

/** Never prefill a prompt longer than a tenant's server-enforced message cap. */
export function availableChatStarterPrompts(
  maxMessageLength: number,
): readonly ChatStarterPrompt[] {
  if (!Number.isSafeInteger(maxMessageLength) || maxMessageLength <= 0) {
    return [];
  }
  return CHAT_STARTER_PROMPTS.filter(
    (starter) => starter.draft.length <= maxMessageLength,
  );
}

/**
 * Only show starters before the visitor's first message. A restored session
 * with previous customer messages, a pending reply, a drafted question or a
 * human handoff must not appear to be a fresh AI conversation.
 */
export function shouldShowChatStarters({
  status,
  hasVisitorMessage,
  hasDraft,
  busy,
}: {
  status: string;
  hasVisitorMessage: boolean;
  hasDraft: boolean;
  busy: boolean;
}): boolean {
  return (
    status === "ai_active" &&
    !hasVisitorMessage &&
    !hasDraft &&
    !busy
  );
}
