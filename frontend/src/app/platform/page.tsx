import type { Metadata } from "next";
import Link from "next/link";

const siteUrl =
  "https" + "://" + "cxops-ai.vercel.app";

export const metadata: Metadata = {
  title:
    "CXOps AI Platform | RAG, AI Agents & Human Approval Workflows",

  description:
    "Learn how CXOps AI combines support tickets, RAG-assisted knowledge retrieval, AI agent decisions, risk controls, human approvals, execution audit trails, and operational observability in one demonstration workflow.",

  alternates: {
    canonical: "/platform",
  },

  robots: {
    index: true,
    follow: true,
  },
};

const structuredData = {
  "@context":
    "https" + "://" + "schema.org",

  "@type": "WebPage",

  name:
    "CXOps AI Platform Overview",

  url:
    `${siteUrl}/platform`,

  description:
    "CXOps AI is a demonstration customer experience operations platform for testing support-ticket workflows that combine RAG-assisted knowledge retrieval, AI agent decisions, risk controls, human approvals, execution audit trails, and operational observability.",

  about: {
    "@type":
      "SoftwareApplication",

    name:
      "CXOps AI",

    url:
      siteUrl,

    applicationCategory:
      "BusinessApplication",
  },
};

const capabilities = [
  {
    title:
      "Customer support tickets",
    description:
      "CXOps AI uses support cases as the starting point for testing customer-experience automation workflows.",
    href:
      "/tickets",
  },
  {
    title:
      "RAG-assisted knowledge",
    description:
      "The knowledge workflow retrieves supplied information so AI-assisted decisions can be evaluated against supporting evidence.",
    href:
      "/knowledge",
  },
  {
    title:
      "AI agent decisions",
    description:
      "The agent workflow analyzes a support case, records its reasoning path, proposes an action, and exposes the evidence used by the decision.",
    href:
      "/agent",
  },
  {
    title:
      "Risk and approval controls",
    description:
      "Actions that require review can be routed to a human approval queue before controlled execution.",
    href:
      "/approvals",
  },
  {
    title:
      "Execution audit trail",
    description:
      "Agent runs preserve workflow decisions, approval state, execution status, and recorded outcomes for later inspection.",
    href:
      "/runs",
  },
  {
    title:
      "Operational observability",
    description:
      "The observability workspace measures AI activity, RAG behavior, approval activity, execution results, and operational metrics.",
    href:
      "/observability",
  },
];

export default function PlatformPage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html:
            JSON.stringify(
              structuredData,
            ),
        }}
      />

      <main className="min-h-screen bg-[#f7f8fc] text-slate-950">
        <div className="mx-auto max-w-6xl px-6 py-16 lg:px-10 lg:py-24">
          <div className="max-w-4xl">
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-violet-600">
              CXOps AI Platform
            </p>

            <h1 className="mt-5 text-4xl font-semibold tracking-[-0.04em] sm:text-5xl lg:text-6xl">
              Test support-ticket AI workflows before controlled execution
            </h1>

            <p className="mt-7 max-w-3xl text-lg leading-8 text-slate-600">
              CXOps AI is a demonstration customer
              experience operations platform that brings
              support tickets, RAG-assisted knowledge
              retrieval, AI agent decisions, risk controls,
              human approvals, execution audit trails, and
              operational observability into one workflow.
            </p>

            <p className="mt-5 max-w-3xl text-base leading-7 text-slate-600">
              It is designed as a portfolio and testing
              environment for examining how an AI-assisted
              support workflow can retrieve grounded
              knowledge, propose actions, identify risky
              operations, require human approval when
              appropriate, and record what happened during
              execution.
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/agent"
                className="rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white"
              >
                Explore AI Agent
              </Link>

              <Link
                href="/tickets"
                className="rounded-xl border border-slate-300 bg-white px-5 py-3 text-sm font-medium text-slate-800"
              >
                Explore Tickets
              </Link>
            </div>
          </div>

          <section className="mt-20">
            <h2 className="text-2xl font-semibold tracking-[-0.025em]">
              How the CXOps AI workflow fits together
            </h2>

            <div className="mt-8 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {capabilities.map(
                (capability) => (
                  <Link
                    key={capability.href}
                    href={capability.href}
                    className="rounded-2xl border border-slate-200 bg-white p-6 transition hover:border-violet-300"
                  >
                    <h3 className="font-semibold text-slate-950">
                      {capability.title}
                    </h3>

                    <p className="mt-3 text-sm leading-6 text-slate-600">
                      {capability.description}
                    </p>

                    <div className="mt-5 text-sm font-medium text-violet-600">
                      Explore workflow →
                    </div>
                  </Link>
                ),
              )}
            </div>
          </section>

          <section className="mt-20 rounded-3xl border border-slate-200 bg-white p-7 lg:p-10">
            <h2 className="text-2xl font-semibold tracking-[-0.025em]">
              Can CXOps AI test a RAG and approval workflow before execution?
            </h2>

            <p className="mt-5 max-w-4xl leading-7 text-slate-600">
              Yes. Within this demonstration environment,
              a support case can move through knowledge
              retrieval, AI-assisted decision making,
              risk-aware tool planning, human approval when
              required, controlled execution, and a
              persistent audit trail.
            </p>

            <p className="mt-4 max-w-4xl leading-7 text-slate-600">
              The purpose is to make the intermediate stages
              visible so developers and operations teams can
              inspect what evidence was retrieved, what the
              agent proposed, whether approval was required,
              and what execution outcome was recorded.
            </p>
          </section>

          <section className="mt-20">
            <h2 className="text-2xl font-semibold tracking-[-0.025em]">
              Explore the CXOps AI system
            </h2>

            <div className="mt-6 flex flex-wrap gap-x-6 gap-y-3 text-sm">
              <Link href="/tickets">
                Support Tickets
              </Link>

              <Link href="/knowledge">
                Knowledge & RAG
              </Link>

              <Link href="/agent">
                AI Agent
              </Link>

              <Link href="/approvals">
                Human Approvals
              </Link>

              <Link href="/runs">
                Audit Trail
              </Link>

              <Link href="/observability">
                Observability
              </Link>

              <Link href="/">
                CXOps AI Home
              </Link>
            </div>
          </section>
        </div>
      </main>
    </>
  );
}
