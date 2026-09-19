"use client";

import {
  Activity,
  ArrowLeft,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  LoaderCircle,
  Mail,
  Pencil,
  Phone,
  Save,
  Ticket as TicketIcon,
  TriangleAlert,
  Users,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useState,
} from "react";

import { useAuthorization } from "@/lib/authorization/context";
import {
  authorizationFeedback,
  planClientAuthorizationResponse,
} from "@/lib/authorization/helpers";
import { deriveCustomerExperience } from "@/lib/customer/customer";

type Customer = {
  id: number;
  name: string;
  email: string;
  phone: string | null;
  organization_id: number;
  external_id: string | null;
  created_at: string;
};

type CustomerTicket = {
  id: number;
  external_id: string | null;
  subject: string;
  status: string;
  priority: string;
  source: string;
  category: string | null;
  assigned_team: string | null;
  created_at: string;
  updated_at: string;
};

type CustomerSummary = {
  customer_id: number;
  total_tickets: number;
  open_tickets: number;
  closed_or_resolved_tickets: number;
  latest_ticket_at: string | null;
  latest_interaction_at: string | null;
  most_recent_ticket: CustomerTicket | null;
  common_category: string | null;
};

type TimelineEvent = {
  id: string;
  type: string;
  source: "ticket" | "ticket_event" | "agent";
  occurred_at: string;
  title: string;
  summary: string | null;
  ticket_id: number | null;
  agent_run_id: string | null;
  metadata: Record<string, unknown>;
};

type TimelineResponse = {
  items: TimelineEvent[];
  total: number;
  partial: boolean;
  unavailable_sources: string[];
};

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "violet";

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
}) {
  const styles: Record<BadgeVariant, string> = {
    default: "border-slate-200 bg-slate-50 text-slate-600",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning: "border-amber-200 bg-amber-50 text-amber-700",
    danger: "border-rose-200 bg-rose-50 text-rose-700",
    info: "border-blue-200 bg-blue-50 text-blue-700",
    violet: "border-violet-200 bg-violet-50 text-violet-700",
  };

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function priorityVariant(priority: string): BadgeVariant {
  switch (priority.toLowerCase()) {
    case "urgent":
    case "high":
      return "danger";
    case "normal":
      return "warning";
    case "low":
      return "success";
    default:
      return "default";
  }
}

function sourceVariant(source: TimelineEvent["source"]): BadgeVariant {
  switch (source) {
    case "agent":
      return "violet";
    case "ticket_event":
      return "info";
    default:
      return "default";
  }
}

function sourceLabel(source: TimelineEvent["source"]) {
  switch (source) {
    case "agent":
      return "Agent";
    case "ticket_event":
      return "Ticket event";
    default:
      return "Ticket";
  }
}

function formatAction(value: string) {
  return value
    .split("_")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(" ");
}

function StatCard({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number;
  note: string;
  tone: "violet" | "amber" | "emerald";
}) {
  const toneClass = {
    violet: "from-violet-500/10 to-indigo-500/[0.025] text-violet-600",
    amber: "from-amber-400/12 to-orange-400/[0.025] text-amber-600",
    emerald: "from-emerald-400/12 to-teal-400/[0.025] text-emerald-600",
  }[tone];

  return (
    <div
      className={`app-panel relative overflow-hidden rounded-[20px] bg-gradient-to-br p-6 ${toneClass}`}
    >
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
        {label}
      </p>

      <p className="editorial-number mt-4 text-4xl font-medium tracking-[-0.045em] text-slate-950">
        {value}
      </p>

      <p className="mt-3 text-xs leading-5 text-slate-500">
        {note}
      </p>
    </div>
  );
}

function InfoRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-5 border-b border-slate-200/70 py-3.5 last:border-0">
      <span className="text-xs text-slate-400">{label}</span>

      <span className="max-w-[68%] text-right text-sm font-medium text-slate-700">
        {value}
      </span>
    </div>
  );
}

