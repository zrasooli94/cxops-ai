"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  Gauge,
  ShieldCheck,
  Ticket,
  Workflow,
} from "lucide-react";
import Link from "next/link";

const items = [
  { href: "/", label: "Home", icon: Gauge },
  { href: "/tickets", label: "Tickets", icon: Ticket },
  { href: "/agent", label: "AI Agent", icon: Bot },
  {
    href: "/approvals",
    label: "Approvals",
    icon: ShieldCheck,
  },
  {
    href: "/knowledge",
    label: "Knowledge",
    icon: BrainCircuit,
  },
  { href: "/runs", label: "Runs", icon: Workflow },
  {
    href: "/observability",
    label: "Observability",
    icon: Activity,
  },
];

export default function AppSidebar({
  active = "/",
}: {
  active?: string;
}) {
  return (
    <aside className="fixed inset-y-0 left-0 z-50 hidden w-[230px] border-r border-slate-200/70 bg-white/80 px-5 py-7 backdrop-blur-2xl xl:flex xl:flex-col">
      <Link href="/" className="flex items-center gap-3 px-2">
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

      <nav className="mt-12 space-y-1.5">
        {items.map((item) => {
          const Icon = item.icon;
          const selected = active === item.href;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group flex items-center gap-3 rounded-xl px-3.5 py-3 text-sm transition-all duration-300 ${
                selected
                  ? "bg-gradient-to-r from-[#eef4ff] to-[#f5f1ff] text-[#4d49d8]"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
              }`}
            >
              <Icon
                className={`h-[17px] w-[17px] ${
                  selected
                    ? "text-[#5f63ff]"
                    : "text-slate-500 group-hover:text-slate-800"
                }`}
                strokeWidth={1.7}
              />

              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto rounded-2xl border border-slate-200/80 bg-white/85 p-4 shadow-[0_10px_35px_rgba(79,90,130,0.05)]">
        <div className="flex items-center gap-2.5">
          <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.7)]" />

          <div>
            <p className="text-[11px] text-slate-400">
              All systems
            </p>

            <p className="text-xs font-medium text-slate-800">
              Operational
            </p>
          </div>
        </div>
      </div>
    </aside>
  );
}
