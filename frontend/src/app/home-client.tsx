"use client";

import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  Activity,
  ArrowRight,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Cpu,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Ticket,
  Workflow,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import AppSidebar from "@/components/app-sidebar";

gsap.registerPlugin(ScrollTrigger, useGSAP);

type AgentKPIs = {
  total_runs: number;
  escalation_rate: number;
  human_review_rate: number;
  no_action_rate: number;
  approval_required_rate: number;
  pending_approval_rate: number;
  auto_approved_runs: number;
  autonomous_execution_rate: number;
  autonomous_executed_runs: number;
  autonomous_success_rate: number;
  execution_success_rate: number;
  queue_retry_rate: number;
  queue_failure_rate: number;
  average_job_attempts: number;
};

type AgentROI = {
  total_runs: number;
  instrumented_runs: number;
  instrumented_autonomous_executed_runs: number;
  support_hourly_cost_usd: number;
  minutes_saved_per_autonomous_execution: number;
  estimated_minutes_saved: number;
  estimated_hours_saved: number;
  estimated_labor_savings_usd: number;
  agent_ai_cost_usd: number;
  estimated_net_savings_usd: number;
  pricing_configured: boolean;
  measurement_status: string;
  minimum_autonomous_samples: number;
  sample_size_sufficient: boolean;
  provisional_roi_percent: number | null;
  roi_percent: number | null;
};

type Health = Record<string, unknown>;

function formatPercent(value?: number) {
  return `${(value ?? 0).toFixed(1)}%`;
}

function formatMoney(value?: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value ?? 0);
}

function KpiCard({
  title,
  value,
  description,
  icon: Icon,
  iconClass,
}: {
  title: string;
  value: string;
  description: string;
  icon: LucideIcon;
  iconClass: string;
}) {
  return (
    <div className="kpi-card min-h-[180px] border-r border-slate-200/70 p-7 last:border-r-0">
      <div className="flex items-center gap-3">
        <div
          className={`flex h-9 w-9 items-center justify-center rounded-xl ${iconClass}`}
        >
          <Icon className="h-4 w-4" strokeWidth={1.8} />
        </div>

        <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-slate-500">
          {title}
        </p>
      </div>

      <p className="editorial-number mt-7 text-4xl font-medium tracking-[-0.04em] text-slate-950">
        {value}
      </p>

      <p className="mt-4 max-w-[220px] text-xs leading-5 text-slate-500">
        {description}
      </p>
    </div>
  );
}

function OperatingRow({
  icon: Icon,
  label,
  value,
  iconClass,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  iconClass: string;
}) {
  return (
    <div className="flex items-center justify-between border-b border-slate-200/70 py-5 last:border-0">
      <div className="flex items-center gap-3">
        <span
          className={`flex h-9 w-9 items-center justify-center rounded-xl ${iconClass}`}
        >
          <Icon className="h-4 w-4" />
        </span>

        <span className="text-sm text-slate-600">
          {label}
        </span>
      </div>

      <span className="editorial-number text-lg font-medium text-slate-950">
        {value}
      </span>
    </div>
  );
}

function CyberVisual() {
  return (
    <div className="relative mx-auto h-[400px] w-full max-w-[530px]">
      <div className="dot-grid absolute inset-0 opacity-35 [mask-image:radial-gradient(circle,black,transparent_72%)]" />

      <div className="absolute left-[19%] top-[21%] h-44 w-44 rotate-[30deg] rounded-[32px] border border-[#8679ff]/25 bg-gradient-to-br from-white/90 via-[#e8ecff]/60 to-[#9c92ff]/30 shadow-[0_35px_80px_rgba(88,76,255,0.15)] backdrop-blur-md" />

      <div className="absolute left-[44%] top-[11%] h-52 w-52 rotate-[30deg] rounded-[34px] border border-[#5d6dff]/25 bg-gradient-to-br from-white/75 via-[#dce4ff]/60 to-[#675aff]/25 shadow-[0_30px_100px_rgba(101,89,255,0.18)] backdrop-blur-md" />

      <div className="absolute left-[43%] top-[41%] h-36 w-36 rotate-[30deg] rounded-[28px] border border-[#6bbcff]/30 bg-gradient-to-br from-white/90 to-[#8dbaff]/30 shadow-[0_30px_70px_rgba(83,164,255,0.18)] backdrop-blur-md" />

      <div className="absolute left-[25%] top-[46%] h-28 w-28 rotate-[30deg] rounded-[24px] border border-[#796aff]/20 bg-white/55 backdrop-blur-xl" />

      <div className="absolute left-[13%] right-[7%] top-[49%] h-px bg-gradient-to-r from-transparent via-[#806fff]/40 to-transparent" />

      <div className="absolute bottom-[16%] left-[12%] right-[7%] h-px rotate-[-18deg] bg-gradient-to-r from-transparent via-[#5fa8ff]/30 to-transparent" />

      <span className="absolute left-[10%] top-[48%] h-2 w-2 rounded-full bg-[#7468ff] shadow-[0_0_14px_#7468ff]" />

      <span className="absolute right-[11%] top-[27%] h-2 w-2 rounded-full bg-[#5fa8ff] shadow-[0_0_14px_#5fa8ff]" />
    </div>
  );
}

