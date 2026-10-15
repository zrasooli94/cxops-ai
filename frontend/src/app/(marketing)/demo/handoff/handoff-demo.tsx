"use client";

import {
  ArrowLeft,
  ArrowRight,
  CheckCheck,
  CheckCircle2,
  Clock3,
  Inbox,
  MessageSquareText,
  RotateCcw,
  Search,
  Send,
  ShieldCheck,
  UserCheck,
  UserRoundMinus,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import {
  addDemoReply,
  filterDemoCases,
  freshDemoCases,
  summarizeDemo,
  transitionDemo,
  type DemoAction,
  type DemoFilter,
  type DemoPriority,
  type DemoStatus,
} from "@/lib/demo/handoff-simulator";

const FILTERS: ReadonlyArray<{ value: DemoFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "waiting", label: "Waiting" },
  { value: "assigned", label: "Assigned" },
  { value: "resolved", label: "Resolved" },
];

const STATUS_STYLE: Record<DemoStatus, string> = {
  waiting: "border-amber-200 bg-amber-50 text-amber-800",
  assigned: "border-blue-200 bg-blue-50 text-blue-800",
  resolved: "border-emerald-200 bg-emerald-50 text-emerald-800",
};

const PRIORITY_STYLE: Record<DemoPriority, string> = {
  urgent: "text-rose-700 bg-rose-50",
  high: "text-amber-800 bg-amber-50",
  normal: "text-slate-600 bg-slate-100",
};

const ACTION_LABEL: Record<DemoAction, string> = {
  assign: "Assigned to you",
  release: "Returned to waiting queue",
  resolve: "Resolved",
};

