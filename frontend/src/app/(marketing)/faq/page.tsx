import type { Metadata } from "next";
import Link from "next/link";
import { ChevronDown } from "lucide-react";

import { Container, CtaBand, SectionHeading } from "@/components/marketing/site";

export const metadata: Metadata = {
  title: "FAQ",
  description:
    "Answers about CXOps AI setup requirements, how answers are generated, human handoff, data handling, pricing availability and current limitations.",
  alternates: { canonical: "/faq" },
};

type Faq = { q: string; a: React.ReactNode; text: string };

const SECTIONS: { id: string; title: string; items: Faq[] }[] = [
  {
    id: "product",
    title: "The product",
    items: [
      {
        q: "What is CXOps AI?",
        text: "A customer-support assistant for your website that answers from your own business knowledge and hands conversations to your team when a person is needed.",
        a: "A customer-support assistant for your website. It answers visitors from the business knowledge you provide and hands conversations to your team when a person is needed. Your team works those conversations from a shared inbox.",
      },
      {
        q: "How does the assistant decide what to say?",
        text: "It uses an AI model together with the knowledge documents you provide. When your knowledge does not cover a question, it says so or hands the conversation to your team.",
        a: "It uses an AI language model together with the knowledge documents you add. Answers drawn from your knowledge keep track of their source. When your knowledge does not cover a question, the assistant says it cannot help with that or hands the conversation to your team.",
      },
      {
        q: "Can the AI make mistakes?",
        text: "Yes. AI answers can be wrong or incomplete. Keep your knowledge accurate and review conversations; visitors can always ask for a person.",
        a: "Yes. AI answers can be wrong or incomplete, which is why handoff is built in and why your team can review every conversation. Keeping your knowledge accurate is the most effective way to improve answers.",
      },
      {
        q: "What happens when a visitor asks for a person?",
        text: "The conversation moves to your handoff queue, where a team member can pick it up, reply and resolve it.",
        a: "The conversation moves to your team's handoff queue. A team member can assign it to themselves, reply, and resolve it. The visitor is told that a person will follow up.",
      },
    ],
  },
  {
    id: "setup",
    title: "Setup requirements",
    items: [
      {
        q: "What do I need to get started?",
        text: "A website where you can add a custom script, the information you want the assistant to share, and an approved early-access request.",
        a: "A website where you can add a custom script tag, the information you want the assistant to share (hours, services, policies, FAQs), and an approved early-access request.",
      },
      {
        q: "Does it work with my website builder?",
        text: "It works with most sites and builders that allow a custom script tag. Some hosted builders restrict custom code on lower plans.",
        a: "It works with most websites and builders that let you add a custom script tag to your pages. Some hosted builders restrict custom code on lower-tier plans - check yours, or ask us during onboarding.",
      },
      {
        q: "Why is there a review before the assistant goes live?",
        text: "Every new business is reviewed before AI replies are switched on, so nothing goes live by accident and AI spending starts only after approval.",
        a: "Every new business is reviewed by our team before AI replies are enabled. Until then your workspace can be fully set up, but the chatbot does not answer visitors and no AI spending occurs.",
      },
    ],
  },
  {
    id: "data",
    title: "Data handling",
    items: [
      {
        q: "Is my business data separated from other customers?",
        text: "Yes. Each business is a separate workspace; conversations, knowledge, usage and settings are scoped to that business and checked on every request.",
        a: "Yes. Each business is a separate workspace. Conversations, knowledge, usage and settings are scoped to that business, and every request is checked against the signed-in person's membership and role.",
      },
      {
        q: "Which AI provider is used?",
        text: "Answers are generated with OpenAI models. Visitor messages and relevant knowledge excerpts are sent to the provider to produce a reply.",
        a: "Answers are currently generated with OpenAI models. To produce a reply, the visitor's message and relevant excerpts of your knowledge are sent to the provider. Do not add information to your knowledge that you would not want an AI provider to process.",
      },
      {
        q: "What do you store about website visitors?",
        text: "The conversation messages and basic session information needed to run the chat. Visitors' IP addresses are not stored with conversations.",
        a: "The conversation messages and the session information needed to run the chat and the handoff. Visitor IP addresses are not stored with conversations, and our operational logs record identifiers and timings rather than message text.",
      },
      {
        q: "Do you hold security certifications?",
        text: "Not at this time. We describe our controls openly instead of claiming certifications we do not have.",
        a: "Not at this time. We would rather describe our controls openly - see the security section of our features page - than claim certifications we do not hold.",
      },
    ],
  },
  {
    id: "pricing",
    title: "Pricing and availability",
    items: [
      {
        q: "How much does CXOps AI cost?",
        text: "Pricing is shared on request during early access. Contact us for a quote.",
        a: (
          <>
            Pricing is shared on request during early access.{" "}
            <Link href="/early-access" className="font-medium text-violet-700 underline underline-offset-4">
              Request a demo
            </Link>{" "}
            and we will provide a quote for your business.
          </>
        ),
      },
      {
        q: "Is there a limit on AI usage?",
        text: "Yes. Each business has a monthly AI allowance. When it is reached, new messages go straight to your team instead of the AI.",
        a: "Yes. Each business has a monthly allowance of AI conversations and estimated AI cost. It is checked before every AI reply; when it is reached, new messages go straight to your team instead. Usage is visible in your workspace.",
      },
      {
        q: "Is the usage cost shown exact?",
        text: "No. Costs shown in the product are estimates based on recorded usage; the AI provider's billing is authoritative.",
        a: "No. Costs shown in the product are estimates calculated from recorded usage. The AI provider's own billing is the authoritative figure.",
      },
    ],
  },
  {
    id: "limitations",
    title: "Current limitations",
    items: [
      {
        q: "What does CXOps AI not do yet?",
        text: "No self-service checkout or online billing, invitation emails are shared manually, response times are not guaranteed, and integrations are set up case by case.",
        a: "Self-service sign-up and online billing are not available yet - access is by approved request. Team invitation links are shared by you rather than emailed automatically. We do not guarantee response times. Connections to other business systems are set up case by case during onboarding.",
      },
    ],
  },
];

const structuredData = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: SECTIONS.flatMap((section) =>
    section.items.map((item) => ({
      "@type": "Question",
      name: item.q,
      acceptedAnswer: { "@type": "Answer", text: item.text },
    })),
  ),
};

export default function FaqPage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />
      <section className="border-b border-slate-100 bg-[linear-gradient(180deg,#fbfbff_0%,#ffffff_100%)] py-16 lg:py-20">
        <Container>
          <SectionHeading
            as="h1"
            eyebrow="FAQ"
            title="Questions businesses ask us"
            intro="Straight answers about setup, how the assistant works, data handling, pricing and what is not available yet."
          />
          <nav aria-label="FAQ sections" className="mt-8 flex flex-wrap gap-2">
            {SECTIONS.map((section) => (
              <a
                key={section.id}
                href={`#${section.id}`}
                className="rounded-full border border-slate-200 bg-white px-4 py-1.5 text-sm text-slate-700 hover:border-violet-300"
              >
                {section.title}
              </a>
            ))}
          </nav>
        </Container>
      </section>

      <section className="py-16">
        <Container className="max-w-4xl space-y-14">
          {SECTIONS.map((section) => (
            <div key={section.id} id={section.id} className="scroll-mt-20">
              <h2 className="text-xl font-semibold tracking-[-0.02em] text-slate-950">{section.title}</h2>
              <div className="mt-5 divide-y divide-slate-200 rounded-2xl border border-slate-200 bg-white">
                {section.items.map((item) => (
                  <details key={item.q} className="group px-5 py-4">
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-md text-left font-medium text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-500 [&::-webkit-details-marker]:hidden">
                      {item.q}
                      <ChevronDown
                        className="h-4 w-4 shrink-0 text-slate-400 transition group-open:rotate-180"
                        aria-hidden="true"
                      />
                    </summary>
                    <div className="mt-3 text-sm leading-7 text-slate-600">{item.a}</div>
                  </details>
                ))}
              </div>
            </div>
          ))}
        </Container>
      </section>

      <CtaBand title="Still have a question?" body="Send us a note with your request and we will answer it personally." />
    </>
  );
}
