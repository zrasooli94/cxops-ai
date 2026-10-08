export type ReplyResult = {
  delivery_status?:
    | "queued"
    | "sending"
    | "retrying"
    | "sent"
    | "failed"
    | null;
  duplicate?: boolean;
};

export const REPLY_SUCCESS_SENT = "Reply sent.";
export const REPLY_SUCCESS_QUEUED = "Reply queued for delivery.";
export const REPLY_DUPLICATE = "This reply was already submitted.";

export function replySuccessMessage(result: ReplyResult): string {
  if (result.duplicate) {
    return REPLY_DUPLICATE;
  }
  return result.delivery_status === "sent"
    ? REPLY_SUCCESS_SENT
    : REPLY_SUCCESS_QUEUED;
}