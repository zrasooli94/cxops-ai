import type { Metadata } from "next";
import type { ComponentType, ReactNode } from "react";
import Link from "next/link";
import { redirect } from "next/navigation";
import {
  Activity, ArrowLeft, ArrowUpRight, CheckCircle2, Clock3, Inbox,
  LockKeyhole, ShieldCheck, Ticket, UserRound, Users,
} from "lucide-react";

import {
  neonStagingEnabled, STAGING_BACKEND_URL, stagingIdentity, stagingSubjects,
} from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";
import {
  parseStagingCustomers, parseStagingOrganization, parseStagingQueue,
  parseStagingTickets, stagingTicketMetrics,
} from "@/lib/staging/operations-contract";
import { stagingSignOut } from "../actions";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Staging Control Center | CXOps AI",
  robots: { index: false, follow: false },
};

function Notification({ children }: { children: ReactNode }) {
  return <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm leading-6 text-amber-950">{children}</div>;
}

function StateCard({
  title, value, note, icon: Icon,
}: {
  title: string; value: number | string; note: string;
  icon: ComponentType<{ className?: string }>;
}) {
  return <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
    <div className="flex items-center justify-between">
      <span className="text-xs font-semibold uppercase tracking-widest text-slate-500">{title}</span>
      <Icon className="h-5 w-5 text-violet-600" />
    </div>
    <p className="mt-4 text-4xl font-semibold tracking-[-0.04em] tabular-nums text-slate-950">{value}</p>
    <p className="mt-2 text-xs text-slate-500">{note}</p>
  </div>;
}

function badgeForStatus(status: string) {
  if (status === "solved" || status === "closed") return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (status === "open" || status === "pending") return "bg-amber-50 text-amber-800 border-amber-200";
  return "bg-blue-50 text-blue-800 border-blue-200";
}

function badgeForPriority(priority: string) {
  if (priority === "urgent") return "bg-rose-50 text-rose-700 border-rose-200";
  if (priority === "high") return "bg-orange-50 text-orange-800 border-orange-200";
  return "bg-slate-50 text-slate-600 border-slate-200";
}

