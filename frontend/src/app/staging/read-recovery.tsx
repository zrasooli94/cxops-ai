"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  stagingRetryState, STAGING_RETRY_DELAYS_MS,
  type StagingReadIssue,
} from "@/lib/staging/read-recovery";

/** No destination, token, or tenant input. Retried requests reuse the
 * server-only authenticated path; at most three in a four-minute tab window.
 */
export function StagingReadRecovery({ issue }: { issue: StagingReadIssue }) {
  const router = useRouter();
  const pathname = usePathname() || "/staging";
  const [refreshIteration, setRefreshIteration] = useState(0);

  useEffect(() => {
    if (issue !== "warming") return;
    const key = "cxops-staging-read-retry:" + pathname;
    let stored: unknown = null;
    try {
      const raw = window.sessionStorage.getItem(key);
      stored = raw ? JSON.parse(raw) as unknown : null;
    } catch { return; } // No automatic retry if storage is unavailable.
    const snapshot = stagingRetryState(stored, Date.now());
    if (snapshot.attempts >= STAGING_RETRY_DELAYS_MS.length) return;
    const timer = window.setTimeout(() => {
      try {
        window.sessionStorage.setItem(key, JSON.stringify({
          attempts: snapshot.attempts + 1,
          startedAt: snapshot.startedAt,
        }));
      } catch { return; }
      setRefreshIteration(count => count + 1);
      router.refresh();
    }, STAGING_RETRY_DELAYS_MS[snapshot.attempts]);
    return () => window.clearTimeout(timer);
  }, [issue, pathname, router, refreshIteration]);

  return <div role="status" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
    {issue === "warming" ? <>
      <p className="font-semibold">Staging API may be waking up</p>
      <p className="mt-1">The Free server sleeps when idle. This page will make up to three
        read-only retries while open. No account, ticket or customer data is changed.</p>
    </> : issue === "denied" ? <>
      <p className="font-semibold">Staging authorization was refused</p>
      <p className="mt-1">Your identity was recognized, but the staging backend did not authorize
        this request. No workspace data was displayed or changed.</p>
    </> : <>
      <p className="font-semibold">Staging data is temporarily unavailable</p>
      <p className="mt-1">The backend response could not be validated. No workspace data was displayed.</p>
    </>}
    {issue !== "denied" && <button type="button" onClick={() => router.refresh()}
      className="mt-3 rounded-lg border border-amber-400 bg-white px-4 py-2 font-semibold text-amber-950 hover:bg-amber-100">
      Retry workspace now
    </button>}
  </div>;
}
