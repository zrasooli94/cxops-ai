import Link from "next/link";
import { Bot, BrainCircuit, ChevronRight, Gauge, ShieldCheck, Ticket, Workflow } from "lucide-react";

const features = [
  { icon: Ticket, title: "Customer Support Tickets", desc: "Real support cases as the foundation for AI-assisted workflows.", href: "/tickets" },
  { icon: BrainCircuit, title: "RAG-Assisted Knowledge", desc: "Semantic retrieval grounds AI decisions in policy evidence.", href: "/knowledge" },
  { icon: Bot, title: "AI Agent Decisions", desc: "LangGraph pipeline analyzes cases and proposes grounded actions.", href: "/agent" },
  { icon: ShieldCheck, title: "Risk & Approval Controls", desc: "Human review gates before any external execution.", href: "/approvals" },
  { icon: Workflow, title: "Execution Audit Trail", desc: "Every decision, path, and outcome persists for inspection.", href: "/runs" },
  { icon: Gauge, title: "Operational Observability", desc: "AI quality, grounding, latency, cost, and ROI telemetry.", href: "/observability" },
];

export default function HomePage() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl">
        <div className="mx-auto flex h-[74px] max-w-6xl items-center justify-between px-6 lg:px-10">
          <Link href="/" className="flex items-center gap-3">
            <div className="grid h-9 w-9 grid-cols-3 gap-[3px]">
              {Array.from({ length: 9 }).map((_, index) => (
                <span
                  key={index}
                  className={`rounded-full ${
                    index % 2 === 0
                      ? "bg-[#7357ff]"
                      : "bg-[#42a5ff]"
                  }`}
                />
              ))}
            </div>
            <div>
              <p className="text-[15px] font-semibold tracking-[-0.03em] text-slate-950">
                CXOps AI
              </p>
            </div>
          </Link>

          <nav className="hidden md:flex items-center gap-6">
            <Link href="/platform" className="text-sm font-medium text-slate-600 hover:text-slate-950 transition">
              Platform
            </Link>
            <Link href="/login" className="rounded-xl bg-slate-950 px-5 py-2.5 text-sm font-medium text-white shadow-[0_8px_22px_rgba(17,24,39,0.12)] transition hover:-translate-y-0.5">
              Sign in
            </Link>
          </nav>
        </div>
      </header>

      <div className="pt-[112px] pb-24">
        <div className="mx-auto max-w-6xl px-6 lg:px-10">
          <div className="max-w-4xl text-center mx-auto mb-16">
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-violet-600">
              CXOps AI Platform
            </p>

            <h1 className="mt-5 text-4xl font-semibold tracking-[-0.04em] sm:text-5xl lg:text-6xl">
              Test support-ticket AI workflows before controlled execution
            </h1>

            <p className="mt-7 max-w-3xl text-lg leading-8 text-slate-600 mx-auto">
              CXOps AI is a demonstration customer experience operations platform that brings support tickets, RAG-assisted knowledge retrieval, AI agent decisions, risk controls, human approvals, execution audit trails, and operational observability into one workflow.
            </p>

            <p className="mt-5 max-w-3xl text-base leading-7 text-slate-600 mx-auto">
              It is designed as a portfolio and testing environment for examining how an AI-assisted support workflow can retrieve grounded knowledge, propose actions, identify risky operations, require human approval when appropriate, and record what happened during execution.
            </p>

            <div className="mt-10 flex flex-wrap gap-3 justify-center">
              <Link href="/login" className="rounded-xl bg-slate-950 px-6 py-3 text-sm font-medium text-white shadow-[0_8px_22px_rgba(17,24,39,0.12)] transition hover:-translate-y-0.5">
                Access Control Center
              </Link>

              <Link href="/platform" className="rounded-xl border border-slate-300 bg-white px-6 py-3 text-sm font-medium text-slate-800 transition hover:border-violet-300">
                Explore Platform
              </Link>
            </div>
          </div>

          <section className="mt-20">
            <h2 className="text-2xl font-semibold tracking-[-0.025em] text-center">
              How the CXOps AI workflow fits together
            </h2>

            <div className="mt-8 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {features.map((feature) => (
                <Link key={feature.href} href={feature.href} className="rounded-2xl border border-slate-200 bg-white p-6 transition hover:border-violet-300">
                  <div className="flex items-center gap-3">
                    <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-50 text-violet-500">
                      <feature.icon className="h-5 w-5" strokeWidth={1.7} />
                    </div>
                    <h3 className="font-semibold text-slate-950">{feature.title}</h3>
                  </div>

                  <p className="mt-3 text-sm leading-6 text-slate-600">{feature.desc}</p>

                  <div className="mt-5 text-sm font-medium text-violet-600">
                    Explore workflow <ChevronRight className="inline h-4 w-4" />
                  </div>
                </Link>
              ))}
            </div>
          </section>

          <section className="mt-20 rounded-3xl border border-slate-200 bg-white p-7 lg:p-10">
            <h2 className="text-2xl font-semibold tracking-[-0.025em] text-center">
              Can CXOps AI test a RAG and approval workflow before execution?
            </h2>

            <p className="mt-5 max-w-4xl leading-7 text-slate-600 mx-auto text-center">
              Yes. Within this demonstration environment, a support case can move through knowledge retrieval, AI-assisted decision making, risk-aware tool planning, human approval when required, controlled execution, and a persistent audit trail.
            </p>

            <p className="mt-4 max-w-4xl leading-7 text-slate-600 mx-auto text-center">
              The purpose is to make the intermediate stages visible so developers and operations teams can inspect what evidence was retrieved, what the agent proposed, whether approval was required, and what execution outcome was recorded.
            </p>
          </section>

          <section className="mt-20 rounded-3xl border border-violet-200 bg-violet-50/60 p-7 lg:p-10 text-center">
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-violet-600">
              Evaluation guide
            </p>

            <h2 className="mt-4 text-2xl font-semibold tracking-[-0.025em]">
              Compare controls before allowing AI agents to take high-impact actions
            </h2>

            <p className="mt-5 max-w-4xl leading-7 text-slate-600 mx-auto">
              Our evidence-backed guide explains how to evaluate authorization, human approval, policy enforcement, auditability, escalation, observability, and recovery boundaries across customer-support AI platforms without assuming that every vendor exposes the same controls.
            </p>

            <Link href="/platform/evaluating-ai-support-platforms" className="mt-7 inline-flex rounded-xl bg-violet-600 px-5 py-3 text-sm font-medium text-white transition hover:bg-violet-700">
              Read the high-risk action evaluation guide
            </Link>
          </section>

          <footer className="mt-20 border-t border-slate-200 pt-12">
            <div className="mx-auto max-w-4xl text-center">
              <p className="text-sm text-slate-500 mb-6">
                CXOps AI is a demonstration platform for testing customer-experience AI workflows.
              </p>
              <div className="flex flex-wrap gap-x-6 gap-y-3 text-sm justify-center text-slate-600">
                <Link href="/platform" className="hover:text-violet-600">Platform</Link>
                <Link href="/tickets" className="hover:text-violet-600">Tickets</Link>
                <Link href="/knowledge" className="hover:text-violet-600">Knowledge</Link>
                <Link href="/agent" className="hover:text-violet-600">AI Agent</Link>
                <Link href="/approvals" className="hover:text-violet-600">Approvals</Link>
                <Link href="/runs" className="hover:text-violet-600">Runs</Link>
                <Link href="/observability" className="hover:text-violet-600">Observability</Link>
                <Link href="/login" className="hover:text-violet-600 font-medium">Sign in</Link>
              </div>
              <p className="mt-6 text-xs text-slate-400">
                CXOps AI &copy; 2025 &mdash; Demonstration Platform
              </p>
            </div>
          </footer>
        </div>
      </div>
    </main>
  );
}