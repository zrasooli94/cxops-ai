/**
 * Customer 360 screen shape derived from backend-provided capabilities only.
 *
 * This module is intentionally pure (no React, no fetch, no policy matrix) so it
 * can be unit-tested with the Node built-in runner. It never maps a role to a
 * permission: every flag comes from the capability predicate the backend
 * authorization context supplies. FastAPI remains the sole authority.
 */
import {
  CAPABILITIES,
  type Capability,
} from "../authorization/capabilities.ts";

export interface CustomerExperience {
  /** The subject may browse the customer list and detail views. */
  readonly canRead: boolean;
  /** The subject may create/update customer records. */
  readonly canWrite: boolean;
  /** The subject may see the linked-tickets section. */
  readonly canViewTickets: boolean;
  /** The subject may see agent-activity entries on the timeline. */
  readonly canViewAgentActivity: boolean;
  /** The subject may edit the customer profile (a customer.write action). */
  readonly canEditProfile: boolean;
  /** Read without write: show data, hide every write affordance. */
  readonly readOnly: boolean;
}

export function deriveCustomerExperience(
  can: (capability: Capability) => boolean,
): CustomerExperience {
  const canRead = can(CAPABILITIES.CUSTOMER_READ);
  const canWrite = can(CAPABILITIES.CUSTOMER_WRITE);

  return {
    canRead,
    canWrite,
    canViewTickets: can(CAPABILITIES.TICKET_READ),
    canViewAgentActivity: can(CAPABILITIES.AGENT_RUN),
    canEditProfile: canWrite,
    readOnly: canRead && !canWrite,
  };
}
