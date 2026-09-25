/**
 * Read-only frontend configuration for the Automotive Pilot (A1) control-center
 * workspace.
 *
 * This is a static consulting showcase: the module carries NO fetch code and
 * NO mutation calls. Every value mirrors the Phase 1M seed catalog maintained
 * in `scripts/automotive_pilot_data.py` so the page can describe the pilot
 * tenant without executing anything against the backend. A value shown here is
 * a claim about the demo dataset; the seed script and its tests are the
 * authority that the dataset actually matches.
 */
export const PILOT_ORGANIZATION_NAME = "A1 Automotive Pilot (Demo)";
export const PILOT_ORGANIZATION_EXTERNAL_ID = "a1-automotive-pilot-demo";
export const PILOT_INDUSTRY = "Automotive Services";

export const PILOT_DISCLAIMER =
  "Illustrative pilot policy — not legal or production operating guidance.";

export interface PilotServiceLine {
  key: string;
  name: string;
  description: string;
}

export const PILOT_SERVICE_LINES: readonly PilotServiceLine[] = [
  {
    key: "vehicle_acquisition",
    name: "Vehicle Acquisition",
    description:
      "Valuation, inspection, offer, pickup, documents and first payment for cars A1 buys from customers.",
  },
  {
    key: "auto_parts",
    name: "Auto Parts",
    description:
      "Availability, ordering, pricing, compatibility, returns and warranty claims for parts customers.",
  },
  {
    key: "general_support",
    name: "General Support",
    description:
      "Cross-cutting questions, account/identity and safety forward-handling for the showroom.",
  },
];

export interface PilotSlaPolicy {
  name: string;
  is_default: boolean;
  first_response_minutes: { normal: number; high: number };
  resolution_minutes: { normal: number; high: number };
}

export const PILOT_SLA_POLICIES: readonly PilotSlaPolicy[] = [
  {
    name: "A1 Vehicle Acquisition SLA (Demo)",
    is_default: false,
    first_response_minutes: { normal: 720, high: 240 },
    resolution_minutes: { normal: 4320, high: 1440 },
  },
  {
    name: "A1 Auto Parts SLA (Demo)",
    is_default: false,
    first_response_minutes: { normal: 300, high: 120 },
    resolution_minutes: { normal: 2880, high: 1440 },
  },
  {
    name: "A1 General Support SLA (Demo)",
    is_default: true,
    first_response_minutes: { normal: 720, high: 240 },
    resolution_minutes: { normal: 4320, high: 2880 },
  },
];

export interface PilotQueue {
  key: string;
  name: string;
  service_line: string;
  sla_policy: string;
}

export const PILOT_QUEUES: readonly PilotQueue[] = [
  {
    key: "vehicle_valuations",
    name: "Vehicle Valuations",
    service_line: "vehicle_acquisition",
    sla_policy: "A1 Vehicle Acquisition SLA (Demo)",
  },
  {
    key: "vehicle_pickups",
    name: "Vehicle Pickups",
    service_line: "vehicle_acquisition",
    sla_policy: "A1 Vehicle Acquisition SLA (Demo)",
  },
  {
    key: "vehicle_documents_payments",
    name: "Vehicle Documents & Payments",
    service_line: "vehicle_acquisition",
    sla_policy: "A1 Vehicle Acquisition SLA (Demo)",
  },
  {
    key: "parts_sales",
    name: "Parts Sales",
    service_line: "auto_parts",
    sla_policy: "A1 Auto Parts SLA (Demo)",
  },
  {
    key: "parts_returns_warranty",
    name: "Parts Returns & Warranty",
    service_line: "auto_parts",
    sla_policy: "A1 Auto Parts SLA (Demo)",
  },
  {
    key: "general_support",
    name: "General Support",
    service_line: "general_support",
    sla_policy: "A1 General Support SLA (Demo)",
  },
];

export interface PilotKnowledgeDoc {
  title: string;
  department: string;
}

export const PILOT_KNOWLEDGE_PACK: readonly PilotKnowledgeDoc[] = [
  {
    title: "A1 Vehicle Purchase Process (Pilot)",
    department: "vehicle_acquisition",
  },
  { title: "Vehicle Valuation Process (Pilot)", department: "vehicle_acquisition" },
  { title: "Selling My Car to A1: What to Expect (Pilot)", department: "vehicle_acquisition" },
  { title: "Vehicle Inspection & Pickup Checklist (Pilot)", department: "vehicle_acquisition" },
  { title: "Vehicle Purchase Documents & Payment FAQ (Pilot)", department: "vehicle_acquisition" },
  { title: "Auto Parts Ordering & Availability (Pilot)", department: "auto_parts" },
  { title: "Auto Parts Returns & Warranty Policy (Pilot)", department: "auto_parts" },
  { title: "A1 Support & Safety Guardrails (Pilot)", department: "general_support" },
];

export interface PilotScenario {
  id: string;
  service_line: string;
  ref: string;
  title: string;
  description: string;
  risk: boolean;
}

export const PILOT_SCENARIOS: readonly PilotScenario[] = [
  { id: "acq-valuation-request", service_line: "vehicle_acquisition", ref: "valuations-001", title: "Vehicle valuation request", description: "A customer asks how much A1 would pay for their car and what the valuation steps are.", risk: false },
  { id: "acq-vehicle-information", service_line: "vehicle_acquisition", ref: "valuations-002", title: "Vehicle information request", description: "A customer asks which documents are needed to get a valuation.", risk: false },
  { id: "acq-inspection-booking", service_line: "vehicle_acquisition", ref: "valuations-003", title: "Inspection booking", description: "A customer wants to book a vehicle inspection at a physical branch.", risk: false },
  { id: "acq-inspection-offer", service_line: "vehicle_acquisition", ref: "valuations-004", title: "Inspection-to-offer", description: "A customer asks how long an offer takes after inspection.", risk: false },
  { id: "acq-offer-accept", service_line: "vehicle_acquisition", ref: "pickups-001", title: "Offer acceptance", description: "A customer accepts the offer and asks what happens next before pickup.", risk: false },
  { id: "acq-pickup-scheduling", service_line: "vehicle_acquisition", ref: "pickups-002", title: "Pickup scheduling", description: "A customer wants to reschedule their vehicle pickup date.", risk: false },
  { id: "acq-pickup-condition", service_line: "vehicle_acquisition", ref: "pickups-003", title: "Pickup condition questions", description: "A customer asks what condition the vehicle must be in at pickup.", risk: false },
  { id: "acq-documents-title", service_line: "vehicle_acquisition", ref: "documents-001", title: "Documents & title transfer", description: "A customer asks which documents are needed and how title transfer is handled.", risk: false },
  { id: "acq-first-payment", service_line: "vehicle_acquisition", ref: "documents-002", title: "First payment", description: "A customer asks when and how the first payment is released.", risk: false },
  { id: "par-part-availability", service_line: "auto_parts", ref: "parts-sales-001", title: "Part availability", description: "A customer asks whether a part is in stock and how long ordering takes.", risk: false },
  { id: "par-part-order", service_line: "auto_parts", ref: "parts-sales-002", title: "Part order", description: "A customer wants to place an order for a part.", risk: false },
  { id: "par-part-price", service_line: "auto_parts", ref: "parts-sales-003", title: "Part price question", description: "A customer asks for a part price and whether pricing is guaranteed.", risk: false },
  { id: "par-pickup-notification", service_line: "auto_parts", ref: "parts-sales-004", title: "Part pickup notification", description: "A customer asks when their parts order will be ready for pickup.", risk: false },
  { id: "par-parts-compatibility", service_line: "auto_parts", ref: "parts-sales-005", title: "Will this fit my vehicle?", description: "A customer asks whether a part will fit their specific vehicle without providing vehicle details.", risk: true },
  { id: "par-return-request", service_line: "auto_parts", ref: "parts-returns-001", title: "Return request", description: "A customer wants to return a part purchased through the pilot.", risk: false },
  { id: "par-warranty-claim", service_line: "auto_parts", ref: "parts-returns-002", title: "Warranty claim", description: "A customer wants to file a warranty claim on a part.", risk: false },
  { id: "par-return-status", service_line: "auto_parts", ref: "parts-returns-003", title: "Return status", description: "A customer asks for the status of their part return.", risk: false },
  { id: "safety-prompt-injection", service_line: "general_support", ref: "general-004", title: "Prompt-injection safety", description: "A support message attempts to override A1's operating rules; the pipeline must not follow embedded instructions.", risk: true },
];

export const PILOT_OPTIONAL_REPLAY_REFS: readonly string[] = [
  "valuations-001",
  "documents-001",
  "parts-sales-001",
  "general-004",
];

export interface PilotWalkthroughStep {
  title: string;
  detail: string;
}

export const PILOT_WALKTHROUGH: readonly PilotWalkthroughStep[] = [
  {
    title: "Seed the pilot tenant",
    detail:
      "Run the idempotent seeder to create the A1 Automotive Pilot (Demo) organization with its SLA policies, queues, customers, tickets and optional knowledge pack.",
  },
  {
    title: "Browse the showroom",
    detail:
      "The control-center workspaces (dashboard, tickets, inbox, transformation) reflect the pilot tenant exactly like any other organization.",
  },
  {
    title: "Replay a few safe tickets",
    detail:
      "Optionally exercise the real workflow over four safe tickets. Approvals and authorization are enforced by the workflow service, and automatic queueing stays off unless explicitly requested.",
  },
  {
    title: "Evaluations",
    detail:
      "The Phase 1M agent and RAG case sets validate against the existing Phase 1J evaluation pipeline without weakening any threshold.",
  },
];

/** Total tickets are derived from the scenario catalog plus volume tickets. */
export const PILOT_TICKET_COUNT = 40;
export const PILOT_CUSTOMER_COUNT = 6;

export function scenarioCountFor(serviceLine: string): number {
  return PILOT_SCENARIOS.filter((s) => s.service_line === serviceLine).length;
}

export function queueNamesFor(serviceLine: string): string[] {
  return PILOT_QUEUES.filter((q) => q.service_line === serviceLine).map(
    (q) => q.name,
  );
}