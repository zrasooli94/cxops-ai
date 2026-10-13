import type { Metadata } from "next";
import Link from "next/link";
import {
  BarChart3,
  BookOpenCheck,
  Building2,
  Globe2,
  Hand,
  Inbox,
  LockKeyhole,
  MessageSquareText,
  ShieldCheck,
} from "lucide-react";

import { ProductPreview } from "@/components/marketing/product-preview";
import {
  Container,
  CtaBand,
  FeatureCard,
  PrimaryCta,
  SecondaryCta,
  SectionHeading,
} from "@/components/marketing/site";

export const metadata: Metadata = {
  title: { absolute: "CXOps AI | AI website assistant with human handoff" },
  description:
    "CXOps AI answers your website visitors from your own business knowledge, hands conversations to your team when a person is needed, and keeps AI spending within limits you set.",
  alternates: { canonical: "/" },
};

const PILLARS = [
  {
    icon: <BookOpenCheck className="h-5 w-5" strokeWidth={1.8} aria-hidden="true" />,
    title: "Answers from your knowledge",
    body: "The assistant draws on the policies, FAQs and service details you provide, and shows which source an answer came from.",
  },
  {
    icon: <Hand className="h-5 w-5" strokeWidth={1.8} aria-hidden="true" />,
    title: "A person when it matters",
    body: "Visitors can ask for a human at any time. Conversations the assistant should not handle go to your team's handoff queue.",
  },
  {
    icon: <ShieldCheck className="h-5 w-5" strokeWidth={1.8} aria-hidden="true" />,
    title: "Controls you can explain",
    body: "Exact website allowlists, per-business data separation, team roles and monthly AI limits are built in, not bolted on.",
  },
];

const STEPS = [
  ["Set up your business", "Name, website and the team members who will answer customers."],
  ["Add your knowledge", "Upload the answers you already give: hours, services, policies and FAQs."],
  ["Install one line of code", "Paste a single script tag into your website. It only works on domains you approve."],
  ["Answer and hand off", "The assistant replies; your team picks up anything that needs a person."],
] as const;

const CAPABILITIES = [
  { icon: MessageSquareText, title: "Website chat widget", body: "A lightweight chat window for your site, branded with your name and a colour theme." },
  { icon: Inbox, title: "Conversation inbox", body: "Every website conversation in one place, with assign, release and resolve for your team." },
  { icon: Building2, title: "Team workspaces", body: "Invite colleagues with roles from viewer to owner. Each business sees only its own data." },
  { icon: BarChart3, title: "Usage and cost visibility", body: "Monthly AI conversation counts and estimated spending against the allowance for your plan." },
  { icon: Globe2, title: "Approved domains only", body: "The widget runs only on the exact website addresses you list - no wildcards." },
  { icon: LockKeyhole, title: "Security by default", body: "One-time widget keys with rotation, rate limits on public traffic, and audited configuration changes." },
];

