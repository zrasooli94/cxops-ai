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
4. Review and deliberately compose the new development-only HTTPS source with a
   securely injected restricted credential. It pins the count/create endpoints,
   zero retries and redacted failures, but is not wired into startup. The checked-in
   feature flag is false and production rejects it; all tests inject a mock client.
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
