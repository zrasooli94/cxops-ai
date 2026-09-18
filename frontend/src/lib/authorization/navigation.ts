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
  | "bot"
  | "shield"
  | "brain"
  | "workflow"
  | "activity";

export interface NavigationItem {
  href: string;
  label: string;
  description: string;
  icon: NavigationIconName;
  /** Capability required to see this destination, or null for always-visible. */
  requiredCapability: Capability | null;
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
    requiredCapability: null,
  },
  {
    href: "/tickets",
    label: "Tickets",
    description: "Customer support workspace",
    icon: "ticket",
    requiredCapability: CAPABILITIES.TICKET_READ,
  },
  {
    href: "/agent",
    label: "AI Agent",
    description: "LangGraph execution console",
    icon: "bot",
    requiredCapability: CAPABILITIES.AGENT_RUN,
  },
  {
    href: "/approvals",
    label: "Approvals",
    description: "Human-in-the-loop safety",
    icon: "shield",
    // Read visibility only; approve/execute actions require their own
    // capabilities in a later slice.
    requiredCapability: CAPABILITIES.AGENT_RUN,
  },
  {
    href: "/knowledge",
    label: "Knowledge",
    description: "RAG playground & ingestion",
    icon: "brain",
    requiredCapability: CAPABILITIES.KNOWLEDGE_READ,
  },
  {
    href: "/runs",
    label: "Runs",
    description: "Persistent audit trail",
    icon: "workflow",
    requiredCapability: CAPABILITIES.AGENT_RUN,
  },
  {
    href: "/observability",
    label: "Observability",
    description: "Production telemetry",
    icon: "activity",
    requiredCapability: CAPABILITIES.OBSERVABILITY_READ,
  },
];

/** Dashboard quick-link cards (everything except the dashboard itself). */
export const DASHBOARD_NAVIGATION: readonly NavigationItem[] =
  PRIMARY_NAVIGATION.filter((item) => item.href !== "/dashboard");

export function isNavigationItemVisible(
  item: NavigationItem,
  can: (capability: Capability) => boolean,
): boolean {
  return item.requiredCapability === null || can(item.requiredCapability);
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
