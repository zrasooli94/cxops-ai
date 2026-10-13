import type { Metadata } from "next";
import { Car, HeartPulse, Headset, Home, ShoppingBag, Store } from "lucide-react";

import { Container, CtaBand, SectionHeading } from "@/components/marketing/site";

export const metadata: Metadata = {
  title: "Solutions",
  description:
    "How automotive businesses, local service providers, customer-support teams, clinics and retailers use an AI website assistant with human handoff.",
  alternates: { canonical: "/solutions" },
};

const SOLUTIONS = [
  {
    id: "automotive",
    icon: Car,
    title: "Automotive sales and services",
    lead: "Dealers, workshops and vehicle buyers field the same questions all day - often after hours.",
    questions: [
      "What are your service hours on Saturday?",
      "Do you buy cars that are not running?",
      "What do I need to bring for a trade-in?",
    ],
    how: "The assistant answers from your published hours, services and policies, and hands anything that needs a quote, an inspection or a booking decision to your team.",
  },
  {
    id: "local-services",
    icon: Home,
    title: "Local and home services",
    lead: "Plumbers, cleaners, electricians and other trades lose leads when nobody answers quickly.",
    questions: [
      "Do you service my suburb?",
      "How soon can someone come out?",
      "Do you offer weekend call-outs?",
    ],
    how: "Answer service-area and availability questions instantly, and pass genuine job requests to a person with the conversation already captured.",
  },
  {
    id: "support-teams",
    icon: Headset,
    title: "Customer-support teams",
    lead: "Teams with a steady volume of repeat questions want to spend their time on the hard ones.",
    questions: [
      "How do I reset my account password?",
      "What is your refund policy?",
      "Where can I find my invoice?",
    ],
    how: "Grounded answers for documented questions, a handoff queue for the rest, and usage reporting so you can see what the assistant is handling.",
  },
  {
    id: "health-wellness",
    icon: HeartPulse,
    title: "Clinics and wellness practices",
    lead: "Practices answer administrative questions that do not need a clinician.",
    questions: [
      "Are you open on public holidays?",
      "Do you have parking?",
      "How do I change my appointment?",
    ],
    how: "Keep the assistant to administrative information you provide, and route anything personal to your front desk. It is not a medical advice tool.",
  },
  {
    id: "retail",
    icon: ShoppingBag,
    title: "Retail and e-commerce",
    lead: "Shoppers ask about delivery, returns and stock before they buy.",
    questions: [
      "How long does delivery take?",
      "Can I return an item bought in store?",
      "Do you price match?",
    ],
    how: "Answer policy questions consistently from your own documents, and hand order-specific issues to your team.",
  },
  {
    id: "professional",
    icon: Store,
    title: "Professional and other services",
    lead: "Agencies, schools, property managers and other service businesses.",
    questions: [
      "What does your onboarding process involve?",
      "Which documents do I need?",
      "Who should I talk to about pricing?",
    ],
    how: "Give visitors accurate first answers and a clear route to the right person on your team.",
  },
];

export default function SolutionsPage() {
  return (
    <>
      <section className="border-b border-slate-100 bg-[linear-gradient(180deg,#fbfbff_0%,#ffffff_100%)] py-16 lg:py-20">
        <Container>
          <SectionHeading
            as="h1"
            eyebrow="Solutions"
            title="Built for businesses that answer the same questions every day"
            intro="The examples below show typical questions and how the assistant and your team share the work. Your assistant only knows what you give it."
          />
        </Container>
      </section>

      <section className="py-20">
        <Container className="grid gap-6 lg:grid-cols-2">
          {SOLUTIONS.map(({ id, icon: Icon, title, lead, questions, how }) => (
            <article id={id} key={id} className="scroll-mt-20 rounded-3xl border border-slate-200 bg-white p-7">
              <div className="flex items-center gap-3">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
                  <Icon className="h-5 w-5" strokeWidth={1.8} aria-hidden="true" />
                </span>
                <h2 className="text-lg font-semibold tracking-[-0.02em] text-slate-950">{title}</h2>
              </div>
              <p className="mt-4 leading-7 text-slate-600">{lead}</p>
              <div className="mt-5 rounded-2xl bg-slate-50 p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                  Typical visitor questions
                </p>
                <ul className="mt-3 space-y-2">
                  {questions.map((question) => (
                    <li key={question} className="text-sm text-slate-700">
                      &ldquo;{question}&rdquo;
                    </li>
                  ))}
                </ul>
              </div>
              <p className="mt-5 text-sm leading-6 text-slate-600">
                <span className="font-semibold text-slate-900">How CXOps AI helps: </span>
                {how}
              </p>
            </article>
          ))}
        </Container>
      </section>

      <CtaBand
        title="Not sure your business is a fit?"
        body="Tell us what your customers ask most often. We will tell you honestly whether the assistant can help."
      />
    </>
  );
}
