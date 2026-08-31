import type { Metadata } from "next";
import Link from "next/link";

const siteUrl = "https" + "://" + "cxops-ai.vercel.app";

export const metadata: Metadata = {
  title: "Evaluating AI Customer Support Platforms for High-Risk Actions",
  description:
    "A factual evaluation guide for comparing authorization, human approval, policy enforcement, auditability, escalation, observability, and recovery controls in customer-support AI platforms.",
  alternates: {
    canonical: "/platform/evaluating-ai-support-platforms",
  },
  robots: {
    index: true,
    follow: true,
  },
};

const structuredData = {
  "@context": "https" + "://" + "schema.org",
  "@type": "Article",
  headline: "Evaluating AI Customer Support Platforms for High-Risk Actions",
  description:
    "A buyer's guide to the operational controls that matter when customer-support AI can take consequential actions.",
  url: `${siteUrl}/platform/evaluating-ai-support-platforms`,
  author: {
    "@type": "Organization",
    name: "CXOps AI",
    url: siteUrl,
  },
};

const criteria = [
  {
    title: "Authorization and tool permissions",
    question:
      "Can you define which tools the agent may call, which records or fields it may change, and which identity or tenant boundaries apply?",
    evidence:
      "Ask for the actual policy configuration and a denied-action example, not only a general security statement.",
  },
  {
    title: "Human approval",
    question:
      "Can risky actions pause before execution so a reviewer sees the proposed tool, arguments, risk, and intended effect?",
    evidence:
      "Verify whether approval applies per action, per workflow, or only through a conversation handoff.",
  },
  {
    title: "Policy enforcement",
    question:
      "Are business rules enforced deterministically before execution, or supplied only as natural-language guidance to the model?",
    evidence:
      "Request tests showing what happens when model output conflicts with the policy.",
  },
  {
    title: "Auditability and observability",
    question:
      "Does the platform preserve the decision path, retrieved evidence, tool arguments, approval decision, execution result, and errors?",
    evidence:
      "Confirm retention, exportability, access controls, and how records correlate across a conversation and external system.",
  },
  {
    title: "Reversibility and failure recovery",
    question:
      "Which actions are genuinely reversible, who can initiate recovery, and how are partial failures or duplicate attempts handled?",
    evidence:
      "Do not treat retries as rollback. Test recovery separately for every consequential integration.",
  },
  {
    title: "Escalation",
    question:
      "Can the agent hand off on explicit rules, uncertainty, customer request, policy conditions, or failed tools while preserving context?",
    evidence:
      "Verify the routing target, fallback behavior, and whether autonomous execution stops after handoff.",
  },
];

const vendorEvidence = [
  {
    vendor: "Forethought",
    finding:
      "Forethought's public product material describes human-agent assistance, smart handoffs, ticket classification, and performance evaluation. The reviewed public pages do not establish a universal per-action approval or rollback contract, so buyers should verify those controls for each integration.",
    sources: [
      ["Agent QA and handoff overview", "https://forethought.ai/platform/agent-qa"],
      ["Assist agent", "https://forethought.ai/platform/assist"],
    ],
  },
  {
    vendor: "Intercom Fin",
    finding:
      "Intercom documents data-driven escalation rules, natural-language escalation guidance, workflow routing, and an email-only human-input option. Intercom also states that Guidance itself cannot perform most conversation actions; those require workflows or other product controls.",
    sources: [
      ["Escalation guidance and rules", "https://www.intercom.com/help/en/articles/12396892-manage-fin-ai-agent-s-escalation-guidance-and-rules"],
      ["Fin Guidance scope and limits", "https://www.intercom.com/help/en/articles/10210126-provide-fin-ai-agent-with-specific-guidance"],
    ],
  },
  {
    vendor: "Ada",
    finding:
      "Ada documents configurable live and asynchronous handoffs with fallback behavior. Its data-export message schema records selected tools, arguments, result status, errors, return values, and timestamps. Buyers should still verify authorization and reversal behavior for each configured Action.",
    sources: [
      ["Handoff management", "https://docs.ada.cx/docs/handoffs/handoff-management"],
      ["Tool-call message records", "https://docs.ada.cx/data-export-message-object"],
    ],
  },
  {
    vendor: "Sierra",
    finding:
      "Sierra's official release-governance material describes automated checks, simulations, human merge approvals, and staged rollouts for agent changes. That is release governance; it should not be assumed to be the same as runtime approval for every customer action.",
    sources: [
      ["Release governance", "https://sierra.ai/blog/release-governance-guardrails-for-agents-at-scale"],
    ],
  },
  {
    vendor: "Decagon",
    finding:
      "The reviewed public material was not specific enough to support claims about per-action authorization, human approval, audit export, or rollback semantics. Treat those capabilities as unverified until Decagon supplies current product documentation or a test environment.",
    sources: [
      ["Decagon product site", "https://decagon.ai/"],
    ],
  },
];

const cxopsControls = [
  "Every proposed tool is checked against an explicit allowlist and assigned a risk level.",
  "Customer-facing replies and ticket updates require human approval in the demonstrated policy.",
  "Reviewers can inspect the proposed tool plan before approving or rejecting execution.",
  "Runs retain decisions, authorization plans, reviewer outcomes, execution status, and errors for inspection.",
  "Unknown tools fail closed instead of receiving implicit permission.",
  "CXOps does not claim generic rollback: reversibility depends on the external tool and must be designed and tested per integration.",
];

export default function EvaluationGuidePage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />

      <main className="min-h-screen bg-[#f7f8fc] text-slate-950">
        <div className="mx-auto max-w-6xl px-6 py-16 lg:px-10 lg:py-24">
          <Link href="/platform" className="text-sm font-medium text-violet-600">
            ← CXOps AI platform
          </Link>

          <header className="mt-10 max-w-4xl">
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-violet-600">
              Evidence-backed buyer&apos;s guide
            </p>
            <h1 className="mt-5 text-4xl font-semibold tracking-[-0.04em] sm:text-5xl lg:text-6xl">
              Evaluating AI customer support platforms for high-risk actions
            </h1>
            <p className="mt-7 text-lg leading-8 text-slate-600">
              A support agent that only drafts an answer has a different risk
              profile from one that refunds an order, changes an account, sends
              a customer message, or updates a system of record. Compare the
              controls around the action—not just answer quality or automation
              rate.
            </p>
            <p className="mt-5 text-sm leading-6 text-slate-500">
              Evidence reviewed 31 August 2026. Vendor products change. The
              links below are official sources, but this guide is not a claim
              that every feature is available in every plan, channel, or
              integration.
            </p>
          </header>

          <section className="mt-16">
            <h2 className="text-2xl font-semibold tracking-[-0.025em]">
              The controls to compare
            </h2>
            <div className="mt-8 grid gap-5 md:grid-cols-2">
              {criteria.map((item) => (
                <article key={item.title} className="rounded-2xl border border-slate-200 bg-white p-6">
                  <h3 className="font-semibold">{item.title}</h3>
                  <p className="mt-3 leading-7 text-slate-600">{item.question}</p>
                  <p className="mt-4 border-l-2 border-violet-300 pl-4 text-sm leading-6 text-slate-500">
                    Evidence to request: {item.evidence}
                  </p>
                </article>
              ))}
            </div>
          </section>

          <section className="mt-20">
            <h2 className="text-2xl font-semibold tracking-[-0.025em]">
              What official vendor sources establish
            </h2>
            <p className="mt-4 max-w-4xl leading-7 text-slate-600">
              This is an evidence boundary, not a vendor ranking. A missing
              public claim means “verify,” not “the capability does not exist.”
            </p>
            <div className="mt-8 space-y-5">
              {vendorEvidence.map((item) => (
                <article key={item.vendor} className="rounded-2xl border border-slate-200 bg-white p-6 lg:p-7">
                  <h3 className="text-lg font-semibold">{item.vendor}</h3>
                  <p className="mt-3 leading-7 text-slate-600">{item.finding}</p>
                  <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm">
                    {item.sources.map(([label, href]) => (
                      <a key={href} href={href} target="_blank" rel="noopener noreferrer" className="font-medium text-violet-600 underline decoration-violet-200 underline-offset-4">
                        {label} ↗
                      </a>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="mt-20 rounded-3xl border border-slate-200 bg-white p-7 lg:p-10">
            <h2 className="text-2xl font-semibold tracking-[-0.025em]">
              How the CXOps AI demonstration approaches execution control
            </h2>
            <p className="mt-5 max-w-4xl leading-7 text-slate-600">
              CXOps AI is a demonstration and testing environment, not a claim
              of production equivalence with the vendors above. Its purpose is
              to make a controlled support workflow inspectable end to end.
            </p>
            <ul className="mt-7 grid gap-3 md:grid-cols-2">
              {cxopsControls.map((item) => (
                <li key={item} className="rounded-xl bg-slate-50 p-4 text-sm leading-6 text-slate-700">
                  {item}
                </li>
              ))}
            </ul>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/approvals" className="rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white">
                Inspect human approvals
              </Link>
              <Link href="/runs" className="rounded-xl border border-slate-300 px-5 py-3 text-sm font-medium">
                Inspect the audit trail
              </Link>
              <Link href="/observability" className="rounded-xl border border-slate-300 px-5 py-3 text-sm font-medium">
                Inspect observability
              </Link>
            </div>
          </section>

          <section className="mt-20 rounded-3xl border border-amber-200 bg-amber-50 p-7 lg:p-10">
            <h2 className="text-2xl font-semibold tracking-[-0.025em]">
              A practical proof before production
            </h2>
            <ol className="mt-6 grid gap-3 text-sm leading-6 text-slate-700 md:grid-cols-2">
              <li>1. Define one high-impact action and its allowed inputs.</li>
              <li>2. Demonstrate a denied unauthorized attempt.</li>
              <li>3. Pause an allowed-but-risky action for human review.</li>
              <li>4. Inspect the evidence, plan, and proposed arguments.</li>
              <li>5. Reject once and verify that nothing executes.</li>
              <li>6. Approve once and correlate the external result.</li>
              <li>7. Simulate timeout, partial failure, and duplicate delivery.</li>
              <li>8. Test the documented recovery or compensating action.</li>
            </ol>
            <p className="mt-6 max-w-4xl leading-7 text-slate-600">
              A platform is ready for a risky workflow only when the team can
              explain who authorized it, what the agent intended, what actually
              happened, and how the organization responds when execution fails.
            </p>
          </section>
        </div>
      </main>
    </>
  );
}
