"use client";

import {
  Activity,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clock3,
  Filter,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Ticket,
  TriangleAlert,
  Workflow,
  XCircle,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import AppSidebar from "@/components/app-sidebar";

type ToolPlanItem = {
  tool: string;
  arguments: Record<string, unknown>;
  risk_level: "low" | "medium" | "high";
  requires_approval: boolean;
  authorized: boolean;
};

type AgentRun = {
  run_id: string;
  ticket_id: number;
  action: string;
  status: string;
  reason: string;
  recommended_team: string | null;
  recommended_priority: string | null;
  response_draft: string | null;
  requires_human_approval: boolean;
  reviewer_note: string | null;
  workflow_path: string[];
  tool_plan: ToolPlanItem[];
};

type TicketRecord = {
  id: number;
  external_id: string | null;
  subject: string;
  requester_email: string;
};

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
    default:
      "border-slate-200 bg-slate-50 text-slate-600",
    success:
      "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning:
      "border-amber-200 bg-amber-50 text-amber-700",
    danger:
      "border-rose-200 bg-rose-50 text-rose-700",
    info:
      "border-blue-200 bg-blue-50 text-blue-700",
    violet:
      "border-violet-200 bg-violet-50 text-violet-700",
  };

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function formatText(value: string) {
  return value
    .replaceAll("_", " ")
    .split(" ")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() +
        part.slice(1),
    )
    .join(" ");
}

function statusVariant(
  status: string,
): BadgeVariant {
  switch (status) {
    case "executed":
    case "approved":
      return "success";

    case "pending_approval":
    case "review_required":
    case "executing":
      return "warning";

    case "execution_failed":
    case "rejected":
      return "danger";

    case "superseded":
    case "no_action":
      return "default";

    default:
      return "info";
  }
}

function actionVariant(
  action: string,
): BadgeVariant {
  switch (action) {
    case "escalate":
      return "danger";

    case "human_review":
      return "warning";

    case "respond":
    case "route":
      return "info";

    case "internal_note":
      return "success";

    default:
      return "default";
  }
}

function riskVariant(
  risk: ToolPlanItem["risk_level"],
): BadgeVariant {
  if (risk === "high") {
    return "danger";
  }

  if (risk === "medium") {
    return "warning";
  }

  return "success";
}

function StatCard({
  title,
  value,
  note,
  tone,
}: {
  title: string;
  value: number;
  note: string;
  tone:
    | "violet"
    | "emerald"
    | "amber"
    | "rose";
}) {
  const styles = {
    violet:
      "bg-violet-50 text-violet-500",
    emerald:
      "bg-emerald-50 text-emerald-500",
    amber:
      "bg-amber-50 text-amber-500",
    rose:
      "bg-rose-50 text-rose-500",
  }[tone];

  return (
    <div className="app-panel rounded-[20px] p-6">
      <div
        className={`flex h-9 w-9 items-center justify-center rounded-xl ${styles}`}
      >
        {tone === "violet" ? (
          <Workflow className="h-4 w-4" />
        ) : tone === "emerald" ? (
          <CheckCircle2 className="h-4 w-4" />
        ) : tone === "amber" ? (
          <Clock3 className="h-4 w-4" />
        ) : (
          <XCircle className="h-4 w-4" />
        )}
      </div>

      <p className="mt-5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
        {title}
      </p>

      <p className="editorial-number mt-2 text-4xl font-medium tracking-[-0.045em] text-slate-950">
        {value}
      </p>

      <p className="mt-3 text-xs leading-5 text-slate-500">
        {note}
      </p>
    </div>
  );
}