export default function Home() {
  const root = useRef<HTMLDivElement>(null);

  const [kpis, setKpis] =
    useState<AgentKPIs | null>(null);
  const [roi, setRoi] =
    useState<AgentROI | null>(null);
  const [health, setHealth] =
    useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [
        healthResponse,
        kpiResponse,
        roiResponse,
      ] = await Promise.all([
        fetch("/api/backend/health", {
          cache: "no-store",
        }),
        fetch(
          "/api/backend/observability/agent/kpis",
          {
            cache: "no-store",
          },
        ),
        fetch(
          "/api/backend/observability/agent/roi",
          {
            cache: "no-store",
          },
        ),
      ]);

      if (
        !healthResponse.ok ||
        !kpiResponse.ok ||
        !roiResponse.ok
      ) {
        throw new Error(
          "One or more CXOps services are unavailable.",
        );
      }

      setHealth(
        (await healthResponse.json()) as Health,
      );
      setKpis(
        (await kpiResponse.json()) as AgentKPIs,
      );
      setRoi(
        (await roiResponse.json()) as AgentROI,
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to load dashboard.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadDashboard();
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadDashboard]);

  const healthy = useMemo(() => {
    const status = health?.status;

    return (
      status === "healthy" ||
      status === "ok" ||
      status === "running"
    );
  }, [health]);

  useGSAP(
    () => {
      if (
        window.matchMedia(
          "(prefers-reduced-motion: reduce)",
        ).matches
      ) {
        return;
      }

      gsap
        .timeline({
          defaults: {
            ease: "power3.out",
          },
        })
        .from(".hero-element", {
          opacity: 0,
          y: 28,
          stagger: 0.09,
          duration: 0.9,
        })
        .from(
          ".cyber-visual",
          {
            opacity: 0,
            scale: 0.94,
            x: 30,
            duration: 1.1,
          },
          "-=0.75",
        )
        .from(
          ".kpi-card",
          {
            opacity: 0,
            y: 20,
            stagger: 0.07,
            duration: 0.7,
          },
          "-=0.6",
        );

      gsap.utils
        .toArray<HTMLElement>(".scroll-reveal")
        .forEach((element) => {
          gsap.from(element, {
            opacity: 0,
            y: 45,
            duration: 1,
            ease: "power3.out",
            scrollTrigger: {
              trigger: element,
              start: "top 88%",
              once: true,
            },
          });
        });

      gsap.to(".cyber-visual", {
        y: 50,
        ease: "none",
        scrollTrigger: {
          trigger: ".hero-area",
          start: "top top",
          end: "bottom top",
          scrub: 1.4,
        },
      });
    },
    {
      scope: root,
    },
  );

  const navigationCards = [
    {
      href: "/tickets",
      title: "Tickets",
      description:
        "Track and manage customer conversations",
      icon: Ticket,
      iconClass:
        "bg-blue-50 text-blue-500",
    },
    {
      href: "/agent",
      title: "AI Agent",
      description:
        "Configure and monitor AI agents",
      icon: Bot,
      iconClass:
        "bg-violet-50 text-violet-500",
    },
    {
      href: "/approvals",
      title: "Approvals",
      description:
        "Review and approve agent actions",
      icon: ShieldCheck,
      iconClass:
        "bg-emerald-50 text-emerald-500",
    },
    {
      href: "/knowledge",
      title: "Knowledge",
      description:
        "Manage your knowledge base and RAG",
      icon: BrainCircuit,
      iconClass:
        "bg-indigo-50 text-indigo-500",
    },
    {
      href: "/runs",
      title: "Runs",
      description:
        "View agent runs and execution history",
      icon: Workflow,
      iconClass:
        "bg-fuchsia-50 text-fuchsia-500",
    },
    {
      href: "/observability",
      title: "Observability",
      description:
        "Monitor performance and system health",
      icon: Activity,
      iconClass:
        "bg-sky-50 text-sky-500",
    },
  ];

  return (
    <div ref={root}>
      <AppSidebar active="/" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/65 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div className="xl:hidden">
              <p className="text-sm font-semibold">
                CXOps AI
              </p>
            </div>

            <div />

            <div className="flex items-center gap-3">
              <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-white/85 px-4 py-2 text-xs text-slate-600 shadow-sm sm:flex">
                <span
                  className={`h-2 w-2 rounded-full ${
                    healthy
                      ? "bg-emerald-400"
                      : "bg-amber-400"
                  }`}
                />

                {healthy
                  ? "Systems operational"
                  : "Checking systems"}
              </div>

              <button
                onClick={() =>
                  void loadDashboard()
                }
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white shadow-sm transition hover:border-violet-300"
              >
                <RefreshCw
                  className={`h-4 w-4 text-slate-600 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
              </button>

              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#111827] text-xs font-medium text-white">
                AI
              </div>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[120px] lg:px-10">
          <section className="hero-area relative grid min-h-[540px] items-center gap-8 lg:grid-cols-[1.03fr_0.97fr]">
            <div className="relative z-10">
              <p className="hero-element text-[10px] font-medium uppercase tracking-[0.28em] text-slate-400">
                Intelligent customer operations
              </p>

              <h1 className="hero-element mt-8 max-w-[690px] text-[clamp(4rem,7vw,7.4rem)] font-light leading-[0.84] tracking-[-0.075em] text-slate-950">
                AI operations,
                <span className="gradient-text mt-2 block font-serif italic">
                  under control.
                </span>
              </h1>

              <p className="hero-element mt-9 max-w-[600px] text-base leading-8 text-slate-600">
                CXOps AI is a demonstration
                customer experience operations
                platform for support-ticket
                workflows, RAG-assisted knowledge
                retrieval, AI agents, human
                approvals, execution audit trails,
                and operational observability.
              </p>

              <div className="hero-element mt-8 flex flex-wrap gap-3">
                <Link
                  href="/agent"
                  className="group inline-flex items-center gap-3 rounded-full bg-[#111827] px-6 py-3.5 text-sm font-medium text-white shadow-[0_12px_30px_rgba(17,24,39,0.15)] transition hover:-translate-y-0.5"
                >
                  Open AI Agent

                  <ArrowRight className="h-4 w-4 transition group-hover:translate-x-1" />
                </Link>

                <Link
                  href="/observability"
                  className="inline-flex items-center gap-3 rounded-full border border-slate-200 bg-white/85 px-6 py-3.5 text-sm text-slate-700 shadow-sm transition hover:border-violet-300"
                >
                  View telemetry
                </Link>
              </div>
            </div>

            <div className="cyber-visual hidden lg:block">
              <CyberVisual />
            </div>
          </section>

          {error && (
            <div className="mb-7 rounded-2xl border border-red-200 bg-red-50/90 p-4 text-sm text-red-700">
              {error}
            </div>
          )}

          <section className="app-panel scroll-reveal overflow-hidden rounded-[22px]">
            <div className="grid md:grid-cols-2 xl:grid-cols-4">
              <KpiCard
                title="Agent runs"
                value={`${kpis?.total_runs ?? 0}`}
                description="Persisted AI decisions across the support workflow"
                icon={Cpu}
                iconClass="bg-violet-50 text-violet-500"
              />

              <KpiCard
                title="Autonomous executions"
                value={`${
                  kpis?.autonomous_executed_runs ??
                  0
                }`}
                description="Low-risk operations completed without unnecessary delay"
                icon={Zap}
                iconClass="bg-blue-50 text-blue-500"
              />

              <KpiCard
                title="Autonomous success"
                value={formatPercent(
                  kpis?.autonomous_success_rate,
                )}
                description="Success rate for safely auto-approved execution paths"
                icon={Bot}
                iconClass="bg-emerald-50 text-emerald-500"
              />

              <KpiCard
                title="Execution success"
                value={formatPercent(
                  kpis?.execution_success_rate,
                )}
                description="Overall execution reliability across agent tool runs"
                icon={Activity}
                iconClass="bg-fuchsia-50 text-fuchsia-500"
              />
            </div>
          </section>

          <section className="scroll-reveal mt-7 grid gap-7 xl:grid-cols-[1.45fr_0.85fr]">
            <div className="app-panel rounded-[22px] p-7">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-slate-900">
                    Execution overview
                  </p>

                  <p className="mt-1 text-xs text-slate-400">
                    Live performance of your AI
                    operations
                  </p>
                </div>

                <span className="rounded-full border border-slate-200 bg-white px-4 py-2 text-[11px] text-slate-500">
                  Live
                </span>
              </div>

              <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  [
                    "Total runs",
                    `${kpis?.total_runs ?? 0}`,
                  ],
                  [
                    "Autonomous",
                    `${kpis?.autonomous_executed_runs ?? 0}`,
                  ],
                  [
                    "Success rate",
                    formatPercent(
                      kpis?.execution_success_rate,
                    ),
                  ],
                  [
                    "Human review",
                    formatPercent(
                      kpis?.human_review_rate,
                    ),
                  ],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="rounded-2xl border border-slate-200/80 bg-white/65 p-4"
                  >
                    <p className="text-[10px] text-slate-400">
                      {label}
                    </p>

                    <p className="editorial-number mt-3 text-2xl font-medium tracking-[-0.04em] text-slate-950">
                      {value}
                    </p>
                  </div>
                ))}
              </div>

              <div className="relative mt-7 h-[220px] overflow-hidden rounded-2xl bg-gradient-to-b from-[#fbfcff] to-[#f8faff]">
                <div className="soft-grid absolute inset-0 opacity-50" />

                <svg
                  viewBox="0 0 800 220"
                  className="absolute inset-0 h-full w-full"
                  preserveAspectRatio="none"
                >
                  <defs>
                    <linearGradient
                      id="lineGradient"
                      x1="0"
                      x2="1"
                    >
                      <stop
                        offset="0%"
                        stopColor="#a866ff"
                      />
                      <stop
                        offset="50%"
                        stopColor="#5f74ff"
                      />
                      <stop
                        offset="100%"
                        stopColor="#4db5ff"
                      />
                    </linearGradient>

                    <linearGradient
                      id="fillGradient"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="0%"
                        stopColor="#7c6cff"
                        stopOpacity="0.18"
                      />
                      <stop
                        offset="100%"
                        stopColor="#7c6cff"
                        stopOpacity="0"
                      />
                    </linearGradient>
                  </defs>

                  <path
                    d="M0 164 C80 160 92 115 145 137 C205 163 220 99 284 120 C350 143 368 78 428 105 C496 138 516 70 579 91 C640 112 664 63 725 84 C759 96 780 111 800 104 L800 220 L0 220 Z"
                    fill="url(#fillGradient)"
                  />

                  <path
                    d="M0 164 C80 160 92 115 145 137 C205 163 220 99 284 120 C350 143 368 78 428 105 C496 138 516 70 579 91 C640 112 664 63 725 84 C759 96 780 111 800 104"
                    fill="none"
                    stroke="url(#lineGradient)"
                    strokeWidth="4"
                    strokeLinecap="round"
                  />
                </svg>
              </div>
            </div>

            <div className="app-panel rounded-[22px] p-7">
              <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-slate-900">
                Estimated value
              </p>

              <p className="mt-1 text-xs text-slate-400">
                Measured impact of automation
              </p>

              <div className="mt-9 flex items-center justify-between gap-6">
                <div>
                  <p className="editorial-number text-5xl font-medium tracking-[-0.05em] text-slate-950">
                    {formatMoney(
                      roi?.estimated_net_savings_usd,
                    )}
                  </p>

                  <p className="mt-2 text-sm text-slate-500">
                    Net estimated savings
                  </p>
                </div>

                <div
                  className="h-[108px] w-[108px] rounded-full p-[9px]"
                  style={{
                    background: `conic-gradient(
                      #725cff 0deg,
                      #55a7ff ${
                        (kpis?.execution_success_rate ??
                          0) * 3.6
                      }deg,
                      #e9edff 0deg
                    )`,
                  }}
                >
                  <div className="flex h-full w-full items-center justify-center rounded-full bg-white">
                    <span className="text-sm font-semibold text-slate-700">
                      {formatPercent(
                        kpis?.execution_success_rate,
                      )}
                    </span>
                  </div>
                </div>
              </div>

              <div className="mt-8 divide-y divide-slate-200/70">
                {[
                  [
                    "Time saved",
                    `${
                      roi?.estimated_minutes_saved ??
                      0
                    } min`,
                  ],
                  [
                    "Labor value",
                    formatMoney(
                      roi?.estimated_labor_savings_usd,
                    ),
                  ],
                  [
                    "AI cost",
                    formatMoney(
                      roi?.agent_ai_cost_usd,
                    ),
                  ],
                  [
                    "Autonomous samples",
                    `${
                      roi?.instrumented_autonomous_executed_runs ??
                      0
                    } / ${
                      roi?.minimum_autonomous_samples ??
                      0
                    }`,
                  ],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="flex justify-between py-4 text-sm"
                  >
                    <span className="text-slate-500">
                      {label}
                    </span>

                    <span className="editorial-number font-medium text-slate-950">
                      {value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="app-panel scroll-reveal mt-7 grid overflow-hidden rounded-[22px] lg:grid-cols-[0.8fr_1fr_1.1fr]">
            <div className="border-b border-slate-200/70 p-8 lg:border-b-0 lg:border-r">
              <p className="text-[10px] font-semibold uppercase tracking-[0.17em] text-[#765cff]">
                Operating model
              </p>

              <h2 className="mt-8 text-4xl font-light leading-[1.03] tracking-[-0.055em] text-slate-950">
                Human when
                <br />
                it matters.
              </h2>

              <p className="gradient-text mt-4 font-serif text-4xl italic leading-tight">
                Autonomous
                <br />
                when it doesn&apos;t.
              </p>
            </div>

            <div className="border-b border-slate-200/70 px-8 py-5 lg:border-b-0 lg:border-r">
              <OperatingRow
                icon={ShieldCheck}
                label="Approval required"
                value={formatPercent(
                  kpis?.approval_required_rate,
                )}
                iconClass="bg-violet-50 text-violet-500"
              />

              <OperatingRow
                icon={Bot}
                label="Human review"
                value={formatPercent(
                  kpis?.human_review_rate,
                )}
                iconClass="bg-blue-50 text-blue-500"
              />

              <OperatingRow
                icon={CheckCircle2}
                label="Autonomous execution"
                value={formatPercent(
                  kpis?.autonomous_execution_rate,
                )}
                iconClass="bg-emerald-50 text-emerald-500"
              />

              <OperatingRow
                icon={Activity}
                label="No-action decisions"
                value={formatPercent(
                  kpis?.no_action_rate,
                )}
                iconClass="bg-slate-100 text-slate-500"
              />
            </div>

            <div className="flex flex-col justify-between p-8">
              <p className="max-w-sm text-sm leading-7 text-slate-600">
                CXOps does not treat automation
                as an all-or-nothing decision.
                Every proposed action passes
                through explicit risk and
                authorization rules before it
                can touch an external system.
              </p>

              <Link
                href="/agent"
                className="mt-10 inline-flex items-center gap-2 text-sm font-medium text-[#6654ff]"
              >
                Learn about our approach

                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </section>

          <section className="scroll-reveal mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            {navigationCards.map((item) => {
              const Icon = item.icon;

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className="app-panel group flex min-h-[245px] flex-col rounded-[20px] p-6 transition duration-300 hover:-translate-y-1 hover:border-[#b9b0ff]"
                >
                  <div
                    className={`flex h-10 w-10 items-center justify-center rounded-xl ${item.iconClass}`}
                  >
                    <Icon className="h-5 w-5" />
                  </div>

                  <h3 className="mt-7 text-lg font-medium tracking-[-0.03em] text-slate-950">
                    {item.title}
                  </h3>

                  <p className="mt-2 text-xs leading-5 text-slate-500">
                    {item.description}
                  </p>

                  <div className="mt-auto flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white transition group-hover:border-violet-300">
                    <ArrowRight className="h-4 w-4 transition group-hover:translate-x-1" />
                  </div>
                </Link>
              );
            })}
          </section>

          <footer className="mt-16 flex items-center justify-between border-t border-slate-200/70 py-8 text-xs text-slate-400">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-[#725cff]" />
              CXOps AI
            </div>

            <p>
              Intelligent Customer Experience
              Automation
            </p>
          </footer>
        </main>
      </div>
    </div>
  );
}
