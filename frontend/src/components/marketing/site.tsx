import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, Menu, X } from "lucide-react";

// Shared marketing chrome (Phase 1Q). Server components only: the mobile menu
// is a native <details> disclosure, so the public site ships no JavaScript for
// navigation.

export const MARKETING_NAV = [
  { href: "/features", label: "Features" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/solutions", label: "Solutions" },
  { href: "/faq", label: "FAQ" },
] as const;

export function BrandMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <span aria-hidden="true" className={`grid grid-cols-3 gap-[3px] ${className}`}>
      {Array.from({ length: 9 }).map((_, index) => (
        <span
          key={index}
          className={`rounded-full ${index % 2 === 0 ? "bg-[#7357ff]" : "bg-[#42a5ff]"}`}
        />
      ))}
    </span>
  );
}

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/85 backdrop-blur-xl">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:rounded-lg focus:bg-slate-950 focus:px-3 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to content
      </a>
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 lg:px-8">
        <Link href="/" className="flex items-center gap-2.5" aria-label="CXOps AI home">
          <BrandMark />
          <span className="text-[15px] font-semibold tracking-[-0.02em] text-slate-950">
            CXOps AI
          </span>
        </Link>

        <nav aria-label="Main" className="hidden items-center gap-7 md:flex">
          {MARKETING_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-sm font-medium text-slate-600 transition hover:text-slate-950"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="hidden items-center gap-3 md:flex">
          <Link
            href="/login"
            className="rounded-lg px-3 py-2 text-sm font-medium text-slate-700 transition hover:text-slate-950"
          >
            Sign in
          </Link>
          <Link
            href="/early-access"
            className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800"
          >
            Request a demo
          </Link>
        </div>

        <details className="group relative md:hidden">
          <summary
            className="flex h-10 w-10 cursor-pointer list-none items-center justify-center rounded-lg border border-slate-200 text-slate-700 [&::-webkit-details-marker]:hidden"
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5 group-open:hidden" strokeWidth={1.8} />
            <X className="hidden h-5 w-5 group-open:block" strokeWidth={1.8} />
          </summary>
          <div className="absolute right-0 top-12 w-64 rounded-2xl border border-slate-200 bg-white p-3 shadow-xl">
            <nav aria-label="Mobile" className="flex flex-col">
              {MARKETING_NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-lg px-3 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  {item.label}
                </Link>
              ))}
              <Link
                href="/login"
                className="rounded-lg px-3 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Sign in
              </Link>
              <Link
                href="/early-access"
                className="mt-2 rounded-lg bg-slate-950 px-3 py-2.5 text-center text-sm font-medium text-white"
              >
                Request a demo
              </Link>
            </nav>
          </div>
        </details>
      </div>
    </header>
  );
}

export function SiteFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="border-t border-slate-200 bg-white">
      <div className="mx-auto grid max-w-6xl gap-10 px-5 py-14 sm:grid-cols-2 lg:grid-cols-4 lg:px-8">
        <div className="sm:col-span-2 lg:col-span-1">
          <Link href="/" className="flex items-center gap-2.5" aria-label="CXOps AI home">
            <BrandMark className="h-7 w-7" />
            <span className="text-sm font-semibold text-slate-950">CXOps AI</span>
          </Link>
          <p className="mt-4 max-w-xs text-sm leading-6 text-slate-500">
            An AI website assistant that answers from your own knowledge and hands
            conversations to your team when a person is needed.
          </p>
        </div>
        <FooterColumn
          title="Product"
          links={[
            ["/features", "Features"],
            ["/how-it-works", "How it works"],
            ["/solutions", "Solutions"],
          ]}
        />
        <FooterColumn
          title="Company"
          links={[
            ["/early-access", "Request a demo"],
            ["/faq", "FAQ"],
            ["/platform/evaluating-ai-support-platforms", "Buyer's guide"],
          ]}
        />
        <FooterColumn
          title="Customers"
          links={[
            ["/login", "Sign in"],
            ["/faq#data", "Data handling"],
            ["/faq#pricing", "Pricing"],
          ]}
        />
      </div>
      <div className="border-t border-slate-100">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-5 py-6 text-xs text-slate-400 sm:flex-row sm:items-center sm:justify-between lg:px-8">
          <p>&copy; {year} CXOps AI. All rights reserved.</p>
          <p>Early access - features and availability may change.</p>
        </div>
      </div>
    </footer>
  );
}

function FooterColumn({ title, links }: { title: string; links: [string, string][] }) {
  return (
    <div>
      <p className="text-sm font-semibold text-slate-950">{title}</p>
      <ul className="mt-4 space-y-2.5">
        {links.map(([href, label]) => (
          <li key={href}>
            <Link href={href} className="text-sm text-slate-500 transition hover:text-slate-950">
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Container({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`mx-auto max-w-6xl px-5 lg:px-8 ${className}`}>{children}</div>;
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-600">{children}</p>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  intro,
  center = false,
  as: Tag = "h2",
}: {
  eyebrow?: string;
  title: string;
  intro?: string;
  center?: boolean;
  as?: "h1" | "h2";
}) {
  return (
    <div className={center ? "mx-auto max-w-3xl text-center" : "max-w-3xl"}>
      {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
      <Tag
        className={`mt-3 font-semibold tracking-[-0.035em] text-slate-950 ${
          Tag === "h1" ? "text-4xl sm:text-5xl" : "text-3xl sm:text-4xl"
        }`}
      >
        {title}
      </Tag>
      {intro ? <p className="mt-5 text-lg leading-8 text-slate-600">{intro}</p> : null}
    </div>
  );
}

export function PrimaryCta({ href = "/early-access", children = "Request a demo" }: { href?: string; children?: ReactNode }) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-2 rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white shadow-[0_10px_30px_rgba(15,23,42,0.18)] transition hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500"
    >
      {children}
      <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
    </Link>
  );
}

export function SecondaryCta({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-5 py-3 text-sm font-medium text-slate-800 transition hover:border-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500"
    >
      {children}
    </Link>
  );
}

export function CtaBand({
  title = "See CXOps AI on your own website content",
  body = "Tell us about your business. We review every request personally and will show you how the assistant would answer your customers.",
}: {
  title?: string;
  body?: string;
}) {
  return (
    <section className="py-20">
      <Container>
        <div className="relative overflow-hidden rounded-3xl bg-slate-950 px-6 py-14 text-center sm:px-12">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(115,87,255,0.35),transparent_45%),radial-gradient(circle_at_80%_100%,rgba(66,165,255,0.25),transparent_45%)]"
          />
          <div className="relative">
            <h2 className="mx-auto max-w-2xl text-3xl font-semibold tracking-[-0.03em] text-white sm:text-4xl">
              {title}
            </h2>
            <p className="mx-auto mt-5 max-w-xl text-base leading-7 text-slate-300">{body}</p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link
                href="/early-access"
                className="inline-flex items-center gap-2 rounded-xl bg-white px-5 py-3 text-sm font-medium text-slate-950 transition hover:bg-slate-100"
              >
                Request a demo
                <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
              </Link>
              <Link
                href="/how-it-works"
                className="inline-flex items-center rounded-xl border border-white/25 px-5 py-3 text-sm font-medium text-white transition hover:border-white/50"
              >
                How it works
              </Link>
            </div>
          </div>
        </div>
      </Container>
    </section>
  );
}

export function FeatureCard({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
        {icon}
      </div>
      <h3 className="mt-5 text-base font-semibold text-slate-950">{title}</h3>
      <div className="mt-2 text-sm leading-6 text-slate-600">{children}</div>
    </div>
  );
}
