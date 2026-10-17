/** Conservative read-only recovery for Render Free cold starts.
 * This module never stores, forwards, or inspects a session or access token.
 */
export type StagingReadIssue = "warming" | "denied" | "unavailable";
export const STAGING_RETRY_DELAYS_MS = [14000, 22000, 30000] as const;
export const STAGING_RETRY_WINDOW_MS = 4 * 60 * 1000;

export function stagingReadIssue(status: number | null, error?: unknown): StagingReadIssue {
  // Never silently retry an explicit authorization denial.
  if (status === 401 || status === 403) return "denied";
  // Render cold-start HTTP errors are retried only while this page is open.
  if (status === 502 || status === 503 || status === 504) return "warming";
  if (status !== null) return "unavailable";

  if (typeof error === "object" && error !== null && "name" in error) {
    const name = (error as { name: unknown }).name;
    if (name === "TimeoutError" || name === "AbortError" || name === "TypeError") {
      return "warming";
    }
  }
  return "unavailable";
}

export type StagingRetryState = { attempts: number; startedAt: number };
export function stagingRetryState(value: unknown, now: number): StagingRetryState {
  const fresh = { attempts: 0, startedAt: now };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fresh;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.startedAt !== "number" ||
      !Number.isFinite(candidate.startedAt) ||
      now < candidate.startedAt ||
      now - candidate.startedAt > STAGING_RETRY_WINDOW_MS ||
      typeof candidate.attempts !== "number" ||
      !Number.isInteger(candidate.attempts) ||
      candidate.attempts < 0) return fresh;
  return {
    startedAt: candidate.startedAt,
    attempts: Math.min(candidate.attempts, STAGING_RETRY_DELAYS_MS.length),
  };
}
