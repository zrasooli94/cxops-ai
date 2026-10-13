import type { Metadata } from "next";

import { InviteClient } from "./invite-client";

export const metadata: Metadata = {
  title: "Accept invitation",
  robots: { index: false, follow: false },
};

// Phase 1Q: invitation acceptance. Deliberately outside the control-center
// layout - an invitee has no membership yet, and that layout would redirect
// them to /no-organization. The token is read from the URL fragment client-
// side; the backend validates it and binds the membership to the signed-in
// identity.
export default function InvitePage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-blue-50 p-6">
      <InviteClient />
    </main>
  );
}
