# Phase 1R - RISPU answer quality, latency and release diagnostics

Baseline: production release `c9e55b7` (Alembic `1q0a0001`). No schema change.
Nothing here touches production; every measurement is on a disposable
PostgreSQL/pgvector database with fake model providers.

## 1. How a RISPU question becomes an automatic answer

A widget message is auto-answered only if **all** of these hold (any miss hands
off to staff - nothing is relaxed in this phase):

| # | Gate | Where |
|---|------|-------|
| 1 | Tenant opted in (`grounded_auto_reply_enabled`) | `PublicChatService._grounded_skip_reason` |
| 2 | Deterministic coordinator intent is `information` | `AgentWorkflowService._classify_intent` |
| 3 | Agent decision is a bare `respond` (no business tool) | same |
| 4 | Tenant-scoped retrieval returns sources above the similarity floor | `KnowledgeSearchService` / `filter_and_format_sources` |
| 5 | Answer is grounded, cites only supplied sources, is not a fallback string | `public_answer_eligible` |
| 6 | AI allowance reserved before any model call | `TenantAIAllowanceService.reserve` |

`[S<n>]` markers are validated, then stripped before the customer sees the text.
Tool authorization, human approval and tenant filtering are unchanged.

## 2. Findings

1. **Coordinator question detection used substring matching and a short list.**
   `show` matched `how`, `whatsapp` matched `what`; natural phrasing ("Do you
   offer SEO?", "Can you build mobile apps?", "Who is RISPU?", "Tell me about
   your services", "Is there a phone number?") fell through to `mixed` and
   *always* handed off. The four owner example questions already classified as
   `information`.
2. **Follow-up messages were classified and retrieved on the session's first
   message.** Only the first message is mirrored to the ticket, and both the
   coordinator and the knowledge specialist read the ticket. A visitor who
   opened with "Hi" could never receive an automatic answer later; a visitor
   whose first message was a question had every follow-up judged by that
   question. Proven by `test_question_after_greeting_is_answered` (fails
   without the fix).
3. **No deterministic guard for instruction-override text.** Safe only because
   such text usually contained no question marker.
4. **A grounded turn embedded the same text twice** (agent retrieval, then RAG).
5. **Diagnostics gaps:** `/version` had no build identity; no counter said *why*
   a turn was or was not auto-answered; per-stage timings were logs only.

## 3. Changes

* `agent_workflow_service`: word-boundary question phrases; capability phrasing
  (`do you`, `can you`, `could you`, `is there`, `are there`, `who`, `where`,
  `tell me about`) counted only when the message is not already action-phrased;
  a *commitment veto* (`can you start my project now?` stays `mixed`); an
  *instruction-override veto* (`ignore previous instructions...`, `system
  prompt`, `act as`, `developer mode`, ... stay `mixed`). Action markers are
  untouched. `AGENT_WORKFLOW_VERSION` 2 -> 3 so no stale analysis is reused.
* Public chat passes the visitor's **current** message to the workflow
  (`latest_customer_text`) for classification and retrieval. Other callers pass
  nothing and are unchanged; fingerprints are unchanged when it is absent.
* `EmbeddingService`: process-local TTL/LRU memo of query embeddings keyed by a
  digest (`EMBEDDING_QUERY_CACHE_SIZE`, `..._TTL_SECONDS`; `0` disables).
* `/version`: adds `build_sha` (hex commit id from platform build metadata only,
  else `null`), `started_at`, `uptime_seconds`. `/`, `/health`, `/version` are
  `async def` so a probe never queues behind the shared thread pool.
* Start-up: schema gate retries with short bounded attempts
  (`SCHEMA_GATE_ATTEMPT_TIMEOUT_SECONDS`=10, `..._MAX_ATTEMPTS`=4); a database
  that stays unreachable or behind still refuses to start. `startup_complete`
  and `schema_gate_database_read` log durations.
* Metrics (closed label sets, no customer text):
  `cxops_public_chat_answer_decisions_total{outcome}`,
  `cxops_public_chat_turn_stage_seconds{stage}`,
  `cxops_embedding_query_cache_total{result}`. `public_chat_reply_sent` now
  also logs `grounded`, `coordinator_intent`, `run_action`, `skip_reason`,
  `answer_sources`.

## 4. Certification (offline)

`tests/test_rispu_answer_certification.py` loads the 12 approved documents from
`config/tenants/rispu.yaml` into a disposable pgvector database next to a decoy
tenant, then drives real HTTP routes. Models are fakes
(`scripts/rispu_certification.py`): a lexical embedder, an extractive
"faithful" answer model, and a policy-aware decision model.

Result: **74 tests pass.** 9/9 genuine questions answered with a cited,
extractively-grounded reply and no marker leakage; 13/13 out-of-scope,
transactional, injection and cross-tenant probes handed off (the injection and
action cases never reached answer generation); ungrounded/foreign-cited/empty/
refusal/erroring model output and non-`respond` decisions all fail closed;
flag-off tenants never auto-answer; the decoy tenant's canaries never appear.

**What this does not prove:** the quality of the *real* model's wording or of
OpenAI embeddings. The test similarity floor (0.12) is calibrated to the lexical
embedder; the production floor (0.35) is validated only by the optional live
`evaluate_rag` job and the single owner-approved live test.

Known conservative limits (handed off by design): "Do I need ..." (pre-existing
`i need` action marker); questions containing `please`, `update`, `cancel`,
`change`, `fix`, `stop`, `reset`, `activate`; any message with an
instruction-override phrase.

## 5. Latency

`scripts/measure_rispu_turn_latency.py` (fixed fake provider latencies: embed
250 ms, decision 1500 ms, answer 1200 ms; one process, disposable DB):

| Case | Grounded turn wall | App-side | Embed calls | DB stmts |
|------|-------------------|----------|-------------|----------|
| before, DB RTT 0 | 3.43 s | 0.23 s | 2 | 56 |
| after,  DB RTT 0 | 3.17 s | 0.22 s | 1 | 56 |
| before, DB RTT 20 ms (modelled) | 5.12 s | 0.80 s | 2 | 56 |
| after,  DB RTT 20 ms (modelled) | 4.87 s | 0.80 s | 1 | 56 |
| handoff turn, RTT 0 / 20 ms | 1.9 / 3.5 s | 0.18 / 0.74 s | 1 | 54 |

Evidence-backed conclusions:

* Application CPU/queue time is ~0.2 s per turn and **there is no
  warm-up penalty inside the application** (first turn == later turns), so
  application code is not the source of a 10 s first message.
* A grounded turn is four sequential provider calls (embed, decide, embed,
  answer). One embedding is removed (-250 ms modelled, and one billed call).
* ~55 statements per turn is the main application-controlled factor: every
  statement is a network hop to Neon (+0.6 s per 20 ms of RTT). Whether Render
  and Neon share a region is therefore the first thing to confirm.
* `/version` is a trivial handler. uvicorn runs `lifespan` - including the
  schema check, a database connect - **before binding the port**, and asyncpg's
  default connect timeout is 60 s. A cold/scaling-to-zero database therefore
  keeps the whole service unreachable until it answers, which matches the
  observed 60 s `/version` timeout. Import time is 1.3 s here (several times
  that on a 0.5 vCPU instance). These are consistent with, but not proven to be,
  the production cause: production telemetry was out of scope.
* Not changed (would need owner decisions/infra): keeping Neon compute warm,
  Render instance size/spin-down, region placement.

Next cold start, `/version` `uptime_seconds` and the `startup_complete` /
`schema_gate_database_read` logs separate "process was cold" from "handler or
model was slow".

## 6. Release verification

After deploy: `GET /version` -> `build_sha` equals the merged commit. The
runbook (`docs/runbooks/rispu-live-pilot.md`) lists the new metrics.

## 7. The one proposed live test (owner approval required)

Preconditions: this PR merged and deployed by the owner. Using RISPU's existing
widget key file, one session, one synthetic question (about one decision call,
one embedding and one answer call, counted against the tenant allowance):

```bash
.venv/bin/python scripts/smoke_public_chat.py \
    --backend-url <render-api> --frontend-url <vercel-frontend> \
    --widget-key-file <key-file> --embedding-origin https://www.rispu.com \
    --expect-build-sha <merged-commit-40-hex> \
    --expect-grounded-reply \
    --probe-text "Does RISPU develop mobile applications?"
```

Script guarantees:

* `--expect-build-sha`: if `/version` reports a different, missing or invalid
  `build_sha`, the script reports `FAIL` and exits **before** the liveness,
  readiness, embed, session-creation or message requests. No session exists
  and no model is called. (A malformed expected value exits 2 before any
  request.)
* `--expect-grounded-reply` (sync delivery only) requires a non-empty reply
  with `handoff` explicitly `false`, `processing_state="completed"`,
  `status="ai_active"`, and a reply that is not one of the known static
  handoff / acknowledgement / local-demo / knowledge-base-fallback texts.
  Anything else is `FAIL`.
* These are **HTTP-level signals only**. They do not prove the answer was
  grounded in retrieved knowledge; the script says so in its output. Grounding
  is established by the audit below.
* Once a session is created it is always closed (including after an
  unexpected error), and closure is verified (`409` on a further message).
  Without the new flags the original behaviour is unchanged. Bodies, widget key
  and session token are never printed.

## 8. Read-only audit after the live test

Run against production by the owner, `SELECT` only, tenant-scoped. None of
these queries selects a message body, question, answer or token; `sources`
is selected only as ids. Let `:org` be the RISPU `organizations.id` and
`:t0` the time just before the smoke run.

```sql
-- 1. Exactly one session, closed (created and closed)
SELECT id, status, created_at, closed_at, ticket_id
FROM public_chat_sessions
WHERE organization_id = :org AND created_at >= :t0;       -- expect 1 row, status 'closed'

-- 2. The turn's agent run took the 'respond' action, with no authorization
SELECT r.id, r.run_id, r.action, r.status, r.authorization_source
FROM agent_runs r
JOIN public_chat_sessions s
  ON s.ticket_id = r.ticket_id AND s.organization_id = r.organization_id
WHERE s.organization_id = :org AND s.created_at >= :t0;    -- action = 'respond'

-- 3. The grounded auto-reply event with valid source ids and eligibility
SELECT e.event_type,
       e.event_data -> 'source_ids'        AS source_ids,
       e.event_data ->> 'eligibility_reason' AS eligibility_reason,
       e.event_data ->> 'rag_request_id'    AS rag_request_id
FROM agent_action_events e
JOIN agent_runs r ON r.id = e.agent_run_id
JOIN public_chat_sessions s
  ON s.ticket_id = r.ticket_id AND s.organization_id = r.organization_id
WHERE s.organization_id = :org AND s.created_at >= :t0
  AND e.event_type = 'public_grounded_auto_reply';
-- expect 1 row; source_ids non-empty, each matching ^S[0-9]+$;
-- eligibility_reason = 'eligible'

-- 4. The matching successful, grounded RAG request (ids only)
SELECT l.request_id, l.feature, l.status, l.grounded, l.llm_called,
       jsonb_array_length(l.sources) AS source_count, l.latency_ms
FROM ai_request_logs l
WHERE l.organization_id = :org AND l.feature = 'public_chat'
  AND l.request_id = :rag_request_id_from_query_3;
-- expect status = 'success', grounded = true, llm_called = true, source_count >= 1

-- 5. The AI reservation settled (reservation key carries the smoke message id)
SELECT status, settled_at
FROM ai_usage_reservations
WHERE organization_id = :org AND reservation_key LIKE 'ai-turn:' || :org || ':%:smoke-%'
  AND created_at >= :t0;                                   -- expect 1 row, 'settled'

-- 6. No asynchronous jobs were created for the test
SELECT job_type, count(*)
FROM integration_jobs
WHERE organization_id = :org AND created_at >= :t0
GROUP BY job_type;                                         -- expect no public_chat.turn
                                                           -- and no agent.execute rows
```

Interpretation: checks 2-4 together are what prove grounding (a `respond`
decision, an eligible event naming valid sources, and a successful grounded
request with the same id). A reply that passes the script but fails 3 or 4
must be treated as a failed test. Record only ids, counts and statuses in the
report - never message text, tokens or keys.