export default function HandoffDemo() {
  const [cases, setCases] = useState(freshDemoCases);
  const [filter, setFilter] = useState<DemoFilter>("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("DEMO-1042");
  const [draft, setDraft] = useState("");
  const [activity, setActivity] = useState<string[]>([]);

  const summary = useMemo(() => summarizeDemo(cases), [cases]);
  const visible = useMemo(() => filterDemoCases(cases, filter, search), [cases, filter, search]);
  const selected = visible.find((item) => item.id === selectedId) ?? visible[0] ?? null;

  function record(message: string) {
    setActivity((previous) => [message, ...previous].slice(0, 4));
  }

  function act(action: DemoAction) {
    if (!selected) return;
    const valid =
      (action === "assign" && selected.status === "waiting") ||
      ((action === "release" || action === "resolve") && selected.status === "assigned");
    if (!valid) return;
    setCases((previous) => transitionDemo(previous, selected.id, action));
    setDraft("");
    record(`${selected.id}: ${ACTION_LABEL[action]} (simulation only)`);
  }

  function sendReply() {
    if (!selected || selected.status !== "assigned") return;
    const body = draft.trim();
    if (!body || body.length > 500) return;
    setCases((previous) => addDemoReply(previous, selected.id, body));
    setDraft("");
    record(`${selected.id}: Simulated reply added to this demo`);
  }

  function reset() {
    setCases(freshDemoCases());
    setFilter("all");
    setSearch("");
    setSelectedId("DEMO-1042");
    setDraft("");
    setActivity([]);
  }

  return (
    <main className="min-h-screen bg-[#F7F8FC] px-4 py-9 text-slate-900 sm:px-6 lg:py-12">
      <div className="mx-auto max-w-[1320px]">
        <div className="mb-7 flex flex-wrap items-center justify-between gap-4">
          <div>
            <Link href="/" className="mb-4 inline-flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-violet-700">
              <ArrowLeft className="h-3.5 w-3.5" /> CXOps AI home
            </Link>
            <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.22em] text-violet-700">
              <ShieldCheck className="h-4 w-4" /> Phase 1V · Interactive product preview
            </div>
            <h1 className="text-3xl font-semibold tracking-[-0.045em] text-slate-950 sm:text-4xl">Human handoff workspace</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
              Try the staff workflow: select a conversation, assign it, send a sample reply, then resolve it. Watch the dashboard update.
            </p>
          </div>
          <button
            type="button"
            onClick={reset}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-violet-300"
          >
            <RotateCcw className="h-4 w-4" /> Reset demo
          </button>
        </div>

        <div role="note" className="mb-6 flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950">
          <ShieldCheck className="mt-1 h-4 w-4 shrink-0" />
          <div><strong>INTERACTIVE DEMO — FICTIONAL DATA.</strong> All actions run in this browser tab only. Nothing is sent, stored, assigned, or resolved in real CXOps AI. No sign-in or production API access is used.</div>
        </div>

        <section aria-label="Demo queue statistics" className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-amber-700"><Inbox className="h-4 w-4" /> Waiting</div>
            <p className="text-4xl font-semibold tabular-nums tracking-tight">{summary.waiting}</p>
            <p className="mt-2 text-xs text-slate-500">{summary.urgentWaiting} urgent waiting</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-violet-700"><Users className="h-4 w-4" /> Assigned</div>
            <p className="text-4xl font-semibold tabular-nums tracking-tight">{summary.assigned}</p>
            <p className="mt-2 text-xs text-slate-500">Working with a team member</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-emerald-700"><CheckCircle2 className="h-4 w-4" /> Resolved</div>
            <p className="text-4xl font-semibold tabular-nums tracking-tight">{summary.resolved}</p>
            <p className="mt-2 text-xs text-slate-500">Completed in this simulation</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-slate-600"><Clock3 className="h-4 w-4" /> Oldest waiting</div>
            <p className="text-4xl font-semibold tabular-nums tracking-tight">{summary.oldestWaitingMinutes === null ? "—" : `${summary.oldestWaitingMinutes}m`}</p>
            <p className="mt-2 text-xs text-slate-500">Illustrative, not a live clock</p>
          </div>
        </section>

        <div className="grid items-start gap-5 lg:grid-cols-[minmax(300px,0.9fr)_minmax(390px,1.3fr)]">
          <section aria-label="Demo conversations" className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-100 p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold">Conversation queue</h2>
                  <p className="mt-1 text-xs text-slate-500">Select a fictional customer to inspect</p>
                </div>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">{visible.length} showing</span>
              </div>
              <label className="relative block">
                <Search className="pointer-events-none absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
                <span className="sr-only">Search demo conversations</span>
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search subjects or case IDs"
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 py-3 pl-10 pr-3 text-sm outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100"
                />
              </label>
              <div role="group" aria-label="Filter by conversation status" className="mt-4 flex flex-wrap gap-1.5">
                {FILTERS.map((item) => (
                  <button type="button" key={item.value} onClick={() => setFilter(item.value)}
                    aria-pressed={filter === item.value}
                    className={`rounded-lg px-3 py-2 text-xs font-semibold transition ${filter === item.value
                      ? "bg-slate-900 text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                  >{item.label}</button>
                ))}
              </div>
            </div>
            <div className="max-h-[670px] overflow-y-auto p-2">
              {visible.length === 0 && (
                <div className="px-4 py-16 text-center text-sm text-slate-500">
                  No demo conversations match your search.
                </div>
              )}
              {visible.map((item) => (
                <button type="button" key={item.id} onClick={() => { setSelectedId(item.id); setDraft(""); }}
                  aria-pressed={selected?.id === item.id}
                  className={`my-1 w-full rounded-xl border p-4 text-left transition focus-visible:outline-2 focus-visible:outline-violet-600 ${selected?.id === item.id
                    ? "border-violet-300 bg-violet-50 shadow-sm"
                    : "border-transparent bg-white hover:border-slate-200 hover:bg-slate-50"}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-slate-500">{item.id}</span>
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold capitalize ${STATUS_STYLE[item.status]}`}>{item.status}</span>
                  </div>
                  <h3 className="mt-2 text-sm font-semibold text-slate-950">{item.topic}</h3>
                  <p className="mt-1 text-xs text-slate-500">{item.visitor} · {item.channel}</p>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <span className={`rounded-md px-2 py-1 text-[11px] font-semibold capitalize ${PRIORITY_STYLE[item.priority]}`}>{item.priority} priority</span>
                    <span className="text-xs text-slate-400">{item.ageMinutes}m ago <ArrowRight className="ml-1 inline h-3 w-3" /></span>
                  </div>
                </button>
              ))}
            </div>
          </section>

          <section aria-label="Demo conversation details" className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            {selected ? (
              <>
                <div className="border-b border-slate-100 p-5 sm:p-6">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <span className="text-xs font-semibold uppercase tracking-[0.13em] text-violet-700">Selected case · {selected.id}</span>
                    <span className={`rounded-full border px-3 py-1 text-xs font-semibold capitalize ${STATUS_STYLE[selected.status]}`}>{selected.status}</span>
                  </div>
                  <h2 className="mt-3 text-xl font-semibold text-slate-950">{selected.topic}</h2>
                  <p className="mt-1 text-xs text-slate-500">{selected.visitor} · {selected.channel} · Owner: {selected.owner ?? "Unassigned"}</p>
                </div>

                <div aria-label="Simulated message history" className="max-h-[410px] min-h-[250px] space-y-4 overflow-y-auto bg-[#FAFBFE] px-5 py-6 sm:px-6">
                  {selected.messages.map((message) => (
                    <div key={message.id} className={`flex ${message.from === "staff" ? "justify-end" : "justify-start"}`}>
                      <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${message.from === "staff"
                        ? "bg-violet-700 text-white"
                        : message.from === "system"
                          ? "border border-slate-200 bg-slate-100 text-slate-600"
                          : "border border-slate-200 bg-white text-slate-800"}`}>
                        <p className={`mb-1 text-[11px] font-semibold ${message.from === "staff" ? "text-violet-200" : "text-slate-500"}`}>
                          {message.from === "staff" ? "Staff · simulated" : message.from === "system" ? "System · simulated" : selected.visitor}
                        </p>
                        <p className="whitespace-pre-wrap break-words">{message.body}</p>
                        <p className={`mt-2 text-right text-[10px] ${message.from === "staff" ? "text-violet-200" : "text-slate-400"}`}>{message.time}</p>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="border-t border-slate-100 px-5 py-4 sm:px-6">
                  <p className="mb-3 text-xs font-semibold uppercase tracking-[0.13em] text-slate-500">Simulated staff actions</p>
                  <div className="flex flex-wrap gap-2">
                    {selected.status === "waiting" && (
                      <button type="button" onClick={() => act("assign")}
                        className="inline-flex items-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-violet-800">
                        <UserCheck className="h-4 w-4" /> Assign to me
                      </button>
                    )}
                    {selected.status === "assigned" && (
                      <>
                        <button type="button" onClick={() => act("resolve")}
                          className="inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-800">
                          <CheckCheck className="h-4 w-4" /> Resolve case
                        </button>
                        <button type="button" onClick={() => act("release")}
                          className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">
                          <UserRoundMinus className="h-4 w-4" /> Return to queue
                        </button>
                      </>
                    )}
                    {selected.status === "resolved" && <span className="inline-flex items-center gap-2 text-sm font-medium text-emerald-700"><CheckCircle2 className="h-4 w-4" /> Resolved (demo only)</span>}
                  </div>
                  <form onSubmit={(event) => { event.preventDefault(); sendReply(); }} className="mt-5">
                    <label htmlFor="demo-reply" className="mb-2 block text-sm font-medium text-slate-700">Write a simulated reply</label>
                    <textarea
                      id="demo-reply"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      rows={3}
                      maxLength={500}
                      disabled={selected.status !== "assigned"}
                      placeholder={selected.status === "waiting" ? "Assign the case to enable replying…" : selected.status === "resolved" ? "This demo case is resolved." : "Type a sample response…"}
                      className="w-full resize-y rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100 disabled:cursor-not-allowed disabled:opacity-60"
                    />
                    <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-xs text-slate-500">Nothing is delivered or saved · {draft.length}/500</span>
                      <button type="submit" disabled={selected.status !== "assigned" || !draft.trim()}
                        className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40">
                        <Send className="h-4 w-4" /> Send sample reply
                      </button>
                    </div>
                  </form>
                </div>
              </>
            ) : (
              <div className="flex min-h-[430px] flex-col items-center justify-center gap-3 px-6 text-center text-sm text-slate-500">
                <MessageSquareText className="h-8 w-8 text-slate-300" />
                Choose a filter or clear search to view a demo conversation.
              </div>
            )}
          </section>
        </div>

        <div className="mt-6 grid gap-5 lg:grid-cols-2">
          <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-base font-semibold text-slate-950">Try the full handoff flow</h2>
            <ol className="mt-3 space-y-2 text-sm leading-6 text-slate-600">
              <li>1. Select <strong>DEMO-1042</strong> in the waiting queue.</li>
              <li>2. Choose <strong>Assign to me</strong>.</li>
              <li>3. Write and send a sample reply.</li>
              <li>4. Resolve the case and watch the queue totals update.</li>
            </ol>
          </section>
          <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-center gap-2"><CheckCircle2 className="h-5 w-5 text-violet-700" /><h2 className="text-base font-semibold">Simulation activity</h2></div>
            <div role="status" aria-live="polite" aria-atomic="true" className="mt-3 space-y-2 text-sm text-slate-600">
              {activity.length ? activity.map((entry, index) => (
                <p key={`${index}-${entry}`} className="rounded-lg bg-slate-50 px-3 py-2">{entry}</p>
              )) : <p>No demo actions yet. Select a conversation to begin.</p>}
            </div>
          </section>
        </div>
        <p className="mt-7 text-center text-xs leading-6 text-slate-500">
          This simulation is a product design preview. Real staff actions require an authenticated, isolated environment and backend authorization checks.
        </p>
      </div>
    </main>
  );
}
