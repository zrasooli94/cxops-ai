import Link from "next/link";
import { Ticket, Bot, ShieldCheck, BrainCircuit, Workflow, Activity } from "lucide-react";

const navCards = [
  { href: "/tickets", label: "Tickets", desc: "Customer support workspace", icon: Ticket },
  { href: "/agent", label: "AI Agent", desc: "LangGraph execution console", icon: Bot },
  { href: "/approvals", label: "Approvals", desc: "Human-in-the-loop safety", icon: ShieldCheck },
  { href: "/knowledge", label: "Knowledge", desc: "RAG playground & ingestion", icon: BrainCircuit },
  { href: "/runs", label: "Runs", desc: "Persistent audit trail", icon: Workflow },
  { href: "/observability", label: "Observability", desc: "Production telemetry", icon: Activity },
];

export default function DashboardPage() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                Control Center
              </p>
              <h1 className="mt-2 text-3xl font-light tracking-[-0.04em] text-slate-950">
                Dashboard
              </h1>
            </div>
          </div>
        </header>

        <div className="mx-auto max-w-[1450px] px-6 pt-[112px] pb-16 lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Operations Overview
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Control Center Dashboard
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Navigate to operational workspaces from here. All pages remain within the
              authenticated Control Center.
            </p>
          </section>

          <section className="mb-12 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {navCards.map((card) => {
              const Icon = card.icon;
              return (
                <Link
                  key={card.href}
                  href={card.href}
                  className="rounded-2xl border border-slate-200 bg-white p-6 transition hover:border-violet-300 hover:-translate-y-0.5 hover:shadow-lg"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-50 text-violet-500">
                      <Icon className="h-5 w-5" strokeWidth={1.7} />
                    </div>
                    <div>
                      <h3 className="font-semibold text-slate-950">{card.label}</h3>
                      <p className="text-xs text-slate-400">{card.desc}</p>
                    </div>
                  </div>

                  <div className="mt-5 text-sm font-medium text-violet-600">
                    Open workspace <span aria-hidden="true">→</span>
                  </div>
                </Link>
              );
            })}
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-6 lg:p-8">
            <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-600">
                  Control Center
                </p>
                <h2 className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-slate-950">
                  All operations in one authenticated workspace
                </h2>
                <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
                  The CXOps Control Center keeps support tickets, AI agent decisions,
                  RAG knowledge, human approvals, execution audit trails, and
                  operational observability in one authenticated, audit-ready workflow.
                </p>
              </div>

              <div className="flex items-center gap-2 rounded-full border border-violet-200 bg-violet-50/60 px-4 py-2 text-xs font-medium text-violet-600">
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
                Authenticated session active
              </div>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}