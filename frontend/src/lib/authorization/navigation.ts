/**
 * Single active source of truth for control-center navigation and the
 * capability each destination requires.
 *
 * This module is intentionally pure (no React, no icon imports) so the
 * requirement list can be unit-tested with the Node built-in runner and shared
 * by both server and client boundaries.
 *
 * A `null` requirement means "authenticated control-center access is enough"
 * (the destination is always visible after a valid session + tenant).
 */
import { CAPABILITIES, type Capability } from "./capabilities.ts";

export type NavigationIconName =
  | "gauge"
  | "ticket"
  | "inbox"
  | "bot"
  | "shield"
  | "brain"
  | "workflow"
  | "activity"
  | "users"
  | "layers";

/** Every control-center destination that carries a capability requirement. */
export type ControlCenterRoute =
  | "/dashboard"
  | "/customers"
  | "/tickets"
  | "/tickets/new"
  | "/inbox"
  | "/agent"
  | "/approvals"
  | "/knowledge"
  | "/runs"
  | "/observability"
  | "/operations";

/**
 * Single source of truth for what each route requires. Sidebar visibility
 * (below), dashboard quick links and the direct-route UX guards all read this
 * same map, so navigation and route protection cannot drift apart.
 *
 * `null` means authentication + tenant context is sufficient.
 */
export const ROUTE_REQUIREMENTS: Readonly<
  Record<ControlCenterRoute, Capability | null>
> = {
  "/dashboard": null,
  "/customers": CAPABILITIES.CUSTOMER_READ,
  "/tickets": CAPABILITIES.TICKET_READ,
  "/tickets/new": CAPABILITIES.TICKET_WRITE,
  "/inbox": CAPABILITIES.TICKET_READ,
  "/agent": CAPABILITIES.AGENT_RUN,
  "/approvals": CAPABILITIES.AGENT_RUN,
  "/knowledge": CAPABILITIES.KNOWLEDGE_READ,
  "/runs": CAPABILITIES.AGENT_RUN,
  "/observability": CAPABILITIES.OBSERVABILITY_READ,
  "/operations": CAPABILITIES.TICKET_READ,
};

export interface NavigationItem {
  href: ControlCenterRoute;
  label: string;
  description: string;
  icon: NavigationIconName;
  /** Capability required to see this destination, or null for always-visible. */
  requiredCapability: Capability | null;
}

/**
 * Shared visibility predicate: a `null` requirement is always satisfied,
 * otherwise the capability must be present. Used by navigation rendering and by
 * the client route guard so both apply identical rules.
 */
export function isCapabilityRequirementSatisfied(
  requiredCapability: Capability | null,
  can: (capability: Capability) => boolean,
): boolean {
  return requiredCapability === null || can(requiredCapability);
}

/**
 * Primary sidebar destinations. `role` is never consulted here — visibility is
 * decided solely by the capability strings the backend returned.
 */
export const PRIMARY_NAVIGATION: readonly NavigationItem[] = [
  {
    href: "/dashboard",
    label: "Dashboard",
    description: "Operations overview",
    icon: "gauge",
    requiredCapability: ROUTE_REQUIREMENTS["/dashboard"],
  },
  {
    href: "/customers",
    label: "Customers",
    description: "Customer 360 workspace",
    icon: "users",
    requiredCapability: ROUTE_REQUIREMENTS["/customers"],
  },
  {
    href: "/tickets",
    label: "Tickets",
    description: "Customer support workspace",
    icon: "ticket",
    requiredCapability: ROUTE_REQUIREMENTS["/tickets"],
  },
  {
    href: "/operations",
    label: "Operations",
    description: "Queues, ownership & SLA",
    icon: "layers",
    requiredCapability: ROUTE_REQUIREMENTS["/operations"],
  },
  {
    href: "/inbox",
    label: "Inbox",
    description: "Unified conversation inbox",
    icon: "inbox",
    requiredCapability: ROUTE_REQUIREMENTS["/inbox"],
  },
  {
    href: "/agent",
    label: "AI Agent",
    description: "LangGraph execution console",
    icon: "bot",
    requiredCapability: ROUTE_REQUIREMENTS["/agent"],
  },
  {
    href: "/approvals",
    label: "Approvals",
    description: "Human-in-the-loop safety",
    icon: "shield",
    // Read visibility only; approve/execute actions require their own
    // capabilities in a later slice.
    requiredCapability: ROUTE_REQUIREMENTS["/approvals"],
  },
  {
    href: "/knowledge",
    label: "Knowledge",
    description: "RAG playground & ingestion",
    icon: "brain",
    requiredCapability: ROUTE_REQUIREMENTS["/knowledge"],
  },
  {
    href: "/runs",
    label: "Runs",
    description: "Persistent audit trail",
    icon: "workflow",
    requiredCapability: ROUTE_REQUIREMENTS["/runs"],
  },
  {
    href: "/observability",
    label: "Observability",
    description: "Production telemetry",
    icon: "activity",
    requiredCapability: ROUTE_REQUIREMENTS["/observability"],
  },
];

/** Dashboard quick-link cards (everything except the dashboard itself). */
export const DASHBOARD_NAVIGATION: readonly NavigationItem[] =
  PRIMARY_NAVIGATION.filter((item) => item.href !== "/dashboard");

export function isNavigationItemVisible(
  item: NavigationItem,
  can: (capability: Capability) => boolean,
): boolean {
  return isCapabilityRequirementSatisfied(item.requiredCapability, can);
}

/**
 * Filter destinations down to those the capability set authorizes. Uses the
 * same predicate as the client context (`AuthorizationValue.can`).
 */
export function filterNavigationByCapabilities(
  items: readonly NavigationItem[],
  can: (capability: Capability) => boolean,
): NavigationItem[] {
  return items.filter((item) => isNavigationItemVisible(item, can));
}
