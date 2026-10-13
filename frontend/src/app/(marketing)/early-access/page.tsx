import type { Metadata } from "next";
import { CalendarCheck, ClipboardList, MailCheck } from "lucide-react";

import { Container, Eyebrow } from "@/components/marketing/site";

import { LeadForm } from "./lead-form";

export const metadata: Metadata = {
  title: "Request a demo",
  description:
    "Request a CXOps AI demo or early access for your business. We review every request and reply by email.",
  alternates: { canonical: "/early-access" },
};

const NEXT_STEPS = [
  {
    icon: ClipboardList,
    title: "We review your request",
    body: "A person on our team reads every request - there is no automated sign-up.",
  },
  {
    icon: MailCheck,
    title: "We contact you by email",
    body: "We discuss what your customers ask, what you would like automated, and pricing.",
  },
  {
    icon: CalendarCheck,
    title: "You get your workspace",
    body: "If it is a fit, you receive an invitation to set up your business with guided steps.",
  },
];

export default function EarlyAccessPage() {
  return (
    <section className="bg-[linear-gradient(180deg,#fbfbff_0%,#ffffff_60%)] py-16 lg:py-20">
      <Container className="grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:gap-16">
        <div>
          <Eyebrow>Request a demo</Eyebrow>
          <h1 className="mt-3 text-4xl font-semibold tracking-[-0.04em] text-slate-950 sm:text-5xl">
            Put an assistant on your website that your team can trust
          </h1>
          <p className="mt-6 text-lg leading-8 text-slate-600">
            We are onboarding a small number of businesses during early access. Tell us
            about yours and we will show you how CXOps AI would answer your customers.
          </p>
          <ol className="mt-10 space-y-6">
            {NEXT_STEPS.map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
                  <Icon className="h-5 w-5" strokeWidth={1.8} aria-hidden="true" />
                </span>
                <div>
                  <h2 className="font-semibold text-slate-950">{title}</h2>
                  <p className="mt-1 text-sm leading-6 text-slate-600">{body}</p>
                </div>
              </li>
            ))}
          </ol>
          <p className="mt-10 text-sm leading-6 text-slate-500">
            Pricing is shared on request. We will never charge you without a separate,
            explicit agreement.
          </p>
        </div>
        <LeadForm />
      </Container>
    </section>
  );
}
