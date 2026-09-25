import {
  BadgeCheck,
  BookOpenCheck,
  Car,
  CarFront,
  ClipboardCheck,
  KeyRound,
  PlayCircle,
  ShieldAlert,
  SlidersHorizontal,
} from "lucide-react";

import {
  PILOT_CUSTOMER_COUNT,
  PILOT_DISCLAIMER,
  PILOT_INDUSTRY,
  PILOT_KNOWLEDGE_PACK,
  PILOT_OPTIONAL_REPLAY_REFS,
  PILOT_ORGANIZATION_NAME,
  PILOT_QUEUES,
  PILOT_SCENARIOS,
  PILOT_SERVICE_LINES,
  PILOT_SLA_POLICIES,
  PILOT_TICKET_COUNT,
  PILOT_WALKTHROUGH,
} from "@/lib/pilot/automotive-pilot";

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "violet";

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
}) {
  const styles: Record<BadgeVariant, string> = {
    default: "border-slate-200 bg-slate-50 text-slate-600",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning: "border-amber-200 bg-amber-50 text-amber-700",
    danger: "border-rose-200 bg-rose-50 text-rose-700",
    info: "border-blue-200 bg-blue-50 text-blue-700",
    violet: "border-violet-200 bg-violet-50 text-violet-700",
  };

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function SectionHeading({
  icon: Icon,
  title,
  subtitle,
  tone = "violet",
}: {
  icon: typeof Car;
  title: string;
  subtitle: string;
  tone?: "violet" | "blue" | "emerald" | "amber";
}) {
  const tones = {
    violet: "bg-violet-50 text-violet-500",
    blue: "bg-blue-50 text-blue-500",
    emerald: "bg-emerald-50 text-emerald-500",
    amber: "bg-amber-50 text-amber-500",
  }[tone];

  return (
    <div className="mb-4 flex items-center gap-3">
      <div className={`flex h-9 w-9 items-center justify-center rounded-xl ${tones}`}>
        <Icon className="h-4 w-4" />
      </div>

      <div>
        <h2 className="font-medium text-slate-900">{title}</h2>
        <p className="text-xs text-slate-400">{subtitle}</p>
      </div>
    </div>
  );
}

function SmallValue({
  label,
  value,
  valueClassName = "",
}: {
  label: string;
  value: React.ReactNode;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
        {label}
      </p>

      <p
        className={`mt-2 text-xl font-medium tracking-[-0.025em] text-slate-900 ${valueClassName}`}
      >
        {value}
      </p>
    </div>
  );
}

function ServiceLineRow({ key: lineKey }: { key: string }) {
  const line = PILOT_SERVICE_LINES.find((s) => s.key === lineKey);
  if (!line) return null;

  const queues = PILOT_QUEUES.filter((q) => q.service_line === line.key);
  const scenarios = PILOT_SCENARIOS.filter(
    (s) => s.service_line === line.key,
  );

  return (
    <section className="app-panel rounded-[22px] p-6 md:p-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="font-medium text-slate-900">{line.name}</h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">
            {line.description}
          </p>
        </div>

        <div className="flex gap-2">
          <Badge variant="info">{queues.length} queues</Badge>
          <Badge variant="violet">{scenarios.length} scenarios</Badge>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        {queues.map((queue) => (
          <Badge key={queue.key}>{queue.name}</Badge>
        ))}
      </div>
    </section>
  );
}

export default function AutomotivePilotPage() {
  const totalScenarioScenarios = PILOT_SCENARIOS.length;

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Automotive Pilot
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                A1 showroom workspace
              </p>
            </div>

            <Badge variant="violet">Static demo showcase</Badge>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              {PILOT_ORGANIZATION_NAME}
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              A dealership-shaped pilot.
              <span className="gradient-text"> Fully synthetic.</span>
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              A self-contained demo tenant — SLA policies, queues, customers,
              conversations and a knowledge pack — that exercises the CXOps AI
              agent across vehicle acquisition and auto parts with no real
              customers, credentials or external side effects.
            </p>
          </section>

          <section className="mb-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <SmallValue label="Organization" value={PILOT_ORGANIZATION_NAME} />
            <SmallValue label="Industry" value={PILOT_INDUSTRY} />
            <SmallValue label="Tickets" value={PILOT_TICKET_COUNT} />
            <SmallValue label="Customers" value={PILOT_CUSTOMER_COUNT} />
          </section>

          <section className="mb-8 rounded-[20px] border border-amber-200 bg-amber-50/55 p-5">
            <div className="flex gap-3">
              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />

              <div>
                <p className="text-sm font-medium text-amber-800">
                  Synthetic tenant guardrails
                </p>

                <p className="mt-1 max-w-3xl text-xs leading-5 text-amber-700/80">
                  {`Every seeded customer uses an @example.com address, every conversation stays local (provider “cxops”, no external thread ids), and no AI run or evaluation result is ever fabricated. The agent can still be exercised optionally via the safe replay path — approvals apply and automatic queueing stays off by default.`}
                </p>
              </div>
            </div>
          </section>

          <section className="mb-8">
            <SectionHeading
              icon={CarFront}
              title="Service Lines"
              subtitle="Three operating domains, each with its own queues"
            />

            <div className="grid gap-4 xl:grid-cols-3">
              {PILOT_SERVICE_LINES.map((line) => (
                <ServiceLineRow key={line.key} />
              ))}
            </div>
          </section>

          <section className="mb-8">
            <SectionHeading
              icon={SlidersHorizontal}
              title="SLA Policies"
              subtitle="First-response and resolution targets per service line"
              tone="blue"
            />

            <div className="grid gap-4 xl:grid-cols-3">
              {PILOT_SLA_POLICIES.map((policy) => (
                <div
                  key={policy.name}
                  className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-5"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-slate-900">
                      {policy.name}
                    </span>

                    {policy.is_default && (
                      <Badge variant="success">Default</Badge>
                    )}
                  </div>

                  <dl className="mt-4 space-y-2 text-xs text-slate-500">
                    <div className="flex justify-between gap-4">
                      <dt>Normal first response</dt>
                      <dd className="font-medium text-slate-800">
                        {policy.first_response_minutes.normal / 60} business
                        hrs
                      </dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt>High first response</dt>
                      <dd className="font-medium text-slate-800">
                        {policy.first_response_minutes.high / 60} business hrs
                      </dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt>Normal resolution</dt>
                      <dd className="font-medium text-slate-800">
                        {policy.resolution_minutes.normal / 60} business hrs
                      </dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt>High resolution</dt>
                      <dd className="font-medium text-slate-800">
                        {policy.resolution_minutes.high / 60} business hrs
                      </dd>
                    </div>
                  </dl>
                </div>
              ))}
            </div>
          </section>

          <section className="mb-8">
            <SectionHeading
              icon={KeyRound}
              title="Queues"
              subtitle="The six A1 queues, grouped by service line"
              tone="emerald"
            />

            <div className="app-panel overflow-hidden rounded-[22px]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200/70 text-left text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                    <th className="py-3 pl-6 pr-4">Queue</th>
                    <th className="py-3 pr-4">Service line</th>
                    <th className="py-3 pr-4">Key</th>
                  </tr>
                </thead>

                <tbody>
                  {PILOT_QUEUES.map((queue) => (
                    <tr
                      key={queue.key}
                      className="border-b border-slate-200/60 last:border-0"
                    >
                      <td className="py-3 pl-6 pr-4 font-medium text-slate-800">
                        {queue.name}
                      </td>
                      <td className="py-3 pr-4 text-slate-600">
                        {
                          PILOT_SERVICE_LINES.find(
                            (s) => s.key === queue.service_line,
                          )?.name
                        }
                      </td>
                      <td className="py-3 pr-6 text-slate-500">
                        <code>{queue.key}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="mb-8">
            <SectionHeading
              icon={BookOpenCheck}
              title="Knowledge Pack"
              subtitle="Eight pilot documents with an explicit non-guidance disclaimer"
              tone="blue"
            />

            <div className="grid gap-3 md:grid-cols-2">
              {PILOT_KNOWLEDGE_PACK.map((doc) => (
                <div
                  key={doc.title}
                  className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4"
                >
                  <p className="text-sm font-medium text-slate-800">
                    {doc.title}
                  </p>

                  <p className="mt-1 text-[11px] text-slate-400">
                    {doc.department}
                  </p>
                </div>
              ))}
            </div>

            <div className="mt-4 rounded-2xl border border-violet-200 bg-violet-50/60 p-4">
              <p className="text-xs leading-5 text-violet-700/90">
                {`Disclaimer carried on every document: “${PILOT_DISCLAIMER}”`}
              </p>
            </div>
          </section>

          <section className="mb-8">
            <SectionHeading
              icon={ClipboardCheck}
              title={`Scenario Catalog (${totalScenarioScenarios})`}
              subtitle="Nine acquisition steps, eight parts cases, one safety case"
              tone="amber"
            />

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {PILOT_SCENARIOS.map((scenario) => (
                <div
                  key={scenario.id}
                  className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4"
                >
                  <div className="flex items-center justify-between gap-3">
                    <code className="text-[11px] text-slate-400">
                      {scenario.id}
                    </code>

                    {scenario.risk && (
                      <Badge variant="danger">Risk</Badge>
                    )}
                  </div>

                  <p className="mt-2 font-medium text-slate-800">
                    {scenario.title}
                  </p>

                  <p className="mt-2 text-xs leading-5 text-slate-500">
                    {scenario.description}
                  </p>

                  <p className="mt-3 text-[11px] text-slate-400">
                    Ticket ref{" "}
                    <code className="text-slate-500">{scenario.ref}</code>
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section className="mb-8">
            <SectionHeading
              icon={PlayCircle}
              title="Optional Replay"
              subtitle="Safe tickets the real agent workflow can be exercised on"
              tone="emerald"
            />

            <div className="app-panel rounded-[22px] p-6">
              <p className="text-xs leading-6 text-slate-500">
                Running{" "}
                <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px]">
                  scripts/run_automotive_pilot.py
                </code>{" "}
                analyzes these tickets through the production pipeline. External
                execution stays off by default, approvals still apply, and the
                pilot tenant is selected purely by its exact organization name.
              </p>

              <div className="mt-4 flex flex-wrap gap-2">
                {PILOT_OPTIONAL_REPLAY_REFS.map((ref) => (
                  <Badge key={ref} variant="info">
                    {ref}
                  </Badge>
                ))}
              </div>
            </div>
          </section>

          <section>
            <SectionHeading
              icon={BadgeCheck}
              title="Demo Walkthrough"
              subtitle="From empty database to a seeded showroom"
              tone="violet"
            />

            <div className="app-panel rounded-[22px] p-6 md:p-7">
              <ol className="grid gap-4 md:grid-cols-2">
                {PILOT_WALKTHROUGH.map((step, index) => (
                  <li
                    key={step.title}
                    className="rounded-2xl border border-slate-200/70 bg-[#fbfcff] p-4"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-violet-50 text-[11px] font-semibold text-violet-600">
                        {index + 1}
                      </span>

                      <p className="text-sm font-medium text-slate-900">
                        {step.title}
                      </p>
                    </div>

                    <p className="mt-3 text-xs leading-5 text-slate-500">
                      {step.detail}
                    </p>
                  </li>
                ))}
              </ol>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}