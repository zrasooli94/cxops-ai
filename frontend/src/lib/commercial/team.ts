// Team roles and invitation links (Phase 1Q).
//
// Role names are display labels only. Which roles an actor may assign comes
// from the backend (`assignable_roles`); this module never decides permissions.

export const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  admin: "Administrator",
  supervisor: "Supervisor",
  agent: "Support agent",
  viewer: "Viewer",
};

export const ROLE_DESCRIPTIONS: Record<string, string> = {
  owner: "Full control of the workspace, including ownership and team access.",
  admin: "Manages settings, knowledge and most team members.",
  supervisor: "Runs day-to-day support and reviews AI actions.",
  agent: "Answers conversations and works the handoff queue.",
  viewer: "Read-only access to conversations and reports.",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/**
 * The accept link. The token travels in the URL fragment, which browsers never
 * send to a server, so it cannot land in request logs or a Referer header.
 */
export function invitationLink(appOrigin: string, token: string): string {
  return `${appOrigin.replace(/\/+$/, "")}/invite#token=${encodeURIComponent(token)}`;
}

/** Read a token from `location.hash` ("#token=..."); null if absent/malformed. */
export function parseInvitationHash(hash: string): string | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const params = new URLSearchParams(raw);
  const token = params.get("token");
  if (!token || token.length < 10 || token.length > 200 || !token.startsWith("inv_")) {
    return null;
  }
  return token;
}

export type InvitationStatus = "pending" | "expired" | "accepted" | "revoked";

export const INVITATION_STATUS_LABELS: Record<InvitationStatus, string> = {
  pending: "Pending",
  expired: "Expired",
  accepted: "Accepted",
  revoked: "Revoked",
};

export const PENDING_INVITATION_STORAGE_KEY = "cxops.pendingInvitation";
