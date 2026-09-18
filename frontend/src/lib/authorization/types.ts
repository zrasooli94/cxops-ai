/**
 * Frontend authorization data.
 *
 * This mirrors the backend `GET /me/authorization` response. The FastAPI backend
 * is the ONLY authority: this data is used exclusively for UX affordances
 * (showing, hiding, disabling, or explaining controls) and is never a security
 * boundary by itself.
 *
 * Capabilities are treated as opaque strings exactly as returned. The frontend
 * never derives them from role names, JWT claims, client-side organization
 * selection, or model/tool-plan output.
 */
export interface AuthorizationInfo {
  readonly organization_id: number;
  readonly role: string;
  readonly capabilities: readonly string[];
}
