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

## Frozen offline plan identity

The source-only evaluation plan was frozen on 2026-10-25 at repository
checkpoint `e4665c07b222c8cf2a285d87d23e49638b64d403`:

- specification source `scripts/plan_chat_model_evaluation.py` SHA-256:
  `80fec287a8e0c20e0476e34cf1399e4162abf3a7ab96751e1699fa499e42d3b6`;
- rendered JSON SHA-256:
  `99a79f752b22ce1b1d9053c34a1ff30c1bbe55dec4fa9e05c7f888a199da0f4c`;
- ten independent conversations, five turns each, with 25 English and 25
  Persian user turns;
- 3,000 full-request input tokens and 500 total output tokens per generation
  attempt, with at most two attempts per turn and 100 generation attempts total.

The 500-token output ceiling includes visible answer and non-visible reasoning
tokens. Each generation attempt first makes one exact full-request count call,
so the external-request ceiling is 100 count calls plus 100 generation calls,
not 100 combined HTTP calls. A count call is separately capped and reserved;
under the reviewed case-18041142 evidence its planned reservation is zero, but
an ambiguous count still retains its reservation identity and stops the run.
The counted canonical request includes the serialized instructions, all bounded
conversation history, retrieved tenant evidence, current message, structured
response schema and other Responses framing. A count above 3,000 blocks
generation.

The immutable tenant/session/run activation manifest is intentionally not
created during offline preflight. The current disposable database has no
evaluation run or release decision, and its existing sessions contain prior
messages or handoff/closed state. Exactly ten fresh synthetic sessions, the
matching offline RAG contract baseline/candidate and authenticated `evaluation.manage`
release decision must be created immediately before an approved execution. The
manifest expiry is limited to five minutes through two hours; binding current
stale sessions or pre-creating a decision before final authorization would be
unsafe. The exact manifest and its canonical hash must be shown in the final
execution record before either activation flag changes.

The manifest also binds the canonical 50-turn fixture SHA-256, the
evaluation-spec source SHA-256, the runner source SHA-256, and the reviewed Git
commit. The runner recomputes all four after loading approval and before
constructing an HTTP client or prompting for a key. Fixture, source, commit, or
session drift therefore fails closed.

The normal legacy `AIEvaluationService.run_rag_evaluation` provider path is not
used for this prerequisite because it can generate billable traffic outside the
controlled ledger. The provider-free contract path described below replaces it
without representing deterministic retrieval as real model evidence.

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
the configured run. That authenticated decision snapshot is the authoritative,
immutable copy of the server-validated manifest; run input is not trusted for
activation. The manifest binds ten synthetic public-chat sessions
(five turns each), expires, and retains the 50-turn/100-attempt/US$0.10 limits.
A session outside that set or another tenant falls through to the existing responder instead of being
disrupted. This reuses existing records and adds no migration or public approval
endpoint. An authenticated tenant member with `evaluation.manage` may attach
the exact reviewed manifest only while recording an approved release decision.
The server validates every bound field and copies it into the immutable
decision snapshot; tenant and deciding subject still come exclusively from the
authenticated context. Ordinary release decisions are unchanged. This records
review but does not install a provider or execute the evaluation.

Operator evidence labels are proposals, never attestations. The release service
promotes a proposal only after the complete tuple matches a server-owned
independent billing-evidence registry. That registry is deliberately empty at
this checkpoint: case 18041142 is supporting AI Support Agent evidence, not
independent proof of free counting, and the Malaysia tax/uplift values remain
planning assumptions. A caller cannot turn either claim into independent
evidence by changing JSON or CLI flags.

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
   policy, and current official prices immediately before approval. On
   2026-10-24 the official model and pricing pages listed `gpt-6-luna` at
   US$0.10/M short-input tokens and US$0.50/M output tokens. Output includes
   non-visible reasoning tokens. Explicit cache mode with no breakpoints avoids
   cache reads and writes.
3. Verify the owner's API project can access that exact model using a securely
   configured restricted credential. No credential exists in this source path.
4. Deliberately construct the reviewed development-only HTTPS source at startup
   with a securely injected restricted credential. The application lifecycle is
   now wired to an injection seam, but checked-in startup intentionally leaves it
   uncomposed. The source pins the count/create endpoints, zero retries and
   redacted failures; all tests inject a mock client.
   Its input-count transport is composed only by the explicit development
   lifecycle and can dispatch only after decision-bound count admission. Tests
   inject a socket-free client. Checked-in startup never installs it.
5. Obtain explicit owner approval for the provider, model, credential method,
   50-turn case set, and US$0.10 hard ceiling before any metadata or generation
   request.
6. Add independently reviewed count-billing and all-in evidence to the
   server-owned registry in a separately reviewed source change. Caller JSON,
   CLI values, the case-18041142 screenshot alone, and an authenticated
   manager's unsupported assertion cannot populate that registry.

## Operator dry run and go/no-go

No activation record or credential should be created while the count-endpoint
billing evidence is unresolved. An operator can render the exact proposed
binding without network access, database writes, or reading a credential:

```sh
python -m scripts.plan_public_chat_model_activation \
  --organization-id 17 \
  --session-id 29 --session-id 30 --session-id 31 --session-id 32 --session-id 33 \
  --session-id 34 --session-id 35 --session-id 36 --session-id 37 --session-id 38 \
  --run-id controlled-eval-01 \
  --expires-at <FUTURE_ISO_8601_UTC_WITHIN_2_HOURS> \
  --additional-all-in-reserve-usd 0.00484 \
  --all-in-evidence-id <REVIEWED_MALAYSIA_PLANNING_EVIDENCE_ID> \
  --all-in-evidence-expires-at <FUTURE_ISO_8601_UTC> \
  --count-billing-status no_charge \
  --count-billing-evidence-id <REVIEWED_CASE_18041142_EVIDENCE_ID> \
  --count-billing-evidence-expires-at <FUTURE_ISO_8601_UTC> \
  --frozen-fixture-sha256 <PRINTED_CANONICAL_FIXTURE_SHA256> \
  --evaluation-spec-sha256 <REVIEWED_SPEC_SOURCE_SHA256> \
  --runner-source-sha256 <REVIEWED_RUNNER_SOURCE_SHA256> \
  --reviewed-source-commit <EXACT_REVIEWED_GIT_COMMIT>
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

The URL and billing status remain operator-supplied, unverified planner inputs:
this offline tool cannot certify or persist them. Authentication proves who
reviewed a manifest, not whether an external billing claim is true. The release
endpoint rejects the proposal unless its complete evidence tuple is already in
the server-owned reviewed registry; changing request verification strings
cannot self-attest it. The complete count policy,
evidence identity, per-attempt reservation and combined reservation are included
in the same proposed decision snapshot as the generation limits.

As of 2026-10-22, the official token-counting guide and API reference document
the authenticated `POST /v1/responses/input_tokens` operation but do not state
whether it is billed or identify a charge unit/rate. The current decision is
therefore **NO-GO**. The smallest unresolved evidence step is written OpenAI
billing/support confirmation that names the endpoint, model, region, charge
unit/rate, and failure/retry treatment. No authenticated count request has been
authorized or performed; only the narrow billing inquiry was authorized.

The owner supplied the full screenshot transcription of written guidance dated
2026-10-23 from an **OpenAI AI Support Agent**, case `18041142`, stating that
`POST /v1/responses/input_tokens` is free, including failed and rejected counting
requests, and does not consume paid generation quota. The owner reports receipt
at 2026-10-24 01:28 Malaysia time (UTC+08:00). The verbatim owner-provided text
and provenance are preserved in
[`docs/evidence/openai-support-case-18041142.md`](evidence/openai-support-case-18041142.md).
The original owner-supplied screenshot attachment was independently materialized
and visually inspected on 2026-10-24. Its 433137 bytes have SHA-256
`ec6aeba1c797adeab058174b17ef76dcb4f01281e0d98ec0f6958561abf624bf`;
the private image is deliberately excluded from this public repository. Its
visible body matches the preserved transcription. This verifies the screenshot
bytes and visible content, not full sender headers, DKIM/SPF, an original `.eml`,
or a human-specialist identity. The source gate does not require a
human-specialist role. An authenticated `evaluation.manage` principal must still
review this evidence, assign an expiring evidence identity, and approve the exact
manifest; the screenshot cannot self-assert `independently_verified` or activate
provider access. All authenticated provider requests remain blocked.

## Generation budget arithmetic

The 50-turn plan permits at most one explicit retry per turn, so 100 dispatched
generation attempts is a ceiling, not the expected run. SDK retries are zero,
and an uncertain count or generation outcome retains its reservation and blocks
all further attempts until reconciliation; it is never retried automatically.

At 3,000 full-request input tokens and 500 total output tokens per attempt:

- one standard attempt is US$0.00055; 50 attempts are US$0.0275; 100 are US$0.055;
- one conservative 10% financial buffer makes those reserved totals US$0.03025
  and US$0.0605; it is not evidence that a regional surcharge applies;
- applying the owner's separate 8% Malaysia planning allowance after that 10%
  buffer gives US$0.03267 for 50 attempts and US$0.06534 for 100 attempts;
- the corresponding incremental all-in reserve at the 100-attempt ceiling is
  US$0.00484 (`0.06534 - 0.06050`), leaving US$0.03466 beneath the US$0.10
  ceiling;
- regional and FedRAMP uplifts are each documented as 10%, but this plan does not
  assume they stack or apply to this account;
- no embeddings, tools, graders, translations, cache writes, or paid model
  metadata requests are included.

The durable controller already includes the 10% financial buffer in its
US$0.06050 generation reservation. Its separate
`additional_all_in_reserve_usd` field must therefore be set to only US$0.00484
for this planning scenario, not the full tax-inclusive total; adding US$0.06534
there would double count generation. The combined reservation is US$0.06534
and leaves US$0.03466 headroom. These values are conditional planning bounds,
not an independently verified account tax or surcharge determination and not
an all-inclusive invoice guarantee. The owner still needs to verify actual tax,
account-specific surcharges, and ancillary charges before manifest attestation.
Standard processing is independently pinned by the request field
`service_tier: "default"`; the financial buffer does not select a geographic or
premium processing tier.

Public-source planning evidence rechecked on 2026-10-24 consists of the
[OpenAI API pricing page](https://developers.openai.com/api/docs/pricing) for
the US$0.10/M input and US$0.50/M output rates, and Royal Malaysian Customs'
[transitional guide for digital services provided by foreign registered
persons](https://mystods.customs.gov.my/storage/app/media/pdf/guide/guide-on-transitional-rules-for-the-change-in-st-rate-to-8-on-digital-service-provided-by-frp.pdf),
section 3.2, for the statutory 8% planning assumption effective 2024-03-01.
Neither source verifies how the owner's specific OpenAI account will be taxed.

Restricted credential procedure: create a dedicated provider-project key with
only the minimum permissions available for Responses count/create and model
access; disable every unrelated endpoint permission. Set the provider project's
lowest practical budget/alert independently of the application ledger, while
recognizing that an alert is not a hard provider spending cap. Before credential
injection, an authorized operator must verify that already-available account
credits exceed the full US$0.10 ceiling and that this evaluation will not trigger
a prepaid-credit purchase or automatic recharge; unknown credit or recharge
state is fail-closed. No source code or operator may change those billing settings
automatically. Store the
key in the approved secret manager and inject it as `OPENAI_API_KEY` only into
the isolated local development process. Never put the value in chat, shell
history, CLI arguments, source, committed `.env` files, screenshots, or logs.
The planner does not inspect whether the variable exists and never reads its
value. Credential creation, injection, and account/model access checks remain
separately unauthorized and undone.

The prepared secret-entry boundary is
`app.services.model_eval_operator_secret.run_with_operator_api_key`. It accepts
the existing key only from a masked interactive terminal prompt, rejects
non-TTY input before prompting, and treats any `getpass` visible-input fallback
as an error. It does not read argv, environment variables, files, Keychain, or
the clipboard. The supplied callback must perform the separately authorized
operation in that same process; the key is not staged for later use. Python
strings cannot be guaranteed to be zeroized, and this design cannot protect
against root/admin tooling, debuggers, or sufficiently privileged same-user
applications. No caller should invoke the prompt until credential entry and the
immediately following operation are both authorized.

The safe command available now is preflight-only and never prompts:

```sh
uv run python -m scripts.preflight_controlled_eval_secret_entry
```

The separately reviewed one-shot metadata launcher is now
`scripts.verify_controlled_eval_model_access`. It fails before prompting unless
the environment is development and both activation flags are false. Its sole
operation is one fixed, non-redirecting, no-retry HTTPS `GET` to
`https://api.openai.com/v1/models/gpt-6-luna`; it neither counts tokens nor
creates a Response. The owner must run it manually in a private terminal only
after that exact operation is authorized:

