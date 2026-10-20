/**
 * Author-only display decisions for the public chat widget.
 *
 * "inbound" means received by CXOps from the visitor: customer messages
 * belong on the right. "outbound" is a support response on the left.
 * This never changes the API's directions, stored messages or auth policy.
 */
export function messagePresentation(direction: string): {
  alignSelf: "flex-start" | "flex-end";
  sender: "You" | "Support";
} {
  return direction === "inbound"
    ? { alignSelf: "flex-end", sender: "You" }
    : { alignSelf: "flex-start", sender: "Support" };
}

export const NEAR_BOTTOM_PX = 80;

/**
 * Only automatically follow new messages if the reader was already close to
 * the end. No snapping a visitor away from older messages while they read.
 *
 * Browser values can briefly be fractional, 0, or invalid during a resize.
 * Treat an invalid geometry as unknown; don't force-scroll in that case.
 */
export function isNearChatBottom(
  scrollHeight: number,
  scrollTop: number,
  clientHeight: number,
): boolean {
  if (
    !Number.isFinite(scrollHeight) ||
    !Number.isFinite(scrollTop) ||
    !Number.isFinite(clientHeight) ||
    scrollHeight < 0 ||
    scrollTop < 0 ||
    clientHeight < 0
  ) {
    return false;
  }
  return scrollHeight - scrollTop - clientHeight <= NEAR_BOTTOM_PX;
}
