// Customer-facing copy for a conversation that has been handed to people.
//
// Tenant-neutral on purpose: no company name, no staffing claim, and never a
// response-time promise. The platform has a staff handoff queue but no
// notification channel, so the widget must not imply that anyone is watching
// in real time or that an answer will arrive by a given time.

export type HandoffState = "human_requested" | "human_assigned";

export interface HandoffCopy {
  chip: string;
  explanation: string;
  placeholder: string;
  newConversationLabel: string;
  closeWarning: string;
  confirmLabel: string;
  keepLabel: string;
}

// Closing the old session marks its ticket solved, so the team stops seeing it
// as waiting. The customer must be told that before they choose it.
const CLOSE_WARNING =
  "Starting a new conversation closes this one, so the team will no " +
  "longer see it as waiting for review. Your messages stay on record.";

const COPY: Record<HandoffState, HandoffCopy> = {
  human_requested: {
    chip: "Sent for human review",
    explanation:
      "Your conversation was sent to the team for review. The assistant " +
      "will not answer further messages in this conversation, and we can't " +
      "promise when someone will reply. You can add details below, or " +
      "start a new conversation to ask the assistant something else.",
    placeholder: "Add details for the team…",
    newConversationLabel: "Start a new conversation",
    closeWarning: CLOSE_WARNING,
    confirmLabel: "Close it and start new",
    keepLabel: "Keep this conversation",
  },
  human_assigned: {
    chip: "An agent is reviewing this conversation",
    explanation:
      "A team member has picked up this conversation. Messages you send " +
      "here go to them. You can also start a new conversation to ask the " +
      "assistant something else.",
    placeholder: "Message the team…",
    newConversationLabel: "Start a new conversation",
    closeWarning: CLOSE_WARNING,
    confirmLabel: "Close it and start new",
    keepLabel: "Keep this conversation",
  },
};

export function isHandoffState(value: string): value is HandoffState {
  return value === "human_requested" || value === "human_assigned";
}

export function handoffCopy(state: HandoffState): HandoffCopy {
  return COPY[state];
}

// The whole customer-visible vocabulary, for tests that guard against
// unsupported promises (time, staffing availability, outcomes).
export function allHandoffText(): string[] {
  return Object.values(COPY).flatMap((copy) => Object.values(copy));
}
