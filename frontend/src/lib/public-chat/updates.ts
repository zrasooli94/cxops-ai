import type { PublicChatMessageRead } from "./types";

// Incremental public-chat updates (Phase 1P.9).
//
// The widget sends with ``delivery: "async"``: the backend acknowledges the
// message as soon as it is stored and the AI reply arrives through the cursor
// endpoint ``GET /sessions/updates?after_id=``. The server returns a
// ``poll_after_ms`` hint (fast while a turn is pending, slow when idle), so the
// widget never polls faster than the server asks and needs no long-lived
// connection.

// Fallback when an older backend without /sessions/updates is deployed.
export const LEGACY_POLL_INTERVAL_MS = 20_000;
const MIN_POLL_MS = 1_000;
const MAX_POLL_MS = 20_000;
// A hidden tab keeps the conversation fresh at a gentle pace.
const HIDDEN_POLL_MS = 30_000;

export function nextPollDelay(
  hintMs: number | null | undefined,
  hidden: boolean,
): number {
  const hint =
    typeof hintMs === "number" && Number.isFinite(hintMs)
      ? Math.min(MAX_POLL_MS, Math.max(MIN_POLL_MS, hintMs))
      : LEGACY_POLL_INTERVAL_MS;
  return hidden ? Math.max(hint, HIDDEN_POLL_MS) : hint;
}

/** Highest persisted (positive) message id, the cursor for the next poll. */
export function cursorOf(
  messages: readonly PublicChatMessageRead[],
): number {
  return messages.reduce(
    (max, message) => (message.id > max ? message.id : max),
    0,
  );
}

// The locally rendered welcome text; it is never stored server-side.
export const WELCOME_PLACEHOLDER_ID = -1;

/**
 * Append messages the widget has not seen. Local placeholders (negative ids:
 * optimistic inbound messages, replies shown straight from the send response)
 * are replaced once the stored copy with the same direction and text arrives,
 * so nothing is shown twice. The welcome placeholder is always kept.
 */
export function mergeMessages(
  previous: readonly PublicChatMessageRead[],
  incoming: readonly PublicChatMessageRead[],
): PublicChatMessageRead[] {
  const known = new Set(
    previous.filter((m) => m.id > 0).map((m) => m.id),
  );
  const fresh = incoming.filter((m) => !known.has(m.id));
  if (fresh.length === 0) {
    return [...previous];
  }
  const stored = fresh.map((m) => `${m.direction}\u0000${m.body}`);
  const kept = previous.filter((m) => {
    if (m.id > 0 || m.id === WELCOME_PLACEHOLDER_ID) {
      return true;
    }
    const index = stored.indexOf(`${m.direction}\u0000${m.body}`);
    if (index === -1) {
      return true;
    }
    stored.splice(index, 1);
    return false;
  });
  return [...kept, ...fresh];
}