```sh
cd /ABSOLUTE/PATH/TO/cxops-ai
env -u OPENAI_API_KEY -u EVALUATION_OPENAI_API_KEY \
  ENVIRONMENT=development \
  MODEL_EVAL_OPENAI_SOURCE_ENABLED=false \
  PUBLIC_CHAT_MODEL_EVAL_ENABLED=false \
  UV_CACHE_DIR=/tmp/cxops-uv-cache \
  uv run python -m scripts.verify_controlled_eval_model_access
```

It prints only `http_status`, `metadata_accessible`, and a sanitized
`error_category`, then exits. An uncertain timeout/network outcome is not
retried. The key exists only for that process invocation; a later controlled
evaluation requires another explicit masked entry and separate execution
approval.

OpenAI's [prepaid billing guidance](https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing)
states that prepaid purchases/recharges have a US$5 minimum, auto-recharge can
be enabled during setup, a configured threshold can trigger a purchase, and
credit cutoff can lag. Therefore this evaluation must not create a prepaid
setup, buy or top up credits, or alter recharge/payment settings. A permitted
future preflight must establish that usable existing credit is sufficient and
that the run will not trigger recharge; otherwise it remains NO-GO.

## Development-only composition and rollback

### Zero-provider RAG prerequisite

The former activation prerequisite required a successful manual
`gpt-6-luna` RAG run through the legacy evaluator. That path uses the ordinary
ChatOpenAI configuration and is outside the controlled reservation ledger, so
it is not an acceptable prerequisite for this US$0.10 run.

The replacement is `offline-grounded-contract-v1`: a persisted, tenant-scoped
lexical retrieval/citation contract run. It executes real current knowledge
lookups, records cases and critical metrics, costs zero provider requests, and
is explicitly **not** model-quality evidence. A matching baseline and candidate
must carry the same SHA-256 fixture identity. The ordinary authenticated
`evaluation.manage` decision remains mandatory and binds the separate
`gpt-6-luna` activation manifest; neither the runner nor a public request can
self-approve it.

For a disposable synthetic development tenant, prepare a JSON list whose cases
contain `id`, `question`, `expected_source_id`, and optional
`previous_customer_question`, then run:

```sh
ENVIRONMENT=development python -m scripts.run_offline_rag_prerequisite \
  --organization-id ORG --subject OPERATOR_SUPPLIED_AUDIT_LABEL \
  --cases /PRIVATE/PATH/synthetic-rag-cases.json --mode baseline

ENVIRONMENT=development python -m scripts.run_offline_rag_prerequisite \
  --organization-id ORG --subject OPERATOR_SUPPLIED_AUDIT_LABEL \
  --cases /PRIVATE/PATH/synthetic-rag-cases.json --mode candidate \
  --baseline-id BASELINE_ID
```

The CLI audit label is not authentication and cannot approve activation. The
commands do not construct a provider client or read a credential. Only an
authenticated owner with `evaluation.manage` may subsequently review the
candidate and exact activation manifest through the existing release-decision
API. A changed fixture, foreign tenant, regressed critical metric, production
environment, expired evidence, or changed manifest fails closed.

The source contains a disabled process-scoped composition:
`installed_development_evaluation_provider` installs count and generation only
for one local process lifetime and derives environment/feature gates from
validated server settings. Application startup does not call it. The existing
authenticated `evaluation.manage` release-decision endpoint is the sole path
that can attest and persist the bounded manifest; public chat and the planner
cannot do so. After evidence/account gates and explicit execution approval are
satisfied, an operator must:

1. use only `ENVIRONMENT=development` and a disposable synthetic tenant/session;
2. inject the restricted credential out of band, verify project/model access,
   and keep SDK retries at zero;
3. bind the exact tenant, session, run, `gpt-6-luna`, expiry, count evidence,
   3,000/500 token limits, 50-turn/100-attempt limits, pricing multiplier and
   US$0.10 ceiling into matching immutable run/decision snapshots;
4. enter the reviewed development lifecycle server-side, then
   enable both `MODEL_EVAL_OPENAI_SOURCE_ENABLED=true` and
   `PUBLIC_CHAT_MODEL_EVAL_ENABLED=true` only in that local process;
5. stop immediately on an uncertain outcome, token overrun, citation failure,
   budget rejection, expired evidence, or handoff.

Rollback is fail-closed: exit or stop the local process; the lifecycle removes
the injected provider in `finally`. Then
unset both flags and the process-scoped credential, restart with defaults, and
leave all durable reservations/audit records intact. Reconcile any unresolved
reservation before a later run; never delete it or retry automatically. The
production validator rejects both flags.

Go only when every item is true:

- authoritative count-endpoint billing and fresh generation pricing are known,
  and count plus generation reservations retain positive budget headroom;
- actual account tax/surcharge treatment has been reviewed, existing available
  credits cover the complete US$0.10 ceiling, and no prepaid purchase or
  automatic recharge can be triggered by the run;
- the exact `gpt-6-luna` model, Responses schema, account access and restricted
  credential have been verified without exposing the key;
- the existing 50-turn EN/FA specification, strict citation membership,
  3,000 full-request input cap, 500 output cap, zero SDK retries, one explicit
  retry, durable ambiguous-outcome reconciliation and US$0.10 ceiling remain
  unchanged;
- the decision snapshot exactly matches the expiring tenant/session/run
  activation manifest, and the final persisted handoff recheck remains enabled;
- the owner gives one explicit approval to execute the controlled evaluation.

Once the evidence, account-charge and credential gates are resolved, the
smallest sufficient owner approval is one bounded authorization naming: the
dedicated restricted API project/key delivery method; one authenticated,
non-generation account/model-access check; authenticated exact count requests;
the 50-turn EN/FA fixture with at most 100 dispatched generation attempts;
`gpt-6-luna`, standard service tier, synthetic tenant only, zero SDK retries;
and an all-provider-charge ceiling of US$0.10. That approval must explicitly
cover credential injection and provider access. It must not authorize production,
deployment, real customer data, tools/actions, or automatic retry of uncertain
outcomes.

Otherwise the decision is **NO-GO**. Run `python -m
scripts.plan_chat_model_evaluation` for the exact 50-turn fixture/replay output;
it remains offline and produces no model-quality score.

Mocks verify contracts and failure handling only. They do not demonstrate model
quality, factual entailment, account access, pricing accuracy, or paid spend.

### Single 50-turn operator command

The composed runner is `scripts.run_controlled_chat_model_evaluation`. It creates
ten fresh local sessions in canonical fixture order, prints the exact
session-bound manifest and request/cost assumptions, and stops by default. It
never accepts a key in argv, an environment variable, a file, or a pipe.

Offline preflight (both provider flags remain false):

```bash
ENVIRONMENT=development uv run python -m scripts.run_controlled_chat_model_evaluation \
  --organization-id <SYNTHETIC_TENANT_ID> \
  --widget-key <LOCAL_SYNTHETIC_WIDGET_KEY> \
  --embedding-origin <EXACT_LOCAL_ALLOWED_ORIGIN> \
  --run-id <OFFLINE_RAG_CANDIDATE_RUN_ID> \
  --expires-at <UTC_5_TO_120_MINUTES_FROM_NOW> \
  --count-evidence-id <REVIEWED_COUNT_EVIDENCE_ID> \
  --count-evidence-expires-at <UTC_FUTURE_TIMESTAMP> \
  --all-in-evidence-id <REVIEWED_ALL_IN_BUDGET_EVIDENCE_ID> \
  --all-in-evidence-expires-at <UTC_FUTURE_TIMESTAMP> \
  --additional-all-in-reserve-usd 0.00484
```

The preflight reports `approval_status: not_checked`, does not prompt for a key,
and performs no provider request. Fresh session tokens remain process-local and
are never printed. Its illustrative sessions are closed in `finally`; a live
run keeps the same process open through approval and attempts to close every
session on success, rejection, interruption, or error. Cleanup failures are reported as
a bounded count and must be reconciled locally; they are not silently called a
successful cleanup.

For a separately authorized live run, add `--execute-live` and set the two
development flags only for that process:

```bash
ENVIRONMENT=development \
MODEL_EVAL_OPENAI_SOURCE_ENABLED=true \
PUBLIC_CHAT_MODEL_EVAL_ENABLED=true \
uv run python -m scripts.run_controlled_chat_model_evaluation \
  --organization-id <SYNTHETIC_TENANT_ID> \
  --widget-key <LOCAL_SYNTHETIC_WIDGET_KEY> \
  --embedding-origin <EXACT_LOCAL_ALLOWED_ORIGIN> \
  --run-id <OFFLINE_RAG_CANDIDATE_RUN_ID> \
  --expires-at <UTC_5_TO_120_MINUTES_FROM_NOW> \
  --count-evidence-id <REVIEWED_COUNT_EVIDENCE_ID> \
  --count-evidence-expires-at <UTC_FUTURE_TIMESTAMP> \
  --all-in-evidence-id <REVIEWED_ALL_IN_BUDGET_EVIDENCE_ID> \
  --all-in-evidence-expires-at <UTC_FUTURE_TIMESTAMP> \
  --additional-all-in-reserve-usd 0.00484 \
  --execute-live
```

The command prints the exact manifest and pauses. While it remains open, an
independently authenticated owner with `evaluation.manage` submits an
`approved` release decision for the candidate through
`POST /evaluations/runs/<RUN_ID>/release-decision`, using the printed manifest
unchanged as `provider_activation` and the legitimate offline RAG baseline id.
The runner then re-reads and validates that tenant/run/session-bound decision
before constructing an HTTP client or opening the masked key prompt. A caller
cannot substitute request flags for the authenticated decision. Evidence ids
and verification claims printed before that decision are operator proposals,
not trust anchors. They become usable only after an exact match in the
server-owned reviewed registry and the authenticated reviewer approves the
resulting immutable snapshot. The current empty registry makes live execution
NO-GO before any key prompt or HTTP-client construction.

Only after that recheck does the same process request the existing restricted
key through `getpass`, install the provider for the context lifetime, execute
the frozen turns in order, and remove the provider/client on exit. A denied
approval, interrupt, count/generation ambiguity, exhausted reservation, changed
evidence, handoff, or tenant mismatch fails closed. Ambiguous reservations are
not refunded. The offline mocked integration drives all 50 ordered turns across
ten sessions through the composed disconnected provider and real public-chat
service on disposable PostgreSQL, then verifies persisted transcripts,
reservations and 50 fixture-linked cases. It does not measure genuine model
quality. The runner stores a separate tenant-owned
fixture-linked protocol record (status, handoff, reply presence, citation
contract enforcement, exact real-provider observation, and no raw customer
text). Exact real-provider observation requires the per-turn request identity,
`llm_called=true`, and model `gpt-6-luna`; stale, question-matched, or mocked
logs are only mock mechanics and never real-model evidence. Naturalness,
factual quality, and multilingual quality remain manually scored after the
genuine run.

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

The official pricing page at <https://developers.openai.com/api/docs/pricing>
listed the standard short-input/output rates above on 2026-10-24 and documented
10% regional and FedRAMP uplifts. The prompt-caching guide at
<https://developers.openai.com/api/docs/guides/prompt-caching> documents that
explicit mode without breakpoints avoids cache reads and writes. These sources
do not establish that the input-token counting endpoint is free.

This source check does **not** prove project access to `gpt-6-luna`, exact
tokenizer support, actual response compatibility, or future schema stability.
Those remain fresh-verification gates immediately before any separately approved
real evaluation.
