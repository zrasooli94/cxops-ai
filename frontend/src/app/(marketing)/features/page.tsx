import type { Metadata } from "next";
import {
  BarChart3,
  BookOpenCheck,
  Building2,
  Globe2,
  Hand,
  Inbox,
  KeyRound,
  LockKeyhole,
  MessageSquareText,
  ShieldCheck,
  SlidersHorizontal,
  Users,
} from "lucide-react";

import { Container, CtaBand, SectionHeading } from "@/components/marketing/site";

export const metadata: Metadata = {
  title: "Features",
  description:
    "Website chatbot, knowledge-grounded answers, human handoff, team workspaces, conversation management, security controls and usage reporting in CXOps AI.",
  alternates: { canonical: "/features" },
};

type Feature = { icon: typeof Inbox; title: string; points: string[] };

const GROUPS: { id: string; eyebrow: string; title: string; intro: string; features: Feature[] }[] = [
  {
    id: "chatbot",
    eyebrow: "For your visitors",
    title: "A website assistant that knows your business",
    intro: "Visitors get an immediate, consistent answer - and a clear path to a person.",
    features: [
      {
        icon: MessageSquareText,
        title: "Website chatbot",
        points: [
          "Installed with one script tag",
          "Your assistant name, greeting and a choice of colour themes",
          "Conversations continue across page loads in the same visit",
        ],
      },
      {
        icon: BookOpenCheck,
        title: "Grounded knowledge answers",
        points: [
          "Answers draw on the documents you provide",
          "Sources are tracked for every grounded answer",
          "When the knowledge does not cover a question, the assistant says so or hands off",
        ],
      },
      {
        icon: Hand,
        title: "Human handoff",
        points: [
          "Visitors can ask for a person at any time",
          "The assistant hands off conversations it should not handle",
          "If the monthly AI allowance is used up, messages go straight to your team",
        ],
      },
    ],
  },
  {
    id: "team",
    eyebrow: "For your team",
    title: "One place to manage every conversation",
    intro: "Your staff see what the assistant said, pick up handoffs, and close the loop.",
    features: [
      {
        icon: Inbox,
        title: "Conversation management",
        points: [
          "Inbox of website conversations and tickets",
          "Assign, release and resolve handoffs",
          "Recent activity on your workspace overview",
        ],
      },
      {
        icon: Users,
        title: "Team access",
        points: [
          "Invite colleagues by email link; invitations expire and can be revoked",
          "Roles: owner, administrator, supervisor, support agent, viewer",
          "One person can belong to several businesses and switch between them",
        ],
      },
      {
        icon: Building2,
        title: "Multi-business administration",
        points: [
          "Each business is a separate workspace with its own data",
          "Guided setup with a readiness checklist",
          "New businesses are reviewed before the assistant goes live",
        ],
      },
    ],
  },
  {
    id: "controls",
    eyebrow: "For your peace of mind",
    title: "Security and spending controls",
    intro: "Designed so a business owner can explain exactly what the assistant can and cannot do.",
    features: [
      {
        icon: Globe2,
        title: "Exact domain allowlists",
        points: [
          "The widget only starts on the website addresses you approve",
          "No wildcard domains",
          "An empty allowlist or the off switch stops new conversations at once",
        ],
      },
      {
        icon: KeyRound,
        title: "Widget keys",
        points: [
          "Shown once at creation and stored only as a one-way hash",
          "Rotate a key at any time; the old key stops working immediately",
          "A widget key never grants staff access",
        ],
      },
      {
        icon: LockKeyhole,
        title: "Business data separation",
        points: [
          "Every record and knowledge search is scoped to one business",
          "Team roles decide who can change settings or see reports",
          "Configuration changes are logged without message content",
        ],
      },
      {
        icon: ShieldCheck,
        title: "Abuse protection",
        points: [
          "Rate limits on public chat traffic and message size limits",
          "Sessions expire automatically",
          "Visitors never see staff tools or internal notes",
        ],
      },
      {
        icon: SlidersHorizontal,
        title: "Monthly AI allowance",
        points: [
          "A monthly limit on AI conversations and estimated AI cost per business",
          "Checked before every AI reply - never after",
          "When reached, conversations route to your team instead",
        ],
      },
      {
        icon: BarChart3,
        title: "Reporting",
        points: [
          "AI conversations used and remaining this month",
          "Estimated AI spending against your allowance",
          "Reply-time and handoff indicators for the last 24 hours",
        ],
      },
    ],
  },
];

export default function FeaturesPage() {
  return (
    <>
      <section className="border-b border-slate-100 bg-[linear-gradient(180deg,#fbfbff_0%,#ffffff_100%)] py-16 lg:py-20">
        <Container>
          <SectionHeading
            as="h1"
            eyebrow="Features"
            title="Everything you need to put a trustworthy assistant on your website"
            intro="CXOps AI combines a website chatbot, your own knowledge, a handoff queue for your team, and the controls a business needs before it lets AI talk to customers."
          />
          <nav aria-label="Feature sections" className="mt-8 flex flex-wrap gap-2">
            {GROUPS.map((group) => (
              <a
                key={group.id}
                href={`#${group.id}`}
                className="rounded-full border border-slate-200 bg-white px-4 py-1.5 text-sm text-slate-700 hover:border-violet-300"
              >
                {group.eyebrow}
              </a>
            ))}
          </nav>
        </Container>
      </section>

      {GROUPS.map((group, index) => (
        <section
          key={group.id}
          id={group.id}
          className={`scroll-mt-20 py-20 ${index % 2 === 1 ? "bg-slate-50/70" : ""}`}
        >
          <Container>
            <SectionHeading eyebrow={group.eyebrow} title={group.title} intro={group.intro} />
            <div className="mt-12 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {group.features.map(({ icon: Icon, title, points }) => (
                <article key={title} className="rounded-2xl border border-slate-200 bg-white p-6">
                  <div className="flex items-center gap-3">
                    <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
                      <Icon className="h-5 w-5" strokeWidth={1.8} aria-hidden="true" />
                    </span>
                    <h3 className="font-semibold text-slate-950">{title}</h3>
                  </div>
                  <ul className="mt-5 space-y-2.5">
                    {points.map((point) => (
                      <li key={point} className="flex gap-2.5 text-sm leading-6 text-slate-600">
                        <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#42a5ff]" aria-hidden="true" />
                        {point}
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </Container>
        </section>
      ))}

      <section className="py-12">
        <Container>
          <div className="rounded-2xl border border-slate-200 bg-white p-6 text-sm leading-6 text-slate-600">
            <p className="font-semibold text-slate-900">What CXOps AI does not claim</p>
            <p className="mt-2">
              We do not currently hold third-party security certifications, and we do not
              promise specific response times. AI answers can be wrong; the handoff queue
              exists so your team stays in control. Integrations with your other systems are
              set up case by case during onboarding.
            </p>
          </div>
        </Container>
      </section>

      <CtaBand />
    </>
  );
}