export default function HomePage() {
  return (
    <>
      <section className="relative overflow-hidden border-b border-slate-100 bg-[linear-gradient(180deg,#fbfbff_0%,#ffffff_100%)]">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -top-40 left-1/2 h-[480px] w-[900px] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgba(115,87,255,0.12),transparent)]"
        />
        <Container className="relative grid items-center gap-14 py-16 lg:grid-cols-[1.05fr_1fr] lg:py-24">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-medium text-violet-700">
              <span className="h-1.5 w-1.5 rounded-full bg-violet-500" aria-hidden="true" />
              Now onboarding early-access businesses
            </p>
            <h1 className="mt-6 text-4xl font-semibold leading-[1.05] tracking-[-0.045em] text-slate-950 sm:text-5xl lg:text-[3.6rem]">
              Answer every website visitor. Hand the right ones to your team.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-8 text-slate-600">
              CXOps AI is a customer-support assistant for your website. It answers
              from your own business knowledge, passes conversations to a person
              when one is needed, and keeps AI usage inside limits you control.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <PrimaryCta />
              <SecondaryCta href="/how-it-works">See how it works</SecondaryCta>
            </div>
            <ul className="mt-9 grid max-w-lg grid-cols-1 gap-2 text-sm text-slate-600 sm:grid-cols-2">
              {[
                "Answers grounded in your content",
                "Human handoff built in",
                "Runs only on your approved domains",
                "Monthly AI limits per business",
              ].map((item) => (
                <li key={item} className="flex items-center gap-2">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#42a5ff]" aria-hidden="true" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <ProductPreview />
        </Container>
      </section>

      <section className="py-20 lg:py-24">
        <Container>
          <SectionHeading
            eyebrow="Why businesses use it"
            title="Fast answers for customers, without losing the human touch"
            intro="Most website questions repeat: opening hours, bookings, prices, policies. CXOps AI handles those consistently and makes sure the conversations that need judgement reach a person."
          />
          <div className="mt-12 grid gap-5 md:grid-cols-3">
            {PILLARS.map((pillar) => (
              <FeatureCard key={pillar.title} icon={pillar.icon} title={pillar.title}>
                {pillar.body}
              </FeatureCard>
            ))}
          </div>
        </Container>
      </section>

      <section className="border-y border-slate-100 bg-slate-50/70 py-20 lg:py-24">
        <Container>
          <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
            <SectionHeading
              eyebrow="How it works"
              title="Live in four steps"
              intro="Guided setup takes you from a new workspace to a chatbot on your site. We review each new business before its assistant goes live."
            />
            <Link href="/how-it-works" className="text-sm font-medium text-violet-700 hover:text-violet-900">
              The full walkthrough →
            </Link>
          </div>
          <ol className="mt-12 grid gap-5 md:grid-cols-4">
            {STEPS.map(([title, body], index) => (
              <li key={title} className="rounded-2xl border border-slate-200 bg-white p-6">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-950 text-sm font-semibold text-white">
                  {index + 1}
                </span>
                <h3 className="mt-5 font-semibold text-slate-950">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">{body}</p>
              </li>
            ))}
          </ol>
        </Container>
      </section>

      <section className="py-20 lg:py-24">
        <Container>
          <SectionHeading
            eyebrow="What's included"
            title="Everything a small support team needs on day one"
          />
          <div className="mt-12 grid gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-3">
            {CAPABILITIES.map(({ icon: Icon, title, body }) => (
              <div key={title} className="flex gap-4">
                <Icon className="mt-0.5 h-5 w-5 shrink-0 text-violet-600" strokeWidth={1.8} aria-hidden="true" />
                <div>
                  <h3 className="font-semibold text-slate-950">{title}</h3>
                  <p className="mt-1.5 text-sm leading-6 text-slate-600">{body}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-12">
            <SecondaryCta href="/features">Explore all features</SecondaryCta>
          </div>
        </Container>
      </section>

      <section className="border-t border-slate-100 bg-slate-50/70 py-20 lg:py-24">
        <Container className="grid gap-12 lg:grid-cols-2">
          <SectionHeading
            eyebrow="Built for"
            title="Businesses whose customers ask before they buy"
            intro="Automotive dealers and workshops, local service businesses, clinics, retailers and support teams who want quick, accurate answers on their website."
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              ["Automotive", "Stock, valuations, bookings and opening hours."],
              ["Local services", "Quotes, availability and service areas."],
              ["Support teams", "Deflect repeat questions, escalate the rest."],
              ["Retail", "Orders, returns and store information."],
            ].map(([title, body]) => (
              <Link
                key={title}
                href="/solutions"
                className="rounded-2xl border border-slate-200 bg-white p-5 transition hover:border-violet-300"
              >
                <p className="font-semibold text-slate-950">{title}</p>
                <p className="mt-1.5 text-sm leading-6 text-slate-600">{body}</p>
              </Link>
            ))}
          </div>
        </Container>
      </section>

      <CtaBand />
    </>
  );
}