export default function RunsPage() {
  const [runs, setRuns] =
    useState<AgentRun[]>([]);

  const [tickets, setTickets] =
    useState<TicketRecord[]>([]);

  const [
    selectedRun,
    setSelectedRun,
  ] = useState<AgentRun | null>(
    null,
  );

  const [search, setSearch] =
    useState("");

  const [
    statusFilter,
    setStatusFilter,
  ] = useState("all");

  const [
    actionFilter,
    setActionFilter,
  ] = useState("all");

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState("");

  const loadData =
    useCallback(async () => {
      setLoading(true);
      setError("");

      try {
        const [
          runsResponse,
          ticketsResponse,
        ] = await Promise.all([
          fetch(
            "/api/backend/agent/runs?limit=200",
            {
              cache: "no-store",
            },
          ),
          fetch(
            "/api/backend/tickets",
            {
              cache: "no-store",
            },
          ),
        ]);

        if (
          !runsResponse.ok ||
          !ticketsResponse.ok
        ) {
          throw new Error(
            "Failed to load agent audit data.",
          );
        }

        const runRows =
          (await runsResponse.json()) as AgentRun[];

        const ticketData =
          await ticketsResponse.json();

        const ticketRows: TicketRecord[] =
          Array.isArray(ticketData)
            ? ticketData
            : Array.isArray(
                  ticketData.items,
                )
              ? ticketData.items
              : [];

        setRuns(runRows);
        setTickets(ticketRows);

        setSelectedRun(
          (current) => {
            if (
              current &&
              runRows.some(
                (run) =>
                  run.run_id ===
                  current.run_id,
              )
            ) {
              return current;
            }

            return runRows[0] ?? null;
          },
        );
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load agent runs.",
        );
      } finally {
        setLoading(false);
      }
    }, []);

  useEffect(() => {
    const timer =
      window.setTimeout(() => {
        void loadData();
      }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadData]);

  const ticketMap = useMemo(
    () =>
      new Map(
        tickets.map((ticket) => [
          ticket.id,
          ticket,
        ]),
      ),
    [tickets],
  );

  const statuses = useMemo(
    () =>
      Array.from(
        new Set(
          runs.map(
            (run) => run.status,
          ),
        ),
      ).sort(),
    [runs],
  );

  const actions = useMemo(
    () =>
      Array.from(
        new Set(
          runs.map(
            (run) => run.action,
          ),
        ),
      ).sort(),
    [runs],
  );

  const filteredRuns =
    useMemo(() => {
      const query = search
        .trim()
        .toLowerCase();

      return runs.filter((run) => {
        const ticket =
          ticketMap.get(
            run.ticket_id,
          );

        const matchesStatus =
          statusFilter === "all" ||
          run.status === statusFilter;

        const matchesAction =
          actionFilter === "all" ||
          run.action === actionFilter;

        const matchesSearch =
          !query ||
          [
            run.run_id,
            String(run.ticket_id),
            run.action,
            run.status,
            run.reason,
            run.recommended_team,
            run.recommended_priority,
            run.reviewer_note,
            ticket?.subject,
            ticket?.requester_email,
            ticket?.external_id,
          ]
            .filter(Boolean)
            .some((value) =>
              String(value)
                .toLowerCase()
                .includes(query),
            );

        return (
          matchesStatus &&
          matchesAction &&
          matchesSearch
        );
      });
    }, [
      runs,
      search,
      statusFilter,
      actionFilter,
      ticketMap,
    ]);

  const executedCount =
    useMemo(
      () =>
        runs.filter(
          (run) =>
            run.status === "executed",
        ).length,
      [runs],
    );

  const pendingCount =
    useMemo(
      () =>
        runs.filter((run) =>
          [
            "pending_approval",
            "review_required",
            "executing",
          ].includes(run.status),
        ).length,
      [runs],
    );

  const failedCount =
    useMemo(
      () =>
        runs.filter((run) =>
          [
            "execution_failed",
            "rejected",
          ].includes(run.status),
        ).length,
      [runs],
    );

  const selectedTicket =
    selectedRun
      ? ticketMap.get(
          selectedRun.ticket_id,
        )
      : undefined;

  const highestRisk =
    selectedRun?.tool_plan.some(
      (tool) =>
        tool.risk_level === "high",
    )
      ? "high"
      : selectedRun?.tool_plan.some(
            (tool) =>
              tool.risk_level ===
              "medium",
          )
        ? "medium"
        : "low";

  return (
    <div className="min-h-screen">
      <AppSidebar active="/runs" />

      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Agent Runs
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Persistent execution audit trail
              </p>
            </div>

            <div className="flex items-center gap-3">
              <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-4 py-2 text-[11px] text-slate-500 shadow-sm sm:flex">
                <CircleDot className="h-3.5 w-3.5 text-emerald-500" />
                Audit trail active
              </div>

              <button
                type="button"
                onClick={() =>
                  void loadData()
                }
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
              >
                <RefreshCw
                  className={`h-4 w-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
              Persistent Agent Telemetry
            </p>

            <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
              Runs / Audit Trail
            </h1>

            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500">
              Inspect every persisted CXOps
              decision, workflow path,
              authorization plan and reviewer
              outcome in one operational record.
            </p>
          </section>

          {error && (
            <div className="mb-6 flex gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <XCircle className="h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          <section className="mb-7 grid gap-4 md:grid-cols-4">
            <StatCard
              title="Total runs"
              value={runs.length}
              note="All persisted agent decisions currently returned by the API."
              tone="violet"
            />

            <StatCard
              title="Executed"
              value={executedCount}
              note="Runs that completed their external execution lifecycle."
              tone="emerald"
            />

            <StatCard
              title="In review"
              value={pendingCount}
              note="Runs awaiting approval, review or current execution."
              tone="amber"
            />

            <StatCard
              title="Failed / rejected"
              value={failedCount}
              note="Runs with rejected or failed execution outcomes."
              tone="rose"
            />
          </section>

          <section className="app-panel mb-6 rounded-[20px] p-4">
            <div className="grid gap-3 md:grid-cols-[1fr_210px_210px]">
              <div className="relative">
                <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />

                <input
                  value={search}
                  onChange={(event) =>
                    setSearch(
                      event.target.value,
                    )
                  }
                  placeholder="Search run ID, ticket, reason, team..."
                  className="w-full rounded-xl border border-slate-200 bg-[#fbfcff] py-3 pl-10 pr-4 text-sm text-slate-700 outline-none transition focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                />
              </div>

              <div className="relative">
                <Filter className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />

                <select
                  value={statusFilter}
                  onChange={(event) =>
                    setStatusFilter(
                      event.target.value,
                    )
                  }
                  className="w-full appearance-none rounded-xl border border-slate-200 bg-[#fbfcff] py-3 pl-10 pr-4 text-sm text-slate-700 outline-none transition focus:border-violet-300"
                >
                  <option value="all">
                    All statuses
                  </option>

                  {statuses.map(
                    (status) => (
                      <option
                        key={status}
                        value={status}
                      >
                        {formatText(
                          status,
                        )}
                      </option>
                    ),
                  )}
                </select>
              </div>

              <select
                value={actionFilter}
                onChange={(event) =>
                  setActionFilter(
                    event.target.value,
                  )
                }
                className="w-full rounded-xl border border-slate-200 bg-[#fbfcff] px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-violet-300"
              >
                <option value="all">
                  All actions
                </option>

                {actions.map(
                  (action) => (
                    <option
                      key={action}
                      value={action}
                    >
                      {formatText(
                        action,
                      )}
                    </option>
                  ),
                )}
              </select>
            </div>
          </section>

          <div className="grid gap-6 xl:grid-cols-[410px_minmax(0,1fr)]">
            <section className="app-panel self-start overflow-hidden rounded-[22px] xl:sticky xl:top-[96px]">
              <div className="flex items-center justify-between border-b border-slate-200/70 p-5">
                <div>
                  <h2 className="font-medium text-slate-900">
                    Run History
                  </h2>

                  <p className="mt-1 text-xs text-slate-400">
                    {
                      filteredRuns.length
                    }{" "}
                    matching records
                  </p>
                </div>

                <Badge variant="violet">
                  {
                    filteredRuns.length
                  }
                </Badge>
              </div>

              <div
                data-lenis-prevent
                className="max-h-[760px] overflow-y-auto overscroll-contain"
              >
                {loading &&
                runs.length === 0 ? (
                  <div className="flex h-48 items-center justify-center">
                    <LoaderCircle className="h-6 w-6 animate-spin text-violet-500" />
                  </div>
                ) : filteredRuns.length ===
                  0 ? (
                  <div className="p-10 text-center">
                    <Search className="mx-auto h-7 w-7 text-slate-300" />

                    <p className="mt-3 text-sm text-slate-400">
                      No runs match the
                      current filters.
                    </p>
                  </div>
                ) : (
                  filteredRuns.map(
                    (run) => {
                      const ticket =
                        ticketMap.get(
                          run.ticket_id,
                        );

                      const selected =
                        selectedRun?.run_id ===
                        run.run_id;

                      return (
                        <button
                          key={run.run_id}
                          type="button"
                          onClick={() =>
                            setSelectedRun(
                              run,
                            )
                          }
                          className={`group relative w-full border-b border-slate-200/60 p-5 text-left transition last:border-0 ${
                            selected
                              ? "bg-gradient-to-r from-violet-50/90 via-blue-50/40 to-white"
                              : "bg-white/30 hover:bg-slate-50/80"
                          }`}
                        >
                          {selected && (
                            <span className="absolute bottom-3 left-0 top-3 w-[3px] rounded-r-full bg-gradient-to-b from-violet-500 to-blue-500" />
                          )}

                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-slate-400">
                                Ticket #
                                {
                                  run.ticket_id
                                }
                              </p>

                              <p className="mt-2 truncate text-sm font-medium text-slate-850">
                                {ticket?.subject ??
                                  formatText(
                                    run.action,
                                  )}
                              </p>
                            </div>

                            <ChevronRight
                              className={`mt-1 h-4 w-4 shrink-0 transition ${
                                selected
                                  ? "translate-x-0.5 text-violet-500"
                                  : "text-slate-300 group-hover:translate-x-0.5 group-hover:text-slate-500"
                              }`}
                            />
                          </div>

                          <div className="mt-3 flex flex-wrap gap-2">
                            <Badge
                              variant={statusVariant(
                                run.status,
                              )}
                            >
                              {formatText(
                                run.status,
                              )}
                            </Badge>

                            <Badge
                              variant={actionVariant(
                                run.action,
                              )}
                            >
                              {formatText(
                                run.action,
                              )}
                            </Badge>
                          </div>

                          <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">
                            {run.reason}
                          </p>

                          <p className="mt-3 truncate font-mono text-[9px] text-slate-300">
                            {run.run_id}
                          </p>
                        </button>
                      );
                    },
                  )
                )}
              </div>
            </section>

            {!selectedRun ? (
              <section className="app-panel flex min-h-[600px] items-center justify-center rounded-[22px]">
                <div className="text-center">
                  <Workflow className="mx-auto h-8 w-8 text-slate-300" />

                  <p className="mt-4 font-medium text-slate-700">
                    Select an agent run
                  </p>
                </div>
              </section>
            ) : (
              <div className="space-y-6">
                <section className="app-panel overflow-hidden rounded-[22px]">
                  <div className="border-b border-slate-200/70 bg-gradient-to-r from-violet-50/70 via-white to-blue-50/55 p-6 md:p-7">
                    <div className="flex flex-col justify-between gap-5 md:flex-row md:items-start">
                      <div>
                        <div className="flex flex-wrap gap-2">
                          <Badge
                            variant={statusVariant(
                              selectedRun.status,
                            )}
                          >
                            {formatText(
                              selectedRun.status,
                            )}
                          </Badge>

                          <Badge
                            variant={actionVariant(
                              selectedRun.action,
                            )}
                          >
                            {formatText(
                              selectedRun.action,
                            )}
                          </Badge>

                          <Badge
                            variant={riskVariant(
                              highestRisk,
                            )}
                          >
                            {highestRisk} risk
                          </Badge>
                        </div>

                        <h2 className="mt-5 text-2xl font-medium tracking-[-0.035em] text-slate-950">
                          {selectedTicket?.subject ??
                            `Ticket #${selectedRun.ticket_id}`}
                        </h2>

                        <p className="mt-2 break-all font-mono text-[10px] text-slate-400">
                          {
                            selectedRun.run_id
                          }
                        </p>
                      </div>

                      <div className="flex items-center gap-2 text-xs text-slate-500">
                        <Ticket className="h-4 w-4 text-violet-500" />

                        Ticket #
                        {
                          selectedRun.ticket_id
                        }
                      </div>
                    </div>
                  </div>

                  <div className="p-6 md:p-7">
                    {selectedTicket && (
                      <div className="mb-5 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-400">
                        <span>
                          {
                            selectedTicket.requester_email
                          }
                        </span>

                        {selectedTicket.external_id && (
                          <span className="text-blue-500">
                            Zendesk #
                            {
                              selectedTicket.external_id
                            }
                          </span>
                        )}
                      </div>
                    )}

                    <div className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                        Decision reason
                      </p>

                      <p className="mt-3 text-sm leading-7 text-slate-600">
                        {
                          selectedRun.reason
                        }
                      </p>
                    </div>

                    <div className="mt-4 grid gap-4 md:grid-cols-2">
                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">
                          Recommended team
                        </p>

                        <p className="mt-2 font-medium text-slate-900">
                          {selectedRun.recommended_team ??
                            "—"}
                        </p>
                      </div>

                      <div className="rounded-2xl border border-slate-200/80 bg-white p-5">
                        <p className="text-xs text-slate-400">
                          Recommended priority
                        </p>

                        <p className="mt-2 font-medium text-slate-900">
                          {selectedRun.recommended_priority
                            ? formatText(
                                selectedRun.recommended_priority,
                              )
                            : "—"}
                        </p>
                      </div>
                    </div>

                    {selectedRun.response_draft && (
                      <div className="mt-4 rounded-2xl border border-blue-100 bg-blue-50/45 p-5">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-500">
                          Response draft
                        </p>

                        <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-600">
                          {
                            selectedRun.response_draft
                          }
                        </p>
                      </div>
                    )}

                    {selectedRun.reviewer_note && (
                      <div className="mt-4 rounded-2xl border border-amber-100 bg-amber-50/55 p-5">
                        <div className="flex items-center gap-2">
                          <ShieldAlert className="h-4 w-4 text-amber-500" />

                          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-amber-600">
                            Reviewer note
                          </p>
                        </div>

                        <p className="mt-3 text-sm leading-7 text-slate-600">
                          {
                            selectedRun.reviewer_note
                          }
                        </p>
                      </div>
                    )}
                  </div>
                </section>

                <section className="app-panel rounded-[22px] p-6 md:p-7">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                      <Workflow className="h-5 w-5" />
                    </div>

                    <div>
                      <h2 className="font-medium text-slate-900">
                        Workflow Path
                      </h2>

                      <p className="text-xs text-slate-400">
                        LangGraph nodes traversed
                      </p>
                    </div>
                  </div>

                  <div className="mt-6 flex flex-wrap items-center gap-2">
                    {selectedRun.workflow_path.length ===
                    0 ? (
                      <p className="text-sm text-slate-400">
                        No workflow path stored.
                      </p>
                    ) : (
                      selectedRun.workflow_path.map(
                        (
                          step,
                          index,
                        ) => (
                          <div
                            key={`${step}-${index}`}
                            className="flex items-center gap-2"
                          >
                            <span className="rounded-xl border border-violet-100 bg-gradient-to-br from-white to-violet-50/70 px-3.5 py-2 text-xs font-medium text-slate-700 shadow-sm">
                              {formatText(
                                step,
                              )}
                            </span>

                            {index <
                              selectedRun
                                .workflow_path
                                .length -
                                1 && (
                              <ChevronRight className="h-4 w-4 text-violet-300" />
                            )}
                          </div>
                        ),
                      )
                    )}
                  </div>
                </section>

                <section className="app-panel rounded-[22px] p-6 md:p-7">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 text-emerald-500">
                        <ShieldCheck className="h-5 w-5" />
                      </div>

                      <div>
                        <h2 className="font-medium text-slate-900">
                          Tool Authorization
                        </h2>

                        <p className="text-xs text-slate-400">
                          Persisted tool plan for this run
                        </p>
                      </div>
                    </div>

                    <Badge variant="violet">
                      {
                        selectedRun.tool_plan.length
                      }{" "}
                      tools
                    </Badge>
                  </div>

                  {selectedRun.tool_plan.length ===
                  0 ? (
                    <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/70 p-5 text-sm text-slate-500">
                      No external tools were
                      proposed for this run.
                    </div>
                  ) : (
                    <div className="mt-6 space-y-4">
                      {selectedRun.tool_plan.map(
                        (
                          tool,
                          index,
                        ) => (
                          <div
                            key={`${tool.tool}-${index}`}
                            className="rounded-2xl border border-slate-200/80 bg-[#fbfcff] p-5"
                          >
                            <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                              <p className="font-mono text-sm font-medium text-violet-600">
                                {
                                  tool.tool
                                }
                              </p>

                              <div className="flex flex-wrap gap-2">
                                <Badge
                                  variant={riskVariant(
                                    tool.risk_level,
                                  )}
                                >
                                  {
                                    tool.risk_level
                                  }{" "}
                                  risk
                                </Badge>

                                <Badge
                                  variant={
                                    tool.authorized
                                      ? "success"
                                      : "warning"
                                  }
                                >
                                  {tool.authorized
                                    ? "Authorized"
                                    : "Locked"}
                                </Badge>

                                {tool.requires_approval && (
                                  <Badge variant="warning">
                                    Approval required
                                  </Badge>
                                )}
                              </div>
                            </div>

                            {Object.keys(
                              tool.arguments ??
                                {},
                            ).length >
                              0 && (
                              <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
                                <div className="border-b border-slate-200 px-4 py-2.5">
                                  <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                                    Arguments
                                  </p>
                                </div>

                                <pre className="overflow-x-auto p-4 text-xs leading-6 text-slate-600">
                                  {JSON.stringify(
                                    tool.arguments,
                                    null,
                                    2,
                                  )}
                                </pre>
                              </div>
                            )}
                          </div>
                        ),
                      )}
                    </div>
                  )}
                </section>

                {selectedRun.status ===
                  "superseded" && (
                  <div className="flex gap-3 rounded-[20px] border border-slate-200 bg-slate-50/75 p-5">
                    <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />

                    <p className="text-sm leading-6 text-slate-500">
                      This run was superseded by
                      a newer analysis of the same
                      ticket and remains stored
                      for audit history.
                    </p>
                  </div>
                )}

                <section className="flex items-center justify-between rounded-[20px] border border-violet-100 bg-gradient-to-r from-violet-50/60 via-white to-blue-50/50 p-5">
                  <div className="flex items-center gap-3">
                    <Sparkles className="h-5 w-5 text-violet-500" />

                    <div>
                      <p className="text-sm font-medium text-slate-800">
                        Persistent audit record
                      </p>

                      <p className="mt-1 text-xs text-slate-400">
                        Decisions remain visible even after completion.
                      </p>
                    </div>
                  </div>

                  <Activity className="h-5 w-5 text-blue-400" />
                </section>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
