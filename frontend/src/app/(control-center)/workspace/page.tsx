"use client";

import Link from "next/link";
import {
  BookOpenCheck,
  Globe2,
  Hand,
  LifeBuoy,
  MessageSquareText,
  PieChart,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  Card,
  LoadingBlock,
  Meter,
  Notice,
  PageHeader,
  StatusPill,
  Unavailable,
  buttonSecondary,
  formatDate,
} from "@/components/workspace/ui";
import { useAuthorization } from "@/lib/authorization/context";
import { CommercialApiError, commercialApi, type WorkspaceOverview } from "@/lib/commercial/client";
import {
  LIMIT_REASON_TEXT,
  formatUsd,
  lifecycleInfo,
  usageShare,
} from "@/lib/commercial/onboarding";
import { roleLabel } from "@/lib/commercial/team";

const SESSION_STATUS: Record<string, { label: string; tone: "ok" | "warn" | "info" | "muted" }> = {
  ai_active: { label: "With assistant", tone: "info" },
  human_requested: { label: "Waiting for team", tone: "warn" },
  human_assigned: { label: "With team member", tone: "ok" },
  closed: { label: "Closed", tone: "muted" },
};

export default function WorkspacePage() {
  const { organizationId } = useAuthorization();
  const [data, setData] = useState<WorkspaceOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await commercialApi.workspace());
    } catch (exc) {
      setData(null);
      setError(exc instanceof CommercialApiError ? exc.message : "Could not load your workspace.");
    }
  }, []);

  useEffect(() => {
    // The authorization provider is keyed by organization id, so switching
    // organizations remounts this page with empty state; the guard in render
    // additionally refuses to show figures for a different organization.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount writes the result of an external request (same pattern as the knowledge page)
    void load();
  }, [load]);

  if (error) {
    return (
      <main className="px-5 py-8 sm:px-8">
        <Notice tone="bad">{error}</Notice>
      </main>
    );
  }
  if (!data || data.organization.id !== organizationId) {
    return (
      <main className="px-5 py-8 sm:px-8">
        <LoadingBlock label="Loading your workspace…" />
      </main>
    );
  }

  const lifecycle = lifecycleInfo(data.organization.lifecycle_status);

  return (
    <main className="space-y-6 px-5 py-8 sm:px-8 lg:py-10">
      <PageHeader
        eyebrow="Overview"
        title={data.organization.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusPill tone={lifecycle.tone}>{lifecycle.label}</StatusPill>
            <span>Signed in as {roleLabel(data.your_role).toLowerCase()}.</span>
            {data.organization.website_url ? (
              <span className="text-slate-500">{data.organization.website_url}</span>
            ) : null}
          </span>
        }
        actions={
          data.organization.lifecycle_status !== "active" ? (
            <Link href="/setup" className={buttonSecondary}>
              Continue setup
            </Link>
          ) : null
        }
      />

      {data.usage.available && data.usage.limit_reached ? (
        <Notice tone="warn">
          {LIMIT_REASON_TEXT[data.usage.limit_reason ?? ""] ??
            "This month's AI allowance is reached. New messages go straight to your team."}
        </Notice>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card
          title="Chatbot"
          icon={<MessageSquareText className="h-4 w-4 text-violet-600" aria-hidden="true" />}
          action={
            data.actions.includes("configure_chatbot") ? (
              <Link href="/settings/public-chat" className="text-xs font-medium text-violet-700">
                Settings →
              </Link>
            ) : null
          }
        >
          {data.chatbot.available ? (
            !data.chatbot.configured ? (
              <p className="text-sm text-slate-600">
                No chatbot yet.{" "}
                <Link href="/setup" className="font-medium text-violet-700 underline underline-offset-4">
                  Create it in setup
                </Link>
                .
              </p>
            ) : (
              <dl className="space-y-3 text-sm">
                <div className="flex items-center justify-between">
                  <dt className="text-slate-600">Status</dt>
                  <dd>
                    {data.chatbot.live ? (
                      <StatusPill tone="ok">Live</StatusPill>
                    ) : data.chatbot.enabled ? (
                      <StatusPill tone="warn">On, not serving</StatusPill>
                    ) : (
                      <StatusPill tone="muted">Off</StatusPill>
                    )}
                  </dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt className="text-slate-600">Assistant name</dt>
                  <dd className="font-medium text-slate-900">{data.chatbot.display_name}</dd>
                </div>
                <div>
                  <dt className="flex items-center gap-1.5 text-slate-600">
                    <Globe2 className="h-3.5 w-3.5" aria-hidden="true" /> Approved domains
                  </dt>
                  <dd className="mt-1 space-y-0.5">
                    {data.chatbot.allowed_origins.length === 0 ? (
                      <span className="text-amber-700">None - the widget cannot start anywhere.</span>
                    ) : (
                      data.chatbot.allowed_origins.map((origin) => (
                        <span key={origin} className="block truncate font-mono text-xs text-slate-700">
                          {origin}
                        </span>
                      ))
                    )}
                  </dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt className="text-slate-600">Installation</dt>
                  <dd>
                    {data.chatbot.installation.detected ? (
                      <span className="text-xs text-slate-600">
                        Last visitor chat {formatDate(data.chatbot.installation.last_session_at)}
                      </span>
                    ) : (
                      <StatusPill tone="muted">Not detected yet</StatusPill>
                    )}
                  </dd>
                </div>
                {data.chatbot.enabled && !data.chatbot.live ? (
                  <p className="text-xs leading-5 text-amber-700">
                    The chatbot is switched on but is not serving visitors - the workspace is
                    not approved yet or no domain is approved.
                  </p>
                ) : null}
              </dl>
            )
          ) : (
            <Unavailable reason={data.chatbot.reason} />
          )}
        </Card>

        <Card
          title="Handoff queue"
          icon={<Hand className="h-4 w-4 text-violet-600" aria-hidden="true" />}
          action={
            data.handoff.available ? (
              <Link href="/public-chat" className="text-xs font-medium text-violet-700">
                Open queue →
              </Link>
            ) : null
          }
        >
          {data.handoff.available ? (
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Waiting for a person" value={data.handoff.waiting} highlight={data.handoff.waiting > 0} />
              <Stat label="With a team member" value={data.handoff.assigned} />
              <p className="col-span-2 text-xs text-slate-500">Current state, refreshed when you open this page.</p>
            </div>
          ) : (
            <Unavailable reason={data.handoff.reason} />
          )}
        </Card>

        <Card
          title="Knowledge"
          icon={<BookOpenCheck className="h-4 w-4 text-violet-600" aria-hidden="true" />}
          action={
            data.actions.includes("manage_knowledge") ? (
              <Link href="/knowledge" className="text-xs font-medium text-violet-700">
                Manage →
              </Link>
            ) : null
          }
        >
          {data.knowledge.available ? (
            <div className="space-y-2 text-sm">
              <Stat label="Documents" value={data.knowledge.documents} />
              {data.knowledge.ready ? (
                <p className="text-xs text-slate-500">Last added {formatDate(data.knowledge.last_added_at)}</p>
              ) : (
                <p className="text-xs leading-5 text-amber-700">
                  No knowledge yet. The assistant can only greet visitors and hand off until
                  you add your business information.
                </p>
              )}
            </div>
          ) : (
            <Unavailable reason={data.knowledge.reason} />
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <Card
          title="Recent conversations"
          icon={<MessageSquareText className="h-4 w-4 text-violet-600" aria-hidden="true" />}
          action={
            data.conversations.available ? (
              <span className="text-xs text-slate-500">
                {data.conversations.last_7_days} in the last 7 days
              </span>
            ) : null
          }
        >
          {data.conversations.available ? (
            data.conversations.recent.length === 0 ? (
              <p className="text-sm text-slate-500">
                No visitor conversations yet. They will appear here once the chatbot is live
                on your website.
              </p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {data.conversations.recent.map((row) => {
                  const status = SESSION_STATUS[row.status] ?? { label: row.status, tone: "muted" as const };
                  return (
                    <li key={row.session_id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                      <span className="text-slate-700">Visitor conversation #{row.session_id}</span>
                      <span className="flex items-center gap-3">
                        <span className="hidden text-xs text-slate-500 sm:inline">{formatDate(row.started_at)}</span>
                        <StatusPill tone={status.tone}>{status.label}</StatusPill>
                      </span>
                    </li>
                  );
                })}
              </ul>
            )
          ) : (
            <Unavailable reason={data.conversations.reason} />
          )}
        </Card>

        <Card title="AI usage this month" icon={<PieChart className="h-4 w-4 text-violet-600" aria-hidden="true" />}>
          {data.usage.available ? (
            <div className="space-y-4">
              <Meter
                label="AI conversations"
                value={`${data.usage.turns_used.toLocaleString()} of ${data.usage.turn_limit.toLocaleString()}`}
                share={usageShare(data.usage.turns_used, data.usage.turn_limit)}
                tone={data.usage.limit_reached ? "bad" : usageShare(data.usage.turns_used, data.usage.turn_limit) > 80 ? "warn" : "ok"}
              />
              <Meter
                label="Estimated AI cost"
                value={`${formatUsd(data.usage.estimated_cost_usd)} of ${formatUsd(data.usage.budget_usd)}`}
                share={usageShare(data.usage.estimated_cost_usd, data.usage.budget_usd)}
                tone={data.usage.limit_reached ? "bad" : "ok"}
              />
              <p className="text-xs leading-5 text-slate-500">
                {data.usage.remaining_turns.toLocaleString()} AI conversations remaining since{" "}
                {new Date(data.usage.period_start).toLocaleDateString()}. Costs are estimates
                from recorded usage, not an invoice.
              </p>
              <p className="text-xs text-slate-500">
                Last 24 hours: {data.usage.chat_turns_24h.completed} answered,{" "}
                {data.usage.chat_turns_24h.failed} failed
                {data.usage.chat_turns_24h.p50_seconds !== null
                  ? `, typical reply ${data.usage.chat_turns_24h.p50_seconds}s`
                  : ""}
                .
              </p>
            </div>
          ) : (
            <Unavailable reason={data.usage.reason} />
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card
          title="Team"
          icon={<Users className="h-4 w-4 text-violet-600" aria-hidden="true" />}
          action={
            data.team.available ? (
              <Link href="/team" className="text-xs font-medium text-violet-700">
                Manage team →
              </Link>
            ) : null
          }
        >
          {data.team.available ? (
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Members" value={data.team.members} />
              <Stat label="Pending invitations" value={data.team.pending_invitations} />
            </div>
          ) : (
            <Unavailable reason={data.team.reason} />
          )}
        </Card>

        <Card title="Help and setup" icon={<LifeBuoy className="h-4 w-4 text-violet-600" aria-hidden="true" />}>
          <ul className="space-y-2 text-sm">
            <li>
              <Link href="/setup" className="font-medium text-violet-700 underline-offset-4 hover:underline">
                Setup checklist
              </Link>{" "}
              <span className="text-slate-500">- what is complete and what still needs attention.</span>
            </li>
            <li>
              <Link href="/faq" className="font-medium text-violet-700 underline-offset-4 hover:underline">
                Frequently asked questions
              </Link>{" "}
              <span className="text-slate-500">- data handling, limits and installation.</span>
            </li>
            <li className="text-slate-500">
              Need help? Reply to your onboarding email and our team will assist you.
            </li>
          </ul>
        </Card>
      </div>
    </main>
  );
}

function Stat({ label, value, highlight = false }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div className={`rounded-xl p-3 ${highlight ? "bg-amber-50" : "bg-slate-50"}`}>
      <p className={`text-2xl font-semibold ${highlight ? "text-amber-800" : "text-slate-950"}`}>
        {value.toLocaleString()}
      </p>
      <p className="mt-0.5 text-xs text-slate-500">{label}</p>
    </div>
  );
}
