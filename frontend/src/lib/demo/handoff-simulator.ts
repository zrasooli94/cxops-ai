/**
 * Offline, fictional handoff data for a public *visual* product simulation.
 * Never connect this module to the authenticated backend or production events.
 */
export type DemoStatus = "waiting" | "assigned" | "resolved";
export type DemoPriority = "urgent" | "high" | "normal";
export type DemoFilter = "all" | DemoStatus;

export interface DemoMessage {
  id: number;
  from: "visitor" | "staff" | "system";
  body: string;
  time: string;
}

export interface DemoCase {
  id: string;
  visitor: string;
  topic: string;
  channel: "Website chat" | "Support form";
  status: DemoStatus;
  priority: DemoPriority;
  ageMinutes: number;
  owner: string | null;
  messages: DemoMessage[];
}

export const INITIAL_DEMO_CASES: readonly DemoCase[] = [
  {
    id: "DEMO-1042",
    visitor: "Demo customer A",
    topic: "Urgent appointment change",
    channel: "Website chat",
    status: "waiting",
    priority: "urgent",
    ageMinutes: 21,
    owner: null,
    messages: [
      { id: 1, from: "visitor", body: "I need to move my appointment today. Can someone help?", time: "21 min ago" },
      { id: 2, from: "system", body: "I've sent this to our human support team.", time: "20 min ago" },
      { id: 3, from: "visitor", body: "Thank you. I'll wait here.", time: "18 min ago" },
    ],
  },
  {
    id: "DEMO-1043",
    visitor: "Demo customer B",
    topic: "Quotation follow-up",
    channel: "Support form",
    status: "waiting",
    priority: "high",
    ageMinutes: 12,
    owner: null,
    messages: [
      { id: 1, from: "visitor", body: "Could your team confirm the quotation options?", time: "12 min ago" },
      { id: 2, from: "system", body: "A team member will review your request.", time: "11 min ago" },
    ],
  },
  {
    id: "DEMO-1044",
    visitor: "Demo customer C",
    topic: "Service hours question",
    channel: "Website chat",
    status: "waiting",
    priority: "normal",
    ageMinutes: 5,
    owner: null,
    messages: [
      { id: 1, from: "visitor", body: "Can I speak with someone about your opening hours?", time: "5 min ago" },
    ],
  },
  {
    id: "DEMO-1045",
    visitor: "Demo customer D",
    topic: "Booking confirmation",
    channel: "Website chat",
    status: "assigned",
    priority: "high",
    ageMinutes: 8,
    owner: "Demo agent",
    messages: [
      { id: 1, from: "visitor", body: "I submitted a booking request earlier. Is it confirmed?", time: "8 min ago" },
      { id: 2, from: "staff", body: "I'll check the booking details in our system.", time: "6 min ago" },
    ],
  },
  {
    id: "DEMO-1046",
    visitor: "Demo customer E",
    topic: "Document upload question",
    channel: "Support form",
    status: "assigned",
    priority: "normal",
    ageMinutes: 4,
    owner: "Demo agent",
    messages: [
      { id: 1, from: "visitor", body: "Which document format should I upload?", time: "4 min ago" },
      { id: 2, from: "staff", body: "PDF files are generally easiest for our review.", time: "2 min ago" },
    ],
  },
  {
    id: "DEMO-1047",
    visitor: "Demo customer F",
    topic: "Contact details updated",
    channel: "Website chat",
    status: "resolved",
    priority: "normal",
    ageMinutes: 31,
    owner: "Demo agent",
    messages: [
      { id: 1, from: "visitor", body: "I want to update the phone number on my request.", time: "31 min ago" },
      { id: 2, from: "staff", body: "Your demonstration request has been marked as resolved.", time: "28 min ago" },
    ],
  },
];

export function freshDemoCases(): DemoCase[] {
  return INITIAL_DEMO_CASES.map((entry) => ({
    ...entry,
    messages: entry.messages.map((message) => ({ ...message })),
  }));
}

export function summarizeDemo(cases: readonly DemoCase[]) {
  const waitingCases = cases.filter((item) => item.status === "waiting");
  return {
    waiting: waitingCases.length,
    assigned: cases.filter((item) => item.status === "assigned").length,
    resolved: cases.filter((item) => item.status === "resolved").length,
    oldestWaitingMinutes: waitingCases.length
      ? Math.max(...waitingCases.map((item) => item.ageMinutes))
      : null,
    urgentWaiting: waitingCases.filter((item) => item.priority === "urgent").length,
  };
}

export function filterDemoCases(cases: readonly DemoCase[], filter: DemoFilter, search: string): DemoCase[] {
  const normalizedSearch = search.trim().toLocaleLowerCase();
  return cases.filter((item) => {
    const matchesStatus = filter === "all" || item.status === filter;
    const searchable = [item.id, item.visitor, item.topic, item.channel].join(" ").toLocaleLowerCase();
    return matchesStatus && (!normalizedSearch || searchable.includes(normalizedSearch));
  });
}

export type DemoAction = "assign" | "release" | "resolve";

/** Status changes are local-only and intentionally mirror basic staff transition guards. */
export function transitionDemo(cases: readonly DemoCase[], id: string, action: DemoAction): DemoCase[] {
  return cases.map((item) => {
    if (item.id !== id) return item;
    if (action === "assign" && item.status === "waiting") {
      return { ...item, status: "assigned", owner: "You (demo)" };
    }
    if (action === "release" && item.status === "assigned") {
      return { ...item, status: "waiting", owner: null };
    }
    if (action === "resolve" && item.status === "assigned") {
      return { ...item, status: "resolved" };
    }
    return item;
  });
}

export function addDemoReply(cases: readonly DemoCase[], id: string, body: string): DemoCase[] {
  const message = body.trim();
  if (!message || message.length > 500) return [...cases];
  return cases.map((item) => {
    if (item.id !== id || item.status !== "assigned") return item;
    return {
      ...item,
      messages: [
        ...item.messages,
        { id: item.messages.reduce((max, m) => Math.max(max, m.id), 0) + 1, from: "staff", body: message, time: "Just now (demo)" },
      ],
    };
  });
}
