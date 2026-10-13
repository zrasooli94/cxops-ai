import type { Metadata } from "next";
import { Building2, CheckCircle2, Code2, FileText, Inbox, ShieldCheck } from "lucide-react";

import { Container, CtaBand, SectionHeading } from "@/components/marketing/site";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "Configure your business, add knowledge, install the widget, receive inquiries and manage conversations - the CXOps AI setup journey step by step.",
  alternates: { canonical: "/how-it-works" },
};

const JOURNEY = [
  {
    icon: Building2,
    title: "Configure your business",
    body: "After your early-access request is approved, you receive an invitation to your own workspace. Add your business details and invite the colleagues who will answer customers.",
    detail: ["Business name, industry and website", "Team roles from viewer to owner", "Each business is a separate workspace"],
  },
  {
    icon: FileText,
    title: "Add your knowledge",
    body: "Provide the information you already give customers - opening hours, services, prices you publish, policies and FAQs. The assistant answers from this content.",
    detail: ["Paste text or upload documents", "Keep it current as your business changes", "Without knowledge, the assistant greets visitors and hands off"],
  },
  {
    icon: ShieldCheck,
    title: "Approval before going live",
    body: "Your setup checklist shows what is complete. When you submit, the CXOps team reviews the workspace before AI replies are switched on - so no business goes live by accident.",
    detail: ["Readiness checklist with clear warnings", "AI replies and spending stay off until approval", "You decide when to switch the chatbot on"],
  },
  {
    icon: Code2,
    title: "Install the widget",
    body: "Copy one line of code into your website. The widget only starts on the exact domains you approved, so the same code cannot be reused on someone else's site.",
    detail: ["One script tag before </body>", "Works with most website builders that allow custom code", "Preview your assistant before it is public"],
  },
  {
    icon: Inbox,
    title: "Receive and manage inquiries",
    body: "Visitors chat with your assistant. When a person is needed, the conversation appears in your handoff queue. Your overview shows activity, usage and what needs attention.",
    detail: ["Handoff queue for your team", "Recent conversations at a glance", "Monthly AI usage and estimated cost"],
  },
];

export default function HowItWorksPage() {
  return (
    <>
      <section className="border-b border-slate-100 bg-[linear-gradient(180deg,#fbfbff_0%,#ffffff_100%)] py-16 lg:py-20">
        <Container>
          <SectionHeading
            as="h1"
            eyebrow="How it works"
            title="From request to a live assistant, with a review in between"
            intro="CXOps AI is set up in a guided sequence. You can stop at any step and pick up where you left off - progress is saved automatically."
          />
        </Container>
      </section>

      <section className="py-20">
        <Container>
          <ol className="relative space-y-6 before:absolute before:bottom-6 before:left-[27px] before:top-6 before:w-px before:bg-slate-200 lg:before:left-[31px]">
            {JOURNEY.map(({ icon: Icon, title, body, detail }, index) => (
              <li key={title} className="relative flex gap-5 lg:gap-8">
                <span className="relative z-10 flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-slate-200 bg-white text-violet-600 shadow-sm lg:h-16 lg:w-16">
                  <Icon className="h-6 w-6" strokeWidth={1.7} aria-hidden="true" />
                </span>
                <div className="flex-1 rounded-2xl border border-slate-200 bg-white p-6 lg:p-7">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
                    Step {index + 1}
                  </p>
                  <h2 className="mt-2 text-xl font-semibold tracking-[-0.02em] text-slate-950">{title}</h2>
                  <p className="mt-3 max-w-3xl leading-7 text-slate-600">{body}</p>
                  <ul className="mt-5 grid gap-2 sm:grid-cols-3">
                    {detail.map((item) => (
                      <li key={item} className="flex gap-2 text-sm leading-6 text-slate-700">
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              </li>
            ))}
          </ol>
        </Container>
      </section>

      <section className="border-t border-slate-100 bg-slate-50/70 py-16">
        <Container className="grid gap-8 md:grid-cols-3">
          {[
            ["What you need", "A website where you can add custom code, and the information you want the assistant to share."],
            ["Who does the setup", "You do, with the guided checklist. Our team reviews it and helps with anything unusual."],
            ["What it costs", "Pricing is shared on request during early access. Every plan includes a monthly AI allowance."],
          ].map(([title, body]) => (
            <div key={title}>
              <h2 className="font-semibold text-slate-950">{title}</h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">{body}</p>
            </div>
          ))}
        </Container>
      </section>

      <CtaBand />
    </>
  );
}
