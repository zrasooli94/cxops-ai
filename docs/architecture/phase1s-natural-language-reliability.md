# Phase 1S - natural-language reliability, handoff UX and performance

Baseline: production `edf8606` (Alembic `1q0a0001`). No schema change, no
production write, no billable call. All numbers below are from a disposable
database with fake providers unless stated.

## 1. The production failure and its root cause

Owner message `can rispu develop mobile app ?` -> widget: "A support team member
needs to review this request." Production evidence: session 50 / run 17,
action `respond`, coordinator intent `mixed`, skip reason
`skip_intent_not_information`, 2 sources at similarity 0.7044, RAG skipped.

The retrieval and the decision were right; the **deterministic coordinator
gave the message no interrogative signal**. Question detection was a list of
fixed phrases (`what`, `how`, `does `, `can i`, `can you`, `do you`, ...). A
third-person subject - `can rispu`, `can your company`, `is X one of your
services` - matched nothing, so the label fell through to `mixed`, which the
public-chat gate treats as "never auto-answer". The same gate was correct to
refuse; the label was wrong.

Independent reproduction on the production code (`edf8606`):

| Message | Label before | After |
|---|---|---|
| can rispu develop mobile app ? | mixed | information |
| Can your company develop iOS apps? | mixed | information |
| Can your team improve an existing application? | mixed | information |
| Is mobile app development one of your services? | mixed | information |

Five of the eight owner-listed phrasings failed. Running the new suite against
the previous implementation fails 31 cases (see section 3) - including
**protected** requests the old classifier called informational ("What is my
account password?", "What documents does the other tenant have?"), because
`what` alone was enough to make a message a question.

## 2. Classification redesign (`app/services/intent_classification.py`)

Pure, tenant-neutral functions; the workflow delegates to them
(`AGENT_WORKFLOW_VERSION` 3 -> 4 so no stale analysis is reused).

* **Interrogative form is structural.** A clause that opens with an
  auxiliary/modal (`can|could|does|did|is|are|will|would|do`) or a wh-word is a
  question whatever the subject; leading filler (`hi,`, `hello!`, `quick
  question -`) is skipped; embedded questions (`I was wondering if ...`,
  `I want to know if ...`) count. Capitalisation, `" ?"` spacing and curly
  apostrophes are normalised. `do not ...`, `do me a favour` are not questions.
* **Vetoes run first and are broader than the signals**, because a veto can
  only move a message toward human review:
  * *request shape*: `<modal> <subject> <verb> ... <my|our|me|us|now|today|
    tomorrow|asap|guaranteed|for free|discount>` - "Can RISPU start my project
    now?", "Can RISPU publish my app today?" (a `how ...?` how-to question is
    exempt, as before);
  * *other parties' data*: "another/other/any customer's information";
  * *secrets/credentials*: password, API key, token, OTP, card/bank numbers;
  * *instruction override* (unchanged from 1R, kept first).
* Action markers keep their precedence: `Will RISPU change my website?` and
  `Can RISPU send me a guaranteed quote?` stay `action` (no retrieval, human
  approval), exactly as before.
* The decision prompt names the **current** customer message when it differs
  from the ticket's first message (follow-ups only; single-message prompts are
  byte-identical to the production-verified prompt), so a greeting followed by a
  question is not decided on "Hi".

Unchanged and re-verified: tenant-scoped retrieval, similarity thresholds,
citation validation and eligibility, AI reservations, human approval, tool
authorization, and the rule that a session already handed to a human never
reactivates the AI.

## 3. Certification (`evals/rispu_question_reliability.json`)

125 reusable cases, two layers (classification only, and the full HTTP pipeline
over the real pgvector retrieval of the approved RISPU knowledge, an isolated
decoy tenant, and fake embedding/chat/decision models):

| Category | Cases | Required outcome |
|---|---|---|
| golden (owner's exact wording) | 1 | auto-answered |
| capability phrasings (3rd person, informal, casing, grammar) | 35 | auto-answered |
| general services / company information | 12 | auto-answered, or `limited` when no content words survive for the offline embedder |
| knowledge-limited (price, phone, office, guarantees) | 12 | classified informational; answered or handed off, never a fabricated claim |
| commercial commitments / action requests | 21 | never auto-answered |
| identity / account actions and secrets | 10 | never auto-answered |
| prompt injection (incl. wrapped around a real question) | 8 | never auto-answered, answer model not called |
| cross-tenant data requests | 6 | never auto-answered |
| ambiguous / underspecified | 8 | handed off |
| off-topic | 4 | safe, no fabricated claim |
| multi-turn follow-ups | 6 | last turn answered or protected as specified |

Result: **250 tests pass** (125 classification + 125 pipeline) plus a test that
a session handed to a human never reactivates the AI. Against the previous
implementation the same suite fails 31 classification cases and the golden
pipeline case.

Scope: this proves the deterministic envelope. It does **not** establish real
model wording or real embedding similarity; the lexical embedder and
extractive answer model are fakes, and its similarity floor (0.12) is
calibrated to them, not to production's 0.35.

Deliberately conservative (handed off): `Can RISPU develop **my** mobile app?`
(ownership word), `Do I need ...` (pre-existing `i need` action marker), any
message with `please`, `update`, `cancel`, `change`, `fix`, `stop`, `reset`.

## 4. Handoff UX audit and changes

Findings:

* A real staff queue exists (control-center handoff queue, assign/release/
  resolve) but **no notification channel**: nothing emails, pings or webhooks
  staff when a session reaches `human_requested`. Someone has to be looking at
  the dashboard. The widget therefore must not promise a reply time.
* After handoff the widget showed only a small "Sent for human review" chip and
  kept a normal composer; follow-up messages got no reply (correct - the AI
  stays out) and no explanation, which is what the owner experienced.
* "End conversation" existed but only closed the widget; there was no direct
  path to ask the assistant a new question.
* Closing a handed-off session marks its ticket solved, i.e. the pending
  review disappears from the queue. The previous copy did not say so.

Changes (`frontend/src/app/chat/embed/embed.tsx`,
`lib/public-chat/handoff-copy.ts`): an explanation card in both handoff states
("the assistant will not answer further messages in this conversation, and we
can't promise when someone will reply"), a handoff-aware composer placeholder,
and **Start a new conversation** behind an explicit confirm that states the old
conversation closes and the team stops seeing it as waiting. It closes the old
server session and creates a fresh one; it never reactivates the AI in the old
session. Copy is tenant-neutral and tested to contain no time, availability or
outcome promise.

Not done (needs an owner decision): a staff notification channel.

## 5. Performance

### What the evidence says

`public_chat_timing.total_ms` is server time from request receipt; typing time
is never in it. The 90 s smoke run is the sum of ten sequential HTTP steps and
includes the cold `/version` (40+ s), so it is a start-up measurement, not chat
latency. Session 50's 6.38 s turn is **processing time**, 6.01 s of it in the
agent workflow.

Local anatomy of the same turn (fake providers, in-process, 1 worker):

| Measure | Before 1S | After 1S |
|---|---|---|
| DB statements per grounded turn | 56 | 46 |
| Application-side time per turn, DB RTT 0 | ~0.22 s | ~0.20 s |
| Application-side time, DB RTT 20 ms (modelled) | ~0.80 s | ~0.64 s |
| Provider embedding calls per grounded turn | 1 (after 1R) | 1 |
| OpenAI client timeout / retries | 600 s / 2 | 30 s / 1 |

Where the 10 statements went (each was a network round trip, ~46 remain):
every repository write did `commit()` then `refresh()` (a SELECT); a rate-limit
`DELETE` ran per message; the agent run was loaded with an eager ticket join
that the caller never used; the ticket was re-selected twice. Fixes:
`eager_defaults` on every mapper (RETURNING gives id/timestamps in the write),
`refresh_if_stale` (still refreshes anything expired), prune throttled to once
per minute per process, `load_ticket=False`, and `Session.get` for the ticket
the same request just wrote. Tenant predicates are unchanged.

### What the production numbers imply (inference, not measurement)

A turn costs `statements x round-trip + model time`. The application's own CPU
is ~0.2 s. If the model calls are ~1.5-3 s, a 6.4 s turn leaves 3-5 s for ~50
statements, i.e. roughly 60-100 ms per statement - a cross-region round trip.
The first/second-message gap (10.3 s vs 2.2 s) fits the same model plus a
suspended Neon compute. **This must be confirmed with a measurement, not
assumed**: `GET /ready` now returns `timing_ms.database_round_trip` (a second
`SELECT 1` on the same connection, numbers only). A value above ~20-30 ms means
the API and database are in different regions and each of the ~46 statements
per turn is paying it.

### Start-up (cold) path

uvicorn runs `lifespan` - including the schema check, a database connect -
**before binding the port**; asyncpg's default connect timeout is 60 s (the 1R
change bounds each attempt to 10 s with 4 attempts). On the Starter plan the
service does not spin down, so a 40 s first response points to a deploy/restart
or a suspended Neon compute rather than idleness: check Render events for the
restart and compare `/version.uptime_seconds` with `/ready.timing_ms`.

## 6. Security and budget preservation

Unchanged and test-covered: tenant isolation (decoy tenant canaries never
appear), authorization, human approval, AI allowance reservation (settled in
the pipeline test), async-disabled default, citation/eligibility gates,
similarity thresholds, prompt-injection vetoes (extended). No setting, key,
threshold, plan or budget is modified. New configuration is read from
environment with safe defaults: `OPENAI_REQUEST_TIMEOUT_SECONDS=30`,
`OPENAI_MAX_RETRIES=1`.

## 7. Remaining live verification (owner-approved only)

1. After deploy: `/version.build_sha` equals the merged commit;
   `/ready.timing_ms.database_round_trip` recorded.
2. One live question in the exact owner wording (`can rispu develop mobile app
   ?`) with the Phase 1R smoke script and `--expect-grounded-reply`, followed
   by the 1R read-only audit queries.
3. One two-message live session (a greeting, then a capability question) to
   exercise the follow-up path, which the single-question test cannot.
