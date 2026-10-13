import { PublicChatApiError } from "./client.ts";

// Ambiguous-failure retries for widget sends (Phase 1P.9R).
//
// A send can fail *after* the server stored the message (the response was
// lost to a network drop, a proxy timeout, a deploy). Retrying with a NEW
// client_message_id would store the message twice; giving up silently would
// lose it from the visitor's view. The backend de-duplicates by
// client_message_id, so the widget retries ambiguous failures with the SAME
// id, and remembers it so a manual resend of the same text reuses it too.

export const SEND_RETRY_DELAYS_MS: readonly number[] = [1_000, 2_000, 4_000];

/** True when the message may or may not have been stored, or the server
 * asked us to come back: retrying with the same id is safe and useful. */
export function isRetryableSendFailure(error: unknown): boolean {
  if (error instanceof PublicChatApiError) {
    return (
      error.status >= 500 ||
      error.status === 408 ||
      error.status === 409 || // still being processed
      error.status === 429 // burst limiter: "try again in a moment"
    );
  }
  // fetch() rejects with a TypeError on network failure / abort.
  return true;
}

export async function sendWithRetry<T>(
  attempt: () => Promise<T>,
  options: {
    delaysMs?: readonly number[];
    sleep?: (ms: number) => Promise<void>;
  } = {},
): Promise<T> {
  const delays = options.delaysMs ?? SEND_RETRY_DELAYS_MS;
  const sleep =
    options.sleep ??
    ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  for (let index = 0; ; index += 1) {
    try {
      return await attempt();
    } catch (error) {
      if (index >= delays.length || !isRetryableSendFailure(error)) {
        throw error;
      }
      await sleep(delays[index]);
    }
  }
}

export interface PendingSend {
  text: string;
  clientMessageId: string;
}

/** Reuse the id of a failed send when the visitor sends the same text again,
 * so a message the server did store is not stored twice. */
export function clientMessageIdForSend(
  text: string,
  pending: PendingSend | null,
  newId: () => string,
): string {
  return pending !== null && pending.text === text
    ? pending.clientMessageId
    : newId();
}