export default function CustomerDetailPage() {
  const params = useParams<{ customerId: string }>();
  const customerId = params.customerId;
  const { can, refresh } = useAuthorization();
  const { canViewTickets, canViewAgentActivity, canEditProfile } =
    deriveCustomerExperience(can);

  const [customer, setCustomer] = useState<Customer | null>(null);
  const [summary, setSummary] = useState<CustomerSummary | null>(null);
  const [tickets, setTickets] = useState<CustomerTicket[]>([]);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);

  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState("");

  const [editing, setEditing] = useState(false);
  const [formName, setFormName] = useState("");
  const [formEmail, setFormEmail] = useState("");
  const [formPhone, setFormPhone] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveSuccess, setSaveSuccess] = useState(false);

  const showTimeline = canViewTickets || canViewAgentActivity;

  const loadCustomer = useCallback(async () => {
    setLoading(true);
    setError("");
    setNotFound(false);

    async function fetchJson(path: string) {
      const response = await fetch(`/api/backend${path}`, {
        cache: "no-store",
      });
      const body = await response.json().catch(() => null);
      return { response, body };
    }

    try {
      const { response, body } = await fetchJson(
        `/customers/${customerId}`,
      );

      if (response.status === 404) {
        setNotFound(true);
        return;
      }

      if (!response.ok) {
        const plan = planClientAuthorizationResponse(
          response.status,
          body?.detail,
        );
        const feedback = authorizationFeedback(plan);

        if (feedback) {
          setError(feedback);
          refresh();
          return;
        }

        throw new Error(
          body?.detail ?? `Customer API returned ${response.status}`,
        );
      }

      const loaded = body as Customer;
      setCustomer(loaded);
      setFormName(loaded.name);
      setFormEmail(loaded.email);
      setFormPhone(loaded.phone ?? "");

      const summaryResult = await fetchJson(
        `/customers/${customerId}/summary`,
      );
      if (summaryResult.response.ok) {
        setSummary(summaryResult.body as CustomerSummary);
      }

      if (canViewTickets) {
        const ticketsResult = await fetchJson(
          `/customers/${customerId}/tickets?limit=20`,
        );
        if (ticketsResult.response.ok) {
          setTickets(
            (ticketsResult.body as { items: CustomerTicket[] }).items,
          );
        }
      }

      if (showTimeline) {
        const timelineResult = await fetchJson(
          `/customers/${customerId}/timeline?limit=50`,
        );
        if (timelineResult.response.ok) {
          setTimeline(timelineResult.body as TimelineResponse);
        }
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to load customer.",
      );
    } finally {
      setLoading(false);
    }
  }, [customerId, canViewTickets, showTimeline, refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadCustomer();
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadCustomer]);

  async function saveProfile(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!customer) {
      return;
    }

    setSaving(true);
    setSaveError("");
    setSaveSuccess(false);

    try {
      const response = await fetch(
        `/api/backend/customers/${customer.id}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          cache: "no-store",
          body: JSON.stringify({
            name: formName,
            email: formEmail,
            phone: formPhone.trim() === "" ? null : formPhone,
          }),
        },
      );

      const body = await response.json().catch(() => null);

      if (!response.ok) {
        const plan = planClientAuthorizationResponse(
          response.status,
          body?.detail,
        );
        const feedback = authorizationFeedback(plan);

        if (feedback) {
          setSaveError(feedback);
          refresh();
          return;
        }

        throw new Error(
          body?.detail ?? `Customer API returned ${response.status}`,
        );
      }

      const updated = body as Customer;
      setCustomer(updated);
      setFormName(updated.name);
      setFormEmail(updated.email);
      setFormPhone(updated.phone ?? "");
      setEditing(false);
      setSaveSuccess(true);
    } catch (err) {
      setSaveError(
        err instanceof Error
          ? err.message
          : "Failed to update customer.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <LoaderCircle className="h-7 w-7 animate-spin text-violet-500" />
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <div className="app-panel max-w-md rounded-[24px] p-10 text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-50">
            <Users className="h-6 w-6 text-slate-400" />
          </div>

          <h1 className="mt-5 text-lg font-medium text-slate-900">
            Customer not found
          </h1>

          <p className="mt-2 text-sm leading-6 text-slate-500">
            This customer does not exist in the selected
            organization.
          </p>

          <Link
            href="/customers"
            className="mt-6 inline-flex items-center gap-2 rounded-full bg-[#111827] px-5 py-2.5 text-xs font-medium text-white"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to customers
          </Link>
        </div>
      </div>
    );
  }

  if (error || !customer) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <div className="app-panel max-w-md rounded-[24px] p-10 text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-red-50">
            <TriangleAlert className="h-6 w-6 text-red-500" />
          </div>

          <h1 className="mt-5 text-lg font-medium text-slate-900">
            Could not load customer
          </h1>

          <p className="mt-2 text-sm leading-6 text-slate-500">
            {error || "The customer could not be loaded."}
          </p>

          <button
            type="button"
            onClick={() => void loadCustomer()}
            className="mt-6 inline-flex items-center gap-2 rounded-full bg-[#111827] px-5 py-2.5 text-xs font-medium text-white"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <Link
              href="/customers"
              className="inline-flex items-center gap-2 text-xs font-medium text-slate-500 transition hover:text-violet-600"
            >
              <ArrowLeft className="h-4 w-4" />
              Customers
            </Link>

            <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
              {customer.name}
            </p>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          {saveSuccess && (
            <div className="mb-6 flex items-center gap-3 rounded-[18px] border border-emerald-200 bg-emerald-50/80 p-4 text-sm text-emerald-700">
              <CheckCircle2 className="h-5 w-5 shrink-0" />
              Customer profile updated.
            </div>
          )}

          <section className="app-panel overflow-hidden rounded-[22px]">
            <div className="relative p-6 md:p-8">
              <div className="pointer-events-none absolute -right-28 -top-36 h-80 w-80 rounded-full bg-violet-300/10 blur-3xl" />

              <div className="relative flex flex-col justify-between gap-6 md:flex-row md:items-start">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                      Customer #{customer.id}
                    </span>

                    {customer.external_id && (
                      <Badge variant="info">
                        {customer.external_id}
                      </Badge>
                    )}

                    {!canEditProfile && (
                      <Badge>Read-only</Badge>
                    )}
                  </div>

                  <h1 className="mt-5 text-3xl font-medium tracking-[-0.04em] text-slate-950">
                    {customer.name}
                  </h1>
                </div>

                {canEditProfile && !editing && (
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(true);
                      setSaveError("");
                      setSaveSuccess(false);
                    }}
                    className="inline-flex shrink-0 items-center gap-2 rounded-full border border-slate-200 bg-white px-5 py-2.5 text-xs font-medium text-slate-700 shadow-sm transition hover:border-violet-300 hover:text-violet-600"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                    Edit profile
                  </button>
                )}
              </div>

              {editing ? (
                <form
                  onSubmit={saveProfile}
                  className="relative mt-7 grid gap-4 md:grid-cols-3"
                >
                  <label className="text-xs text-slate-500">
                    Name
                    <input
                      value={formName}
                      onChange={(event) =>
                        setFormName(event.target.value)
                      }
                      required
                      className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 outline-none focus:border-violet-300 focus:ring-4 focus:ring-violet-100/50"
                    />
                  </label>

                  <label className="text-xs text-slate-500">
                    Email
                    <input
                      type="email"
                      value={formEmail}
                      onChange={(event) =>
                        setFormEmail(event.target.value)
                      }
                      required
                      className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 outline-none focus:border-violet-300 focus:ring-4 focus:ring-violet-100/50"
                    />
                  </label>

                  <label className="text-xs text-slate-500">
                    Phone
                    <input
                      value={formPhone}
                      onChange={(event) =>
                        setFormPhone(event.target.value)
                      }
                      className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 outline-none focus:border-violet-300 focus:ring-4 focus:ring-violet-100/50"
                    />
                  </label>

                  {saveError && (
                    <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50/80 p-3 text-xs text-red-700 md:col-span-3">
                      <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
                      {saveError}
                    </div>
                  )}

                  <div className="flex items-center gap-2 md:col-span-3">
                    <button
                      type="submit"
                      disabled={saving}
                      className="inline-flex items-center gap-2 rounded-full bg-[#111827] px-5 py-2.5 text-xs font-medium text-white disabled:opacity-60"
                    >
                      {saving ? (
                        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Save className="h-3.5 w-3.5" />
                      )}
                      {saving ? "Saving..." : "Save changes"}
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setEditing(false);
                        setSaveError("");
                        setFormName(customer.name);
                        setFormEmail(customer.email);
                        setFormPhone(customer.phone ?? "");
                      }}
                      className="rounded-full border border-slate-200 bg-white px-5 py-2.5 text-xs font-medium text-slate-600"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <div className="relative mt-7 grid gap-4 md:grid-cols-3">
                  <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff]/80 p-4">
                    <p className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                      <Mail className="h-3.5 w-3.5" />
                      Email
                    </p>
                    <p className="mt-2 truncate text-sm font-medium text-slate-800">
                      {customer.email}
                    </p>
                  </div>

                  <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff]/80 p-4">
                    <p className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                      <Phone className="h-3.5 w-3.5" />
                      Phone
                    </p>
                    <p className="mt-2 text-sm font-medium text-slate-800">
                      {customer.phone ?? "—"}
                    </p>
                  </div>

                  <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff]/80 p-4">
                    <p className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
                      <CircleDot className="h-3.5 w-3.5" />
                      Created
                    </p>
                    <p className="mt-2 text-sm font-medium text-slate-800">
                      {new Date(customer.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
              )}
            </div>
          </section>

          {summary && (
            <section className="mt-6 grid gap-4 md:grid-cols-3">
              <StatCard
                label="Total tickets"
                value={summary.total_tickets}
                note="All tickets linked to this customer."
                tone="violet"
              />

              <StatCard
                label="Open"
                value={summary.open_tickets}
                note="New, open, or pending cases."
                tone="amber"
              />

              <StatCard
                label="Closed / resolved"
                value={summary.closed_or_resolved_tickets}
                note="Solved or closed cases."
                tone="emerald"
              />
            </section>
          )}

          <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
            {showTimeline && (
              <section className="app-panel rounded-[22px] p-6 md:p-7">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                      <Activity className="h-5 w-5" />
                    </div>

                    <div>
                      <h2 className="font-medium text-slate-900">
                        Activity timeline
                      </h2>

                      <p className="text-xs text-slate-400">
                        Newest first, capability-scoped
                      </p>
                    </div>
                  </div>

                  {timeline && (
                    <Badge variant="info">
                      {timeline.total} event
                      {timeline.total === 1 ? "" : "s"}
                    </Badge>
                  )}
                </div>

                {timeline?.partial && (
                  <div className="mt-5 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-700">
                    <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                    Some activity sources are temporarily
                    unavailable
                    {timeline.unavailable_sources.length > 0
                      ? ` (${timeline.unavailable_sources.join(", ")})`
                      : ""}
                    .
                  </div>
                )}

                {!timeline || timeline.items.length === 0 ? (
                  <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/70 p-6 text-sm text-slate-500">
                    No activity recorded for this customer yet.
                  </div>
                ) : (
                  <ol className="mt-6 space-y-4">
                    {timeline.items.map((event) => (
                      <li
                        key={event.id}
                        className="flex gap-4"
                      >
                        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-slate-400">
                          {event.source === "agent" ? (
                            <Bot className="h-4 w-4" />
                          ) : event.source === "ticket" ? (
                            <TicketIcon className="h-4 w-4" />
                          ) : (
                            <CircleDot className="h-4 w-4" />
                          )}
                        </div>

                        <div className="min-w-0 flex-1 border-b border-slate-200/60 pb-4">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={sourceVariant(event.source)}>
                              {sourceLabel(event.source)}
                            </Badge>

                            <span className="text-[11px] text-slate-400">
                              {new Date(
                                event.occurred_at,
                              ).toLocaleString()}
                            </span>
                          </div>

                          <p className="mt-2 text-sm font-medium text-slate-800">
                            {event.title}
                          </p>

                          {event.summary && (
                            <p className="mt-1 text-xs text-slate-500">
                              {event.summary}
                            </p>
                          )}

                          {Object.keys(event.metadata).length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-2">
                              {Object.entries(event.metadata).map(
                                ([key, value]) => (
                                  <span
                                    key={key}
                                    className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[10px] text-slate-500"
                                  >
                                    {formatAction(key)}:{" "}
                                    {String(value)}
                                  </span>
                                ),
                              )}
                            </div>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            )}

            <div className={showTimeline ? "space-y-6" : "space-y-6 xl:col-span-2"}>
              {canViewTickets && (
                <section className="app-panel rounded-[22px] p-6 md:p-7">
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-500">
                        <TicketIcon className="h-5 w-5" />
                      </div>

                      <div>
                        <h2 className="font-medium text-slate-900">
                          Linked tickets
                        </h2>

                        <p className="text-xs text-slate-400">
                          Cases attached to this customer
                        </p>
                      </div>
                    </div>

                    <Link
                      href={`/tickets?customer_id=${customer.id}`}
                      className="inline-flex items-center gap-1 text-[11px] font-medium text-violet-600"
                    >
                      View all
                      <ChevronRight className="h-3.5 w-3.5" />
                    </Link>
                  </div>

                  {tickets.length === 0 ? (
                    <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/70 p-5 text-sm text-slate-500">
                      No tickets linked to this customer.
                    </div>
                  ) : (
                    <ul className="mt-6 space-y-3">
                      {tickets.map((ticket) => (
                        <li
                          key={ticket.id}
                          className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-4"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
                                #{ticket.id}
                                {ticket.external_id
                                  ? ` · ${ticket.external_id}`
                                  : ""}
                              </p>

                              <p className="mt-1.5 truncate text-sm font-medium text-slate-800">
                                {ticket.subject}
                              </p>

                              <div className="mt-2 flex flex-wrap items-center gap-2">
                                <Badge>{ticket.status}</Badge>

                                <Badge
                                  variant={priorityVariant(
                                    ticket.priority,
                                  )}
                                >
                                  {ticket.priority}
                                </Badge>
                              </div>
                            </div>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              )}

              <section className="app-panel rounded-[22px] p-6 md:p-7">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                    <CircleDot className="h-5 w-5" />
                  </div>

                  <div>
                    <h2 className="font-medium text-slate-900">
                      Service context
                    </h2>

                    <p className="text-xs text-slate-400">
                      Derived from linked tickets
                    </p>
                  </div>
                </div>

                <div className="mt-5">
                  <InfoRow
                    label="Most recent ticket"
                    value={
                      summary?.most_recent_ticket?.subject ?? "—"
                    }
                  />

                  <InfoRow
                    label="Common category"
                    value={summary?.common_category ?? "—"}
                  />

                  <InfoRow
                    label="Last ticket"
                    value={
                      summary?.latest_ticket_at
                        ? new Date(
                            summary.latest_ticket_at,
                          ).toLocaleString()
                        : "—"
                    }
                  />

                  <InfoRow
                    label="Last interaction"
                    value={
                      summary?.latest_interaction_at
                        ? new Date(
                            summary.latest_interaction_at,
                          ).toLocaleString()
                        : "—"
                    }
                  />
                </div>
              </section>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
