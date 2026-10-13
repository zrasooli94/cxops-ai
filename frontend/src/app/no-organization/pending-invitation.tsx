"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PENDING_INVITATION_STORAGE_KEY } from "@/lib/commercial/team";

// Phase 1Q: after signing in from an invitation link, a new member lands here
// (no membership yet). If the invitation token was kept for this tab, offer to
// finish accepting it.
export function PendingInvitationNotice() {
  const [pending, setPending] = useState(false);
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- session storage exists only in the browser; read after mount to keep hydration stable
      setPending(Boolean(window.sessionStorage.getItem(PENDING_INVITATION_STORAGE_KEY)));
    } catch {
      setPending(false);
    }
  }, []);
  if (!pending) {
    return null;
  }
  return (
    <div className="mt-6 rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-900">
      You have an invitation waiting.{" "}
      <Link href="/invite" className="font-semibold underline underline-offset-4">
        Accept it now
      </Link>
    </div>
  );
}
