"use client";

import {
  AlertTriangle,
  LoaderCircle,
  RefreshCw,
  Settings,
} from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  Suspense,
} from "react";

import EscalationsTab from "./escalations-tab.tsx";
import Setup from "./setup.tsx";
import TeamLoad from "./team-load.tsx";
import WorkQueue, { type QueueFilters } from "./work-queue.tsx";
import { CAPABILITIES } from "@/lib/authorization/capabilities";
import { useAuthorization } from "@/lib/authorization/context";
import { canViewTeamLoad } from "@/lib/operations/team-load";
import type {
  OperationsQueueItem,
  OperationsSummary,
  ServiceQueue,
  SLAPolicy,
} from "./types.ts";

const TABS = [
  { id: "work-queue", label: "Work Queue" },
  { id: "escalations", label: "Escalations" },
  { id: "team-load", label: "Team Load" },
  { id: "setup", label: "Setup" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function OperationsPageContent() {
  const { can } = useAuthorization();
  const canWrite = can(CAPABILITIES.TICKET_WRITE);
  const canManage = can(CAPABILITIES.AUTOMATION_MANAGE);
  const canSeeTeamLoad = canViewTeamLoad(can);

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const activeTab: TabId = useMemo(() => {
    const raw = searchParams.get("tab");
    if (raw === "escalations" || raw === "setup") {
      return raw;
    }
    if (raw === "team-load" && canSeeTeamLoad) {
      return raw;
    }
    return "work-queue";
  }, [searchParams, canSeeTeamLoad]);

  const setTab = useCallback(
    (tab: TabId) => {
      const params = new URLSearchParams(searchParams.toString());
      if (tab === "work-queue") {
        params.delete("tab");
      } else {
        params.set("tab", tab);
      }
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams]
  );

  const [summary, setSummary] = useState<OperationsSummary | null>(null);
  const [queue, setQueue] = useState<OperationsQueueItem[]>([]);
  const [queues, setQueues] = useState<ServiceQueue[]>([]);
  const [slaPolicies, setSlaPolicies] = useState<SLAPolicy[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [filters, setFilters] = useState<QueueFilters>({
    queue_id: "",
    status: "",
    priority: "",
    sla_state: "",
    needs_response: false,
    unassigned: false,
    mine: false,
    search: "",
  });

  const loadSummary = useCallback(async () => {
    try {
      const response = await fetch("/api/backend/service-operations/summary", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Failed to load summary");
      setSummary(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Summary error");
    }
  }, []);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.queue_id) params.set("queue_id", filters.queue_id);
      if (filters.status) params.set("status", filters.status);
      if (filters.priority) params.set("priority", filters.priority);
      if (filters.sla_state) params.set("sla_state", filters.sla_state);
      if (filters.needs_response) params.set("needs_response", "true");
      if (filters.unassigned) params.set("unassigned", "true");
      if (filters.mine) params.set("mine", "true");
      if (filters.search) params.set("search", filters.search);

      const response = await fetch(
        `/api/backend/service-operations/queue?${params.toString()}`,
        { cache: "no-store" }
      );
      if (!response.ok) throw new Error("Failed to load queue");
      setQueue(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Queue error");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadConfig = useCallback(async () => {
    try {
      const [queuesResponse, slaResponse] = await Promise.all([
        fetch("/api/backend/service-queues", { cache: "no-store" }),
        fetch("/api/backend/sla-policies", { cache: "no-store" }),
      ]);
      if (queuesResponse.ok) setQueues(await queuesResponse.json());
      if (slaResponse.ok) setSlaPolicies(await slaResponse.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Config error");
    }
  }, []);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    void loadSummary();
    void loadConfig();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [loadSummary, loadConfig]);

  useEffect(() => {
    const timer = setTimeout(() => void loadQueue(), 150);
    return () => clearTimeout(timer);
  }, [filters, loadQueue]);

  const refreshWorkQueue = useCallback(() => {
    void loadSummary();
    void loadQueue();
  }, [loadSummary, loadQueue]);

  const claimTicket = async (ticketId: number) => {
    try {
      const response = await fetch(
        `/api/backend/service-operations/tickets/${ticketId}/claim`,
        { method: "POST" }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Claim failed");
      }
      refreshWorkQueue();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Claim failed");
    }
  };

  const applySuggestion = async (ticketId: number, queueId: number) => {
    try {
      const response = await fetch(
        `/api/backend/service-operations/tickets/${ticketId}/apply-suggestion`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ queue_id: queueId }),
        }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Apply failed");
      }
      refreshWorkQueue();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Apply failed");
    }
  };

  const assignQueue = async (ticketId: number, queueId: number | null) => {
    try {
      const response = await fetch(
        `/api/backend/tickets/${ticketId}/assignment`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ service_queue_id: queueId }),
        }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Assignment failed");
      }
      refreshWorkQueue();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Assignment failed");
    }
  };

  const visibleTabs = useMemo(
    () =>
      TABS.filter(
        (tab) =>
          (tab.id !== "setup" || canManage) &&
          (tab.id !== "team-load" || canSeeTeamLoad)
      ),
    [canManage, canSeeTeamLoad]
  );

  const handleTabError = useCallback((message: string) => {
    setError(message);
  }, []);

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                Service Operations
              </p>
              <h1 className="mt-2 text-3xl font-light tracking-[-0.04em] text-slate-950">
                Operations
              </h1>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  void loadSummary();
                  if (activeTab === "work-queue") void loadQueue();
                }}
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
              {canManage && (
                <button
                  onClick={() => setTab("setup")}
                  className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm shadow-sm transition ${
                    activeTab === "setup"
                      ? "border-violet-300 bg-violet-50 text-violet-600"
                      : "border-slate-200 bg-white text-slate-600 hover:border-violet-300 hover:text-violet-600"
                  }`}
                >
                  <Settings className="h-4 w-4" />
                  Setup
                </button>
              )}
            </div>
          </div>
        </header>

        <div className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          {error && (
            <div className="mb-5 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
              <button
                onClick={() => setError("")}
                className="ml-auto text-xs underline"
              >
                Dismiss
              </button>
            </div>
          )}

          <nav className="mb-6 flex border-b border-slate-100">
            {visibleTabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setTab(tab.id)}
                className={`px-5 py-3 text-sm font-medium transition ${
                  activeTab === tab.id
                    ? "border-b-2 border-violet-500 text-violet-600"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>

          {activeTab === "work-queue" && (
            <WorkQueue
              summary={summary}
              queues={queues}
              queue={queue}
              loading={loading}
              filters={filters}
              setFilters={setFilters}
              onRefresh={refreshWorkQueue}
              canWrite={canWrite}
              onClaim={claimTicket}
              onApplySuggestion={applySuggestion}
              onAssignQueue={assignQueue}
            />
          )}

          {activeTab === "escalations" && (
            <EscalationsTab
              canWrite={canWrite}
              onError={handleTabError}
            />
          )}

          {activeTab === "team-load" && canSeeTeamLoad && (
            <TeamLoad onError={handleTabError} />
          )}

          {activeTab === "setup" && canManage && (
            <Setup
              queues={queues}
              slaPolicies={slaPolicies}
              onSaved={() => {
                void loadConfig();
                void loadSummary();
                void loadQueue();
              }}
              canManageAutomation={canManage}
            />
          )}
        </div>
      </div>
    </main>
  );
}

export default function OperationsPage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-blue-50">
          <LoaderCircle className="h-8 w-8 animate-spin text-violet-500" />
        </main>
      }
    >
      <OperationsPageContent />
    </Suspense>
  );
}