/** Explicitly trusted staging HTTPS origin. Never accept a client-supplied URL. */
async function readStagingEndpoint(path: string, token: string, tenant: number) {
  const response = await fetch(STAGING_BACKEND_URL + path, {
    headers: {
      Authorization: "Bearer " + token,
      ...(tenant > 0 ? { "X-CXOps-Organization-ID": String(tenant) } : {}),
      Accept: "application/json",
    },
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("Staging API refused read");
  return (await response.json()) as unknown;
}

function UnavailableWorkspace({ message }: { message: string }) {
  return <main className="min-h-screen bg-slate-50 px-5 py-20">
    <section className="mx-auto max-w-xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
      <ShieldCheck className="h-8 w-8 text-violet-600" />
      <h1 className="mt-5 text-2xl font-semibold text-slate-950">Staging Control Center</h1>
      <div className="mt-5"><Notification>{message}</Notification></div>
      <Link href="/staging" className="mt-6 inline-flex items-center gap-2 text-sm font-semibold text-violet-700">
        <ArrowLeft className="h-4 w-4" /> Back to staging
      </Link>
    </section>
  </main>;
}

export default async function StagingOperationsPage() {
  if (!neonStagingEnabled(process.env)) {
    return <UnavailableWorkspace message="Staging is not enabled. No production account or data is accessible here." />;
  }

  let identity;
  try {
    identity = await stagingIdentity(stagingAuth(), stagingSubjects(process.env));
  } catch {
    return <UnavailableWorkspace message="Staging identity is temporarily unavailable. No workspace information was loaded." />;
  }
  if (!identity) redirect("/staging/login");

  const token = identity.token; // server-only: never returned to any client component
  let organization;
  try {
    const memberships = await readStagingEndpoint("/me/organizations", token, 0);
    organization = parseStagingOrganization(memberships);
  } catch {
    return <UnavailableWorkspace message="Unable to independently verify the synthetic organization. Read-only access remains blocked." />;
  }

  const [ticketsResult, customersResult, queueResult] = await Promise.allSettled([
    readStagingEndpoint("/tickets?limit=50", token, organization.id)
      .then(value => parseStagingTickets(value, organization.id)),
    readStagingEndpoint("/customers?limit=50", token, organization.id)
      .then(value => parseStagingCustomers(value, organization.id)),
    readStagingEndpoint("/staff/public-chat/summary?window=24h", token, organization.id)
      .then(value => parseStagingQueue(value, organization.id)),
  ]);

  const tickets = ticketsResult.status === "fulfilled" ? ticketsResult.value : null;
  const customers = customersResult.status === "fulfilled" ? customersResult.value : null;
  const queue = queueResult.status === "fulfilled" ? queueResult.value : null;
  const metrics = tickets ? stagingTicketMetrics(tickets) : null;
  const customerNames = new Map((customers ?? []).map(c => [c.id, c.name]));
  const dataUnavailable = tickets === null || customers === null || queue === null;

  return <main className="min-h-screen bg-[#F7F8FC] text-slate-950">
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-[1250px] flex-wrap items-center justify-between gap-4 px-5 py-5 sm:px-7">
        <Link href="/staging" className="inline-flex items-center gap-3">
          <span className="grid h-9 w-9 grid-cols-3 gap-1">
            {Array.from({ length: 9 }).map((_, i) => <span key={i} className={`rounded-[5px] ${i % 2 ? "bg-blue-400" : "bg-violet-600"}`} />)}
          </span>
          <span className="text-base font-semibold tracking-tight">CXOps AI <span className="ml-1 text-xs font-medium text-violet-700">STAGING</span></span>
        </Link>
        <nav aria-label="Staging navigation" className="flex items-center gap-4">
          <Link href="/staging" className="text-sm font-medium text-slate-600 hover:text-violet-700">Workspace home</Link>
          <form action={stagingSignOut}><button className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-semibold hover:bg-slate-50">Sign out</button></form>
        </nav>
      </div>
    </header>

    <div className="mx-auto max-w-[1250px] px-5 py-9 sm:px-7 lg:py-12">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-violet-700">
            <ShieldCheck className="h-4 w-4" /> Phase 1X · Verified synthetic data
          </div>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.045em] sm:text-4xl">Control Center overview</h1>
          <p className="mt-2 text-sm text-slate-500">{organization.name} · signed in as {identity.user.name}</p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-emerald-200 bg-white px-4 py-2 text-xs font-semibold text-emerald-700">
          <LockKeyhole className="h-4 w-4" /> Read-only staging
        </div>
      </div>

      <div className="mt-7 flex gap-3 rounded-xl border border-violet-200 bg-violet-50 px-4 py-4 text-sm leading-6 text-violet-900">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" />
        <p><strong>Real authenticated API, fictional records.</strong> Everything below is fetched through your tenant-authorized staging service. No live customers, production tokens, payments, integrations, AI calls or staff write actions are involved.</p>
      </div>

      {dataUnavailable && <div className="mt-5"><Notification>Some staging information could not be verified. Unavailable values are shown as dashes, not as zero. Please refresh later.</Notification></div>}

      <section className="mt-8">
        <h2 className="mb-4 text-lg font-semibold">Operations at a glance</h2>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StateCard title="Active tickets" value={metrics?.active ?? "—"} note="New, open and pending" icon={Ticket} />
          <StateCard title="Test customers" value={customers?.length ?? "—"} note="Synthetic accounts only" icon={Users} />
          <StateCard title="Urgent tickets" value={metrics?.urgent ?? "—"} note="Open urgent priorities" icon={Activity} />
          <StateCard title="Resolved tickets" value={metrics?.resolved ?? "—"} note="Solved or closed" icon={CheckCircle2} />
        </div>
      </section>

      <div className="mt-7 grid items-start gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(280px,0.85fr)]">
        <section id="tickets" className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-5 sm:px-6">
            <div>
              <h2 className="text-lg font-semibold">Support tickets</h2>
              <p className="mt-1 text-xs text-slate-500">Read-only · from tenant-scoped /tickets</p>
            </div>
            <span className="rounded-full bg-violet-50 px-3 py-1 text-xs font-semibold text-violet-700">{metrics?.total ?? "—"} total</span>
          </div>
          {tickets === null ? <p role="alert" className="p-6 text-sm text-rose-700">Ticket data unavailable. Access remains closed.</p> :
            tickets.length === 0 ? <p className="p-6 text-sm text-slate-500">No synthetic tickets for this workshop.</p> :
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left">
                <thead><tr className="border-b border-slate-100 bg-slate-50/80 text-[11px] font-bold uppercase tracking-wide text-slate-500">
                  <th scope="col" className="px-6 py-3">Case</th>
                  <th scope="col" className="px-4 py-3">Customer</th>
                  <th scope="col" className="px-4 py-3">Priority</th>
                  <th scope="col" className="px-5 py-3">Status</th>
                </tr></thead>
                <tbody>
                  {tickets.map(ticket => <tr key={ticket.id} className="border-b border-slate-100 last:border-b-0">
                    <td className="px-6 py-4 align-top">
                      <p className="text-sm font-semibold text-slate-900">{ticket.subject}</p>
                      <p className="mt-1 text-xs text-slate-400">{ticket.external_id}</p>
                    </td>
                    <td className="px-4 py-4 align-top text-sm text-slate-600">{ticket.customer_id ? customerNames.get(ticket.customer_id) ?? "Synthetic customer" : "Unlinked"}</td>
                    <td className="px-4 py-4 align-top"><span className={`rounded-full border px-2.5 py-1 text-xs font-medium capitalize ${badgeForPriority(ticket.priority)}`}>{ticket.priority}</span></td>
                    <td className="px-5 py-4 align-top"><span className={`rounded-full border px-2.5 py-1 text-xs font-medium capitalize ${badgeForStatus(ticket.status)}`}>{ticket.status}</span></td>
                  </tr>)}
                </tbody>
              </table>
            </div>}
        </section>

        <div className="space-y-6">
          <section id="handoffs" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-center gap-2"><Inbox className="h-5 w-5 text-violet-600" /><h2 className="text-lg font-semibold">Human handoffs</h2></div>
            <p className="mt-2 text-xs text-slate-500">Real staging queue · last 24h view</p>
            {queue ? <>
              <div className="mt-5 grid grid-cols-2 gap-3">
                <div className="rounded-xl bg-amber-50 p-4"><p className="text-xs text-amber-800">Waiting</p><p className="mt-1 text-3xl font-semibold text-amber-950">{queue.waiting}</p></div>
                <div className="rounded-xl bg-violet-50 p-4"><p className="text-xs text-violet-800">Assigned</p><p className="mt-1 text-3xl font-semibold text-violet-950">{queue.assigned}</p></div>
              </div>
              <p className="mt-4 flex items-center gap-2 text-xs text-slate-500"><Clock3 className="h-4 w-4" /> Oldest waiting: {queue.oldestWaitingMinutes === null ? "—" : queue.oldestWaitingMinutes + " min"}</p>
              {queue.attention ? <Notification>Staging queue needs attention.</Notification> : <p className="mt-3 text-xs text-emerald-700">No queue attention signal</p>}
            </> : <p role="alert" className="mt-5 text-sm text-rose-700">Handoff data unavailable.</p>}
          </section>

          <section id="customers" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-center gap-2"><UserRound className="h-5 w-5 text-violet-600" /><h2 className="text-lg font-semibold">Test customers</h2></div>
            <p className="mt-2 text-xs text-slate-500">Verified fictional customer records</p>
            {customers ? <div className="mt-5 divide-y divide-slate-100">
              {customers.map(person => <div key={person.id} className="py-3 first:pt-0 last:pb-0">
                <p className="text-sm font-medium text-slate-900">{person.name}</p>
                <p className="mt-1 text-xs text-slate-500">{person.email}</p>
              </div>)}
              {customers.length === 0 && <p className="text-sm text-slate-500">No fictional customers created.</p>}
            </div> : <p role="alert" className="mt-5 text-sm text-rose-700">Customer data unavailable.</p>}
          </section>
        </div>
      </div>

      <section className="mt-7 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-6 py-5">
        <div>
          <p className="text-sm font-semibold text-slate-900">Next: staff assignment and resolution</p>
          <p className="mt-1 text-xs text-slate-500">These operations remain disabled until separately tested and reviewed.</p>
        </div>
        <span className="inline-flex items-center gap-2 text-xs font-semibold text-slate-400">Write actions unavailable <ArrowUpRight className="h-4 w-4" /></span>
      </section>
    </div>
  </main>;
}
