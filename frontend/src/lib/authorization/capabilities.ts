/**
 * Capability name constants for type safety and autocomplete only.
 *
 * The backend `GET /me/authorization` response is authoritative. These constants
 * exist so call sites can reference capability strings without typos.
 *
 * This file is NOT a policy matrix. It deliberately contains no role mapping
 * (no `OWNER -> [...]`, no `ADMIN -> [...]`). The frontend never decides what a
 * role may do; it only checks the capability strings the backend returned for
 * the resolved organization.
 */
export const CAPABILITIES = {
  ORGANIZATION_READ: "organization.read",
  ORGANIZATION_MANAGE: "organization.manage",
  MEMBER_READ: "member.read",
  MEMBER_MANAGE: "member.manage",
  CUSTOMER_READ: "customer.read",
  CUSTOMER_WRITE: "customer.write",
  TICKET_READ: "ticket.read",
  TICKET_WRITE: "ticket.write",
  AGENT_RUN: "agent.run",
  AGENT_APPROVE: "agent.approve",
  AGENT_EXECUTE: "agent.execute",
  KNOWLEDGE_READ: "knowledge.read",
  KNOWLEDGE_MANAGE: "knowledge.manage",
  AUTOMATION_READ: "automation.read",
  AUTOMATION_MANAGE: "automation.manage",
  INTEGRATION_READ: "integration.read",
  INTEGRATION_MANAGE: "integration.manage",
  OBSERVABILITY_READ: "observability.read",
  EVALUATION_READ: "evaluation.read",
  EVALUATION_MANAGE: "evaluation.manage",
} as const;

export type Capability = (typeof CAPABILITIES)[keyof typeof CAPABILITIES];
