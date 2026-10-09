import type { SummaryWindow } from "./staff.ts";
import type {
  PublicChatPilotSummary,
  StaffBusinessAction,
  StaffHandoffSession,
} from "./types.ts";

// Handoff-queue + pilot-summary UI state that must NEVER cross a tenant
// boundary. The active organization is a hard gate on the public-chat page:
// when it changes, the previous tenant's summary, queue, expanded/action rows,
// and errors are dropped BEFORE the new tenant's data is requested — org A's
// summary must never render as "last good data" while org B is loading.
export interface PilotWorkbenchState {
  organizationId: number;
  summary: PublicChatPilotSummary | null;
  summaryError: string;
  error: string;
  sessions: StaffHandoffSession[];
  expanded: Record<number, boolean>;
  actions: Record<number, StaffBusinessAction[]>;
  actionsLoading: Record<number, boolean>;
  busy: number | null;
  loading: boolean;
  summaryLoading: boolean;
}

export function emptyPilotWorkbench(
  organizationId: number,
): PilotWorkbenchState {
  return {
    organizationId,
    summary: null,
    summaryError: "",
    error: "",
    sessions: [],
    expanded: {},
    actions: {},
    actionsLoading: {},
    busy: null,
    loading: false,
    summaryLoading: false,
  };
}

// Pure tenant-gated reset. The page keys an effect on organizationId and applies
// this to the previous workbench: the same organization is a no-op that returns
// the SAME reference (so an ordinary refresh keeps last-good data), while a
// different organization returns a fully cleared workbench bound to the new
// tenant. The cleared state commits before the new tenant's loads resolve, which
// is what makes stale cross-tenant data impossible.
export function pilotWorkbenchForOrganization(
  previous: PilotWorkbenchState,
  organizationId: number,
): PilotWorkbenchState {
  if (previous.organizationId === organizationId) return previous;
  return emptyPilotWorkbench(organizationId);
}

// Tenant-guarded async completion. An operation captures the organizationId
// when its request starts and wraps every post-await mutation with this helper:
// the mutation is applied only while the stored workbench still belongs to that
// tenant. A stale response that resolves after a switch to a different
// organization therefore becomes a no-op instead of laying previous-tenant data
// into the new tenant's workbench.
export function patchForOrganization(
  organizationId: number,
  updater: (prev: PilotWorkbenchState) => PilotWorkbenchState,
): (prev: PilotWorkbenchState) => PilotWorkbenchState {
  return (prev) =>
    prev.organizationId === organizationId ? updater(prev) : prev;
}

// Render-time tenant gate. React effects run after render, so the page must not
// rely on the reset effect alone to hide the previous tenant: this derivation
// returns the empty workbench of the active organization whenever the stored
// workbench belongs to a different one, guaranteeing Org A's summary/sessions
// cannot render under Org B even for a single frame. The empty copy is
// transient and never written back.
export function visiblePilotWorkbench(
  state: PilotWorkbenchState,
  organizationId: number,
): PilotWorkbenchState {
  return state.organizationId === organizationId
    ? state
    : emptyPilotWorkbench(organizationId);
}

// Window- AND tenant-guarded summary completion. A summary response is accepted
// only while the stored workbench still belongs to the request tenant AND the
// requested window is still the active window, so a late 24h response can never
// replace 7d (or previous-tenant) data. It guards success, error, and finally
// alike, so a stale completion cannot clear the newer request's summaryLoading
// state either.
export function applySummaryChangeFor(
  organizationId: number,
  requestWindow: SummaryWindow,
  currentWindow: SummaryWindow,
  updater: (prev: PilotWorkbenchState) => PilotWorkbenchState,
): (prev: PilotWorkbenchState) => PilotWorkbenchState {
  return (prev) => {
    if (prev.organizationId !== organizationId) return prev;
    if (currentWindow !== requestWindow) return prev;
    return updater(prev);
  };
}