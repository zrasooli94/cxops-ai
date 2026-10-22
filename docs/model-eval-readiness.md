# Bounded real-model evaluation readiness

The 50-turn English/Persian evaluation remains **provider-disconnected**. Run
`python -m scripts.plan_chat_model_evaluation` to render its synthetic cases,
expected outcomes, scoring rubric, and worst-case arithmetic. This command does
not read credentials or call a provider.

The plan contains ten synthetic five-turn conversations: five English and five
Persian. Every noninitial turn links the full prior conversation, with expected
behavior, evidence requirements, and forbidden claims. Its rubric is a manual
acceptance definition, not an implemented grader or a model-quality result.
Token admission must count the fully serialized system instructions, all linked
history, retrieved evidence, current message, and response framing; prompt-only
byte checks are fixture sanity checks and cannot admit a real request.

The planner also performs a deterministic mocked replay. It appends a synthetic
assistant outcome after every user turn, carries all prior user/assistant entries
forward, verifies expected citation IDs, and enforces silence after mocked
handoff. Serialized byte counts are diagnostics only; every turn remains marked
blocked for exact-token admission.

## Disabled application composition

`app/services/public_chat_model_eval.py` composes tenant-scoped lexical
retrieval, the last twelve ordered public conversation turns, the complete
structured Responses input, exact-count admission, durable maximum-cost
reservation, response/citation validation, the existing late-handoff recheck,
and normal reply persistence. `PublicChatService` calls that composition through
the actual `/public/chat/messages` lifecycle only when a server-side setting is
enabled and a provider object has deliberately been injected. Checked-in
startup injects no provider, the setting defaults false, and production config
rejects true.

The durable approval is not a widget or owner-studio request field. It is the
latest immutable-by-application tenant-owned `AIEvaluationReleaseDecision` for
the configured run. That decision snapshot must contain an exact copy of the
bounded activation manifest stored on the run, so later run-input mutation
invalidates activation. The manifest binds the exact synthetic public-chat
session, expires, and retains the 50-turn/100-attempt/US$0.10 limits. A second
session or tenant falls through to the existing responder instead of being
disrupted. This reuses existing records and adds no migration or public approval
endpoint. There is intentionally no supported HTTP/UI flow that provisions this
special activation snapshot today; controlled operator provisioning remains a
pre-evaluation blocker.

The route-level test is reproducible with:

```sh
DATABASE_URL=postgresql+asyncpg://USER@127.0.0.1:PORT/TEST_DB \
  uv run pytest -q \
  tests/test_public_chat.py::test_disabled_model_evaluation_path_requires_durable_session_bound_approval
```

It injects a disconnected response transport and a synthetic exact counter. Its
Persian answer is visibly prefixed `پاسخ ساختگی` (mock response), while the local
owner studio continues to label itself “Offline responder · no paid AI”. This
proves route composition and safety mechanics only; it is not a genuine model
quality result.

## Reproducible customer and owner demonstration

The safest complete demonstration is intentionally split into two complementary
checks. There is no browser/API switch that injects a provider: adding one would
make a server-only spending and approval boundary caller-controlled.

1. The route-level test above exercises the new composition seam through the
   real `/public/chat/messages` endpoint. It proves three ordered turns including
   an explicit topic switch, English input plus a Persian mocked answer, tenant knowledge/citation membership,
   durable approval and reservation, session restoration context, non-target
   fall-through, cross-tenant refusal, and same-tenant foreign-conversation
   rejection. The test-only controls are Python object injection, an in-memory
   disconnected transport, a synthetic full-request counter, and direct ORM
   setup of a decision-bound activation record. None is exposed by application
   startup or HTTP.
2. The existing isolated browser harness exercises the normal owner and visitor
   interfaces against the same persisted tenant lifecycle. With the disposable
   database seeded and the local backend/frontend running as described in
   `docs/local-offline-chat-demo.md`, run it from `/tmp` to keep generated
   screenshots outside the repository:

   ```sh
   cd /tmp
   node /ABSOLUTE/PATH/TO/cxops-ai/scripts/headless_owner_studio_e2e.mjs
   ```

   The browser harness narrowly fault-injects only the local evidence-fetch
   endpoint to verify stale/error recovery; chat and staff actions use the normal
   local HTTP routes. A successful run prints one JSON object whose sixteen values are `true`. It
   covers owner settings and FAQ save/reload, document upload/retrieval/removal,
   English and Persian visitor questions, source-evidence display, knowledge-gap
   correction and a fresh grounded preview, transcript restoration, unknown
   knowledge handoff, staff claim/reply/release/resolve, post-takeover visitor
   persistence with no automated reply, analytics, and 390 px RTL layout.

