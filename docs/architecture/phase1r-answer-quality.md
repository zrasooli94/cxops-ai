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

Preconditions: this PR merged and deployed by the owner; `/version` shows the
merged `build_sha`. Then, using RISPU's existing widget key file, one session,
one synthetic question (~one decision call, one embedding, one answer call;
counted against the tenant allowance):

```bash
.venv/bin/python scripts/smoke_public_chat.py \
    --backend-url <render-api> --frontend-url <vercel-frontend> \
    --widget-key-file <key-file> --embedding-origin https://www.rispu.com \
    --expect-build-sha <merged-commit> \
    --probe-text "Does RISPU develop mobile applications?"
```

Pass criteria: `build identity` passes, `message answered` reports
`handoff=False`, status `ai_active`; the script then requests handoff and
closes the same session (no further model calls). Bodies and keys are never
printed. Abort on any `FAIL`.
