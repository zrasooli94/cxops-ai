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

It injects disconnected response and exact-count transports plus a synthetic,
independently-verified count billing policy. Its
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
   disconnected transports, a synthetic decision-bound count policy, and direct ORM
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

- A policy-declared maximum count charge is durably reserved before each mocked
  exact-count dispatch. Only a positive exact count at or below 3,000 can proceed.
- The conservative generation maximum is then reserved separately before the
  mocked generation dispatch. Both charges contribute to the same US$0.10 cap.
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

1. Obtain authoritative billing terms for the authenticated Responses input-token
   endpoint, then bind a verified, expiring endpoint/model/region policy and its
   maximum per-attempt charge into the approval snapshot. Installed `tiktoken` does not map
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
   Its checked-in input-count method deliberately fails before HTTP. The generic
   controller now validates and reserves a decision-bound count policy before an
   injected count invocation, but a live count transport remains intentionally
   uncomposed until authoritative billing evidence and separate authorization exist.
5. Obtain explicit owner approval for the provider, model, credential method,
   50-turn case set, and US$0.10 hard ceiling before any metadata or generation
   request.

## Operator dry run and go/no-go

No activation record or credential should be created while the count-endpoint
billing evidence is unresolved. An operator can render the exact proposed
binding without network access, database writes, or reading a credential:

```sh
python -m scripts.plan_public_chat_model_activation \
  --organization-id 17 \
  --session-id 29 \
  --run-id controlled-eval-01 \
  --expires-at <FUTURE_ISO_8601_UTC_WITHIN_2_HOURS>
```

Use real local identifiers and an expiry between five minutes and two hours in
the future. The output is always `ready_to_activate: false`; it shows the exact
tenant/session/run/model/limits/expiry manifest and the identical snapshot that
a future immutable-by-application release decision must contain. There is no
`--apply` option and no public route for provisioning it.

If authoritative evidence later establishes a billable maximum count-request
cost, supply its per-attempt reservation with `--count-billing-status billable`,
`--count-cost-reservation-per-attempt-usd`, and the supporting official URL via
`--count-billing-evidence-url`. The planner reserves it across all 100 maximum
attempts and requires positive headroom below US$0.10. Use `no_charge` only with
an official OpenAI developer URL that explicitly establishes that status;
absence of published pricing is `unresolved`, never free.

The URL and billing status remain operator-supplied, unverified inputs: this
offline tool cannot certify their contents. It therefore always retains an
independent-authoritative-verification blocker. The complete count policy,
evidence identity, per-attempt reservation and combined reservation are included
in the same proposed decision snapshot as the generation limits.

As of 2026-10-22, the official token-counting guide and API reference document
the authenticated `POST /v1/responses/input_tokens` operation but do not state
whether it is billed or identify a charge unit/rate. The current decision is
therefore **NO-GO**. The smallest unresolved evidence step is written OpenAI
billing/support confirmation that names the endpoint, model, region, charge
unit/rate, and failure/retry treatment. No authenticated count request has been
authorized or performed; only the narrow billing inquiry was authorized.

OpenAI's Help Center assistant could not certify the endpoint's price, failure
charges, model-budget treatment, or endpoint-specific usage reporting and
escalated the question to a support specialist on 2026-10-22. That automated
response is not billing evidence. The specialist response is pending, so count
billing remains unknown and all authenticated provider requests remain blocked.

Restricted credential procedure: create a dedicated provider-project key with
only the minimum Responses permissions and a project-side spend limit, store it
in the approved secret manager, and inject it as `OPENAI_API_KEY` only into the
local development process. Never put the value in chat, shell history, CLI
arguments, source, `.env` committed files, screenshots, or logs. The planner
does not inspect whether the variable exists and never reads its value.

Go only when every item is true:

- authoritative count-endpoint billing and fresh generation pricing are known,
  and count plus generation reservations retain positive budget headroom;
- the exact `gpt-6-luna` model, Responses schema, account access and restricted
  credential have been verified without exposing the key;
- the existing 50-turn EN/FA specification, strict citation membership,
  3,000 full-request input cap, 500 output cap, zero SDK retries, one explicit
  retry, durable ambiguous-outcome reconciliation and US$0.10 ceiling remain
  unchanged;
- the decision snapshot exactly matches the expiring tenant/session/run
  activation manifest, and the final persisted handoff recheck remains enabled;
- the owner gives one explicit approval to execute the controlled evaluation.

Otherwise the decision is **NO-GO**. Run `python -m
scripts.plan_chat_model_evaluation` for the exact 50-turn fixture/replay output;
it remains offline and produces no model-quality score.

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
