import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { PublicChatApiError } from "./client.ts";
import {
  applySummaryChangeFor,
  emptyPilotWorkbench,
  patchForOrganization,
  pilotWorkbenchForOrganization,
  visiblePilotWorkbench,
  type PilotWorkbenchState,
} from "./pilotWorkbench.ts";
import type { PublicChatPilotSummary, StaffHandoffSession } from "./types.ts";

const SUMMARY: PublicChatPilotSummary = {
  tenant_id: 42,
  window: "24h",
  generated_at: "2026-10-09T12:00:00Z",
  config: {
    widget_enabled: true,
    grounded_auto_reply_enabled: true,
    theme_token: "midnight",
    allowed_origin_count: 1,
  },
  queue: {
    human_requested: 1,
    human_assigned: 0,
    ai_active: 2,
    active_total: 3,
  },
  queue_health: {
    health: "normal",
    oldest_waiting_since: "2026-10-09T11:30:00Z",
    oldest_waiting_minutes: 12,
  },
  window_summary: {
    sessions_created: 5,
    customer_messages: 12,
    sessions_closed: 2,
    grounded_public_auto_replies: 3,
  },
  rag: {
    rag_requests: 9,
    rag_grounded: 6,
    rag_errors: 0,
    avg_rag_latency_ms: 120,
    avg_best_similarity: 0.81,
    estimated_ai_cost_usd: 0.01,
  },
  safety: {
    public_chat_integration_jobs: 0,
    autonomous_public_chat_executions: 0,
  },
};

const SESSION: StaffHandoffSession = {
  session_id: 7,
  conversation_id: 9,
  ticket_id: 11,
  customer_id: null,
  status: "human_requested",
  assigned_to_subject: null,
  assigned_at: null,
  released_at: null,
  expires_at: "2026-10-10T12:00:00Z",
  created_at: "2026-10-09T11:00:00Z",
  updated_at: "2026-10-09T11:00:00Z",
};

function populatedWorkbench(organizationId: number): PilotWorkbenchState {
  return {
    organizationId,
    summary: SUMMARY,
    summaryError: "",
    error: "a session error",
    sessions: [SESSION],
    expanded: { 7: true },
    actions: { 7: [] },
    actionsLoading: { 7: false },
    busy: 7,
    loading: false,
    summaryLoading: false,
  };
}

const B_SESSION: StaffHandoffSession = {
  ...SESSION,
  session_id: 8,
};

const SUMMARY_7D: PublicChatPilotSummary = {
  ...SUMMARY,
  window: "7d",
};

describe("emptyPilotWorkbench", () => {
  it("binds every field to the organization", () => {
    const state = emptyPilotWorkbench(12);
    assert.equal(state.organizationId, 12);
    assert.equal(state.summary, null);
    assert.equal(state.summaryError, "");
    assert.equal(state.error, "");
    assert.deepEqual(state.sessions, []);
    assert.deepEqual(state.expanded, {});
    assert.deepEqual(state.actions, {});
    assert.deepEqual(state.actionsLoading, {});
    assert.equal(state.busy, null);
    assert.equal(state.loading, false);
    assert.equal(state.summaryLoading, false);
  });
});

describe("pilotWorkbenchForOrganization", () => {
  it("leaves the workbench untouched for the same organization", () => {
    const previous = populatedWorkbench(42);
    const next = pilotWorkbenchForOrganization(previous, 42);
    assert.equal(next, previous);
    assert.equal(next.summary?.tenant_id, 42);
    assert.deepEqual(next.sessions, [SESSION]);
  });

  it("clears the previous organization's summary before the new tenant loads", () => {
    const previous = populatedWorkbench(42);
    const next = pilotWorkbenchForOrganization(previous, 99);
    assert.notEqual(next, previous);
    assert.equal(next.organizationId, 99);
    assert.equal(next.summary, null);
    assert.equal(next.summaryError, "");
    assert.equal(next.error, "");
    assert.deepEqual(next.sessions, []);
    assert.deepEqual(next.expanded, {});
    assert.deepEqual(next.actions, {});
    assert.deepEqual(next.actionsLoading, {});
    assert.equal(next.busy, null);
  });

  it("an empty workbench for the same org stays a no-op", () => {
    const previous = emptyPilotWorkbench(7);
    assert.equal(pilotWorkbenchForOrganization(previous, 7), previous);
    assert.notEqual(pilotWorkbenchForOrganization(previous, 8), previous);
  });
});

describe("patchForOrganization", () => {
  it("applies a completion while the workbench still belongs to the request tenant", () => {
    const state = emptyPilotWorkbench(42);
    const next = patchForOrganization(42, (prev) => ({
      ...prev,
      sessions: [SESSION],
    }))(state);
    assert.equal(next.organizationId, 42);
    assert.deepEqual(next.sessions, [SESSION]);
  });

  it("a stale Org A completion after moving to Org B mutates nothing", () => {
    const bState: PilotWorkbenchState = {
      ...emptyPilotWorkbench(99),
      sessions: [B_SESSION],
    };
    const next = patchForOrganization(42, (prev) => ({
      ...prev,
      sessions: [SESSION],
    }))(bState);
    assert.equal(next, bState);
    assert.deepEqual(next.sessions, [B_SESSION]);
  });

  it("a stale Org A error completion cannot set Org B's error", () => {
    const bState: PilotWorkbenchState = { ...emptyPilotWorkbench(99) };
    const next = patchForOrganization(42, (prev) => ({
      ...prev,
      error: "late Org A failure",
    }))(bState);
    assert.equal(next, bState);
    assert.equal(next.error, "");
  });

  it("a stale Org A finally cannot clear Org B's loading or busy state", () => {
    const bState: PilotWorkbenchState = {
      ...emptyPilotWorkbench(99),
      loading: true,
      busy: 8,
    };
    const next = patchForOrganization(42, (prev) => ({
      ...prev,
      loading: false,
      busy: null,
    }))(bState);
    assert.equal(next, bState);
    assert.equal(next.loading, true);
    assert.equal(next.busy, 8);
  });

  it("a live finally clears only its own tenant's loading state", () => {
    const bState: PilotWorkbenchState = {
      ...emptyPilotWorkbench(99),
      loading: true,
    };
    const next = patchForOrganization(99, (prev) => ({
      ...prev,
      loading: false,
    }))(bState);
    assert.equal(next.loading, false);
  });
});

describe("visiblePilotWorkbench", () => {
  it("renders the stored workbench for the active organization", () => {
    const state = populatedWorkbench(42);
    assert.equal(visiblePilotWorkbench(state, 42), state);
  });

  it("Org A state rendering under active Org B shows no A summary or sessions", () => {
    const state = populatedWorkbench(42);
    const visible = visiblePilotWorkbench(state, 99);
    assert.notEqual(visible, state);
    assert.equal(visible.organizationId, 99);
    assert.equal(visible.summary, null);
    assert.deepEqual(visible.sessions, []);
    assert.equal(visible.error, "");
    assert.equal(visible.summaryError, "");
    assert.deepEqual(visible.expanded, {});
    assert.equal(visible.busy, null);
    assert.equal(visible.loading, false);
    assert.equal(visible.summaryLoading, false);
  });

  it("render gating never mutates the stored workbench", () => {
    const state = populatedWorkbench(42);
    visiblePilotWorkbench(state, 99);
    assert.equal(state.organizationId, 42);
    assert.equal(state.summary?.tenant_id, 42);
    assert.deepEqual(state.sessions, [SESSION]);
    assert.equal(state.error, "a session error");
  });
});

describe("applySummaryChangeFor", () => {
  it("accepts a summary result while tenant and window still match", () => {
    const state = emptyPilotWorkbench(42);
    const next = applySummaryChangeFor(42, "24h", "24h", (prev) => ({
      ...prev,
      summary: SUMMARY,
      summaryError: "",
    }))(state);
    assert.equal(next.summary, SUMMARY);
    assert.equal(next.summaryError, "");
  });

  it("a stale 24h result cannot replace the 7d state", () => {
    const state: PilotWorkbenchState = {
      ...emptyPilotWorkbench(42),
      summary: SUMMARY_7D,
    };
    const next = applySummaryChangeFor(42, "24h", "7d", (prev) => ({
      ...prev,
      summary: SUMMARY,
      summaryError: "",
    }))(state);
    assert.equal(next, state);
    assert.equal(next.summary, SUMMARY_7D);
  });

  it("a stale window error cannot write into the 7d state", () => {
    const state: PilotWorkbenchState = { ...emptyPilotWorkbench(42) };
    const next = applySummaryChangeFor(42, "24h", "7d", (prev) => ({
      ...prev,
      summaryError: "late 24h failure",
    }))(state);
    assert.equal(next, state);
    assert.equal(next.summaryError, "");
  });

  it("a stale window finally cannot clear the 7d loading state", () => {
    const state: PilotWorkbenchState = {
      ...emptyPilotWorkbench(42),
      summaryLoading: true,
    };
    const next = applySummaryChangeFor(42, "24h", "7d", (prev) => ({
      ...prev,
      summaryLoading: false,
    }))(state);
    assert.equal(next, state);
    assert.equal(next.summaryLoading, true);
  });

  it("a stale cross-tenant summary result is rejected even for the same window", () => {
    const state = emptyPilotWorkbench(99);
    const next = applySummaryChangeFor(42, "24h", "24h", (prev) => ({
      ...prev,
      summary: SUMMARY,
    }))(state);
    assert.equal(next, state);
    assert.equal(next.summary, null);
  });

  it("an ordinary same-org refresh failure preserves the last-good summary", () => {
    const state = populatedWorkbench(42);
    const next = applySummaryChangeFor(42, "24h", "24h", (prev) => ({
      ...prev,
      summaryError: "Failed to load pilot summary",
    }))(state);
    assert.equal(next.summary, SUMMARY);
    assert.equal(next.summaryError, "Failed to load pilot summary");
  });
});

describe("PublicChatApiError", () => {
  it("exposes status and detail from a failed summary load", () => {
    const error = new PublicChatApiError(422, "Invalid window");
    assert.equal(error.status, 422);
    assert.equal(error.message, "Invalid window");
  });
});