import Link from "next/link";

export const metadata = {
  title: "Human Handoff Dashboard Demo | CXOps AI",
  robots: { index: false, follow: false },
};

const SAMPLE = {
  waiting: 3,
  assigned: 2,
  oldestWaitingMinutes: 21,
  needsAttention: true,
};

export default function HandoffDemoPage() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-violet-50 px-5 py-12 text-slate-900">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-violet-700">CXOps AI · Feature preview</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Human handoff queue</h1>
            <p className="mt-2 text-sm text-slate-500">A preview of Phase 1V staff operations, without signing in.</p>
          </div>
          <Link href="/login" className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold hover:border-violet-400">
            Control Center sign in →
          </Link>
        </div>
        <div role="note" className="mb-7 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <strong>DEMO — SYNTHETIC DATA ONLY.</strong> These numbers are illustrative, not connected to RISPU or any live customer account. No API, customer data or authentication is used on this page.
        </div>
        <section aria-label="Example human handoff queue" className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold">Human handoff queue</h2>
              <p className="mt-2 text-sm text-slate-500">Example of the authenticated dashboard widget</p>
            </div>
            <span className="rounded-full bg-violet-100 px-3 py-1 text-xs font-semibold text-violet-700">Preview only</span>
          </div>
          <div className="mt-7 grid gap-4 sm:grid-cols-3">
            <div className="rounded-2xl bg-amber-50 p-6">
              <p className="text-sm font-medium text-amber-900">Waiting for staff</p>
              <p className="mt-3 text-5xl font-semibold tracking-tight text-amber-950">{SAMPLE.waiting}</p>
            </div>
            <div className="rounded-2xl bg-violet-50 p-6">
              <p className="text-sm font-medium text-violet-900">Assigned conversations</p>
              <p className="mt-3 text-5xl font-semibold tracking-tight text-violet-950">{SAMPLE.assigned}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 p-6">
              <p className="text-sm font-medium text-slate-700">Oldest waiting time</p>
              <p className="mt-3 text-5xl font-semibold tracking-tight text-slate-950">{SAMPLE.oldestWaitingMinutes}m</p>
            </div>
          </div>
          {SAMPLE.needsAttention && (
            <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm font-medium text-amber-900">
              Queue requires attention — staff should review unassigned handoffs.
            </div>
          )}
        </section>
        <p className="mt-6 text-sm leading-6 text-slate-500">
          In the actual Control Center, the widget reads tenant-scoped counts from the authenticated backend. This demonstration intentionally does not connect to that backend.
        </p>
      </div>
    </main>
  );
}
