"use client";

import {
  Activity,
  Bot,
  BrainCircuit,
  Gauge,
  LogOut,
  ShieldCheck,
  Ticket,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

interface ControlCenterShellProps {
  session: {
    displayName: string | null;
    email: string | null;
  };
  children: React.ReactNode;
}

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: Gauge },
  { href: "/tickets", label: "Tickets", icon: Ticket },
  { href: "/agent", label: "AI Agent", icon: Bot },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  { href: "/knowledge", label: "Knowledge", icon: BrainCircuit },
  { href: "/runs", label: "Runs", icon: Workflow },
  { href: "/observability", label: "Observability", icon: Activity },
];

export default function ControlCenterShell({
  session,
  children,
}: ControlCenterShellProps) {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-50 hidden w-[230px] border-r border-slate-200/70 bg-white/80 px-5 py-7 backdrop-blur-2xl xl:flex xl:flex-col">
        <Link href="/tickets" className="flex items-center gap-3 px-2">
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
          {navItems.map((item) => {
            const Icon = item.icon;
            const selected = pathname === item.href;

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
          <div className="flex items-center gap-2.5 mb-4">
            <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.7)]" />

            <div className="flex-1 min-w-0">
              <p className="text-[11px] text-slate-400 truncate">
                {session.email ?? session.displayName ?? "Signed in"}
              </p>

              <p className="text-xs font-medium text-slate-800 truncate">
                Control Center
              </p>
            </div>
          </div>

          <button
            className="w-full flex items-center gap-3 rounded-xl bg-slate-50 px-3.5 py-3 text-sm text-slate-600 transition hover:bg-slate-100 hover:text-slate-950"
            onClick={async () => {
              const { logout } = await import("@/lib/session/logout");
              await logout();
              router.push("/login");
            }}
          >
            <LogOut className="h-[17px] w-[17px] text-slate-400" strokeWidth={1.7} />
            Sign out
          </button>
        </div>
      </aside>

      <div className="xl:pl-[230px]">
        {children}
      </div>
    </div>
  );
}