# Phase 1P.2 — Real Business Tools & Human Handoff

Revision 1 (final — Phase 1P.2 implemented and validated end to end,
uncommitted). Supersedes nothing: Phase 1P.1 (see
`phase1p-public-customer-chat.md`) remains the widget/session foundation this
builds on.

Backend: business-tool framework + provider registry, A1 Cash for Cars local-demo
adapter, durable `BusinessAction` execution with idempotency, DB-backed fixed-window
rate limiter, staff handoff API, and `1p2a0001` migration (single head). Frontend:
staff handoff client + tests, control-center Handoff Queue page, sidebar entry, and
widget `human_assigned` handling.

## 1. Purpose

Two gaps remained after 1P.1: the agent could not take a **real business action**
on the customer's behalf, and the human handoff it offered had no **staff-side
queue** to land in. 1P.2 closes both while preserving every invariant 1P.1
established.

Explicitly still out of scope: RISPU tools, payments/refunds, CRM writes,
WhatsApp/Messenger/Instagram/voice, attachments, customer login, multilingual.

## 2. Non-negotiable invariants (unchanged from 1P.1)

1. **No customer text ever reaches a side effect directly.** The only path is
   `customer message → agent decision → tool plan → ToolAuthorizationService →
   approval → durable execution`. `AgentExecutionService` refuses any run that is
   not `approved`.
2. **Tool arguments never carry tenant, organization, or conversation identity.**
   Those are derived from trusted persisted parents inside the executor. A model
   cannot address another tenant by writing an id into a payload.
3. **Every business tool is `requires_approval=True`, `auto_authorize=False`.**
   A tool that could move money, a vehicle, or a schedule is never auto-approved,
   regardless of how confident the decision is.
4. **Fail closed.** Unknown provider, unknown tool, disabled tool, or a tool whose
   enablement changed between planning and execution all refuse to run. Enablement
   is re-checked at execution time, not only at plan time.
5. **No ad-hoc branching in `public_chat_service`.** Reply selection stays
   handoff-only; tool routing lives in the agent workflow.

## 3. Authorization pipeline

`plan_requires_zendesk(tool_plan)` gates the Zendesk pre-check. A **business-only**
plan (A1 tools, no Zendesk action) skips the Zendesk check entirely rather than
failing it; plans containing a Zendesk action still require a resolvable ticket.

## 4. Business actions are durable and idempotent

Each approved tool execution materialises a `BusinessAction` row, unique on
`(organization_id, ticket_id, request_type, dedupe_key)`.

- Dedupe key: `agent_run:<run_id>:<tool_name>`.
- A retried or replayed run therefore **mirrors onto the same row** rather than
  creating a second business record. `tests/test_phase1p2_business_tools.py::
  test_business_action_executes_and_mirrors_idempotently` pins this.
- Customer-visible replies are mirrored through `ensure_agent_reply_message`,
  dedupe `agent_run:<run_id>:<dedupe_suffix>`, with `provider` defaulting to
  `ZENDESK_PROVIDER` and `LOCAL_PROVIDER` for cxops-side mirrors.

The A1 executor dispatches handlers by `_<tool_name with dots replaced by
underscores>`. Tool names are namespaced (`a1.create_vehicle_lead`) but Python
attributes cannot contain dots, so `A1CashForCarsExecutor.execute` maps
`a1.create_vehicle_lead → _a1_create_vehicle_lead`.

## 5. A1 Cash for Cars adapter (local demo)

The adapter is deliberately **local-demo**: it performs no outbound calls.

- Results are tagged `"provider": "local_demo"`.
- References are deterministic `A1-<sha256[:8]>` so replays are stable.
- Pickup availability respects weekdays.
- The adapter **never computes pricing**. Quote status is a bounded, opaque
  value.

Six tools are exposed: `a1.create_vehicle_lead`, `a1.update_vehicle_details`,
`a1.request_vehicle_photos`, `a1.get_quote_status`, `a1.check_pickup_availability`,
`a1.create_pickup_request`.

## 6. Rate limiting

The 1P.1 in-memory limiter is **replaced** by `public_chat_rate_limiter_db` for
the inbox path; the in-memory limiter is retained only for back-compat.

- Fixed, aligned windows.
- A single atomic `UPDATE … WHERE count < limit RETURNING` plus
  `pg_insert(...).on_conflict_do_nothing` on the bucket.
- **Rejected requests are not charged** — a caller who exceeds the limit does not
  extend their own penalty window. Pinned by
  `test_rate_limiter_db_charges_only_accepted`.
- Bucket key: `{scope}:{key}`.

## 7. Staff handoff API

Auth-free widget routes are unchanged; these routes require a control-center
principal, derive the tenant from **memberships**, and are capability-gated. The
active-organization header is only a selector — it is never trusted as the tenant.

| Endpoint | Capability | Method |
| --- | --- | --- |
| `/staff/public-chat/handoff` | `TICKET_READ` | GET |
| `/staff/public-chat/sessions/{id}/business-actions` | `TICKET_READ` | GET |
| `/staff/public-chat/sessions/{id}/assign` | `TICKET_WRITE` | POST |
| `/staff/public-chat/sessions/{id}/release` | `TICKET_WRITE` | POST |

Transitions are strict: `human_requested → human_assigned` (assign) and
`human_assigned → human_requested` (release). An unresolvable session returns
**404**; a valid session in the wrong state returns **409**. Assigning to another
tenant is a 404, never a 403 that would leak existence.

## 8. Frontend

- `src/lib/public-chat/staff.ts` — staff client over the authenticated
  `/api/backend/staff/public-chat/...` proxy (which forwards the nhost session and
  active-organization selection), with `staff.test.ts` covering URL shape, method,
  and error surfacing.
- `src/app/(control-center)/public-chat/page.tsx` — the Handoff Queue. `TICKET_READ`
  to view; `TICKET_WRITE` to assign/release. Per-session business actions expand
  inline. Read-only subjects see an explicit "Read only" affordance rather than
  hidden controls.
- The embed widget now treats `human_assigned` as its own state and keeps polling
  through it, so a visitor sees "a support team member has joined" and, on release
  back to the queue, the original handoff notice returns. The composer stays
  disabled outside `ai_active`.

## 9. Migrations

Single head `1p2a0001`. Two pre-existing tests pinned the previous head
(`1p1a0001`) and were updated accordingly
(`tests/test_service_transformation_experiment.py`,
`tests/test_service_transformation_simulation.py`).

## 10. Validation

- `pytest tests -m "not integration"` — green.
- `mypy app` — success, 194 source files.
- `ruff check --isolated app tests alembic` — clean.
- `alembic heads` / `current` / `check` — single head `1p2a0001`, no pending ops.
- Frontend: `npm run lint`, `npx next typegen`, `npx tsc --noEmit` clean;
  `npm test` 621 passed, `npm run test:session` 100 passed, `npm run test:auth`
  583 passed; `npm run build` succeeds and registers `/public-chat`.