Measured locally on 2026-10-22: the route/provider regression group passed
150 tests with two existing warnings, and the browser harness returned all
sixteen checks true. The browser run intentionally uses the visibly labeled
offline deterministic responder; the route test intentionally uses mocked model
text. Together they prove UI/lifecycle integration and the disabled model seam,
not genuine model intelligence, semantic retrieval quality, factual entailment,
provider account access, or paid spend.

## Enforced offline contract

- One durable maximum-cost reservation is committed before each mocked dispatch.
- Duplicate attempts are rejected explicitly; concurrent attempts stop while an
  unresolved reservation exists.
- SDK retries are zero and only attempt numbers 1–2 are admitted per turn.
- Missing, malformed, negative, or over-cap provider usage is ambiguous: the
  reservation stays open and the run stops for reconciliation.
- Reported response input usage must exactly equal the payload-bound admission
  count; a mismatch remains ambiguous and keeps the reservation open.
- Confirmed usage within the 3,000-input/500-output caps settles the conservative
  maximum reservation. The reservation column has six-decimal precision, so it
  is intentionally not rewritten with a potentially rounded token-level value.
- Timeouts, malformed responses, and unknown citations remain ambiguous after
  dispatch. They are never silently retried.
- Persisted handoff state suppresses dispatch; the normal public-chat reply path
  also rechecks handoff immediately before persistence so late takeover wins.

## Finite blockers before an owner-approved real evaluation

1. Obtain an exact full-request count from the authenticated Responses input-token
   endpoint for each final payload. Installed `tiktoken` does not map
   `gpt-6-luna`, and even a text-tokenizer mapping would omit request-structure
   tokens; byte/character estimates are not accepted for admission. Pricing for
   the count request is also unverified.
2. Re-verify the exact model identifier, Responses schema, service tier, cache
   policy, and current official prices immediately before approval.
3. Verify the owner's API project can access that exact model using a securely
   configured restricted credential. No credential exists in this source path.
4. Deliberately construct the reviewed development-only HTTPS source at startup
   with a securely injected restricted credential. The application lifecycle is
   now wired to an injection seam, but checked-in startup intentionally leaves it
   uncomposed. The source pins the count/create endpoints, zero retries and
   redacted failures; all tests inject a mock client.
   Its input-count method deliberately fails before HTTP until count-request cost
   can be included in the same durable reservation and handoff envelope.
5. Obtain explicit owner approval for the provider, model, credential method,
   50-turn case set, and US$0.10 hard ceiling before any metadata or generation
   request.

Mocks verify contracts and failure handling only. They do not demonstrate model
quality, factual entailment, account access, pricing accuracy, or paid spend.

## Public schema evidence (checked 2026-10-22)

The disabled seam follows the official Responses create reference at
<https://developers.openai.com/api/reference/resources/responses/methods/create>:
`POST https://api.openai.com/v1/responses`, nonstreaming `stream: false`,
`store: false`, `max_output_tokens`, `service_tier`, structured JSON through
`text.format` with `type: json_schema`, `prompt_cache_options.mode/ttl`, and response
`usage.input_tokens` / `usage.output_tokens`. The reference says the cache TTL
currently supports `30m` and deprecates `prompt_cache_retention` in favor of
`prompt_cache_options.mode/ttl`.

The exact-count requirement follows the official token-counting guide at
<https://developers.openai.com/api/docs/guides/token-counting>, which states that
the input-token endpoint accepts the Responses request format and includes
structural tokens that local text tokenizers can omit.

The official model page at
<https://developers.openai.com/api/docs/models/gpt-6-luna> listed the exact
model ID `gpt-6-luna` and Responses `v1/responses` support on the same check
date. This is public documentation evidence only, not evidence that the owner's
API project is entitled to use the model.

This source check does **not** prove project access to `gpt-6-luna`, exact
tokenizer support, actual response compatibility, or future schema stability.
Those remain fresh-verification gates immediately before any separately approved
real evaluation.
