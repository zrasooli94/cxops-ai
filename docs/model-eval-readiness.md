# Bounded real-model evaluation readiness

The 50-turn English/Persian evaluation remains **provider-disconnected**. Run
`python -m scripts.plan_chat_model_evaluation` to render its synthetic cases,
expected outcomes, scoring rubric, and worst-case arithmetic. This command does
not read credentials or call a provider.

The plan repeats ten independent fixtures five times. Its rubric is a manual
acceptance definition, not an implemented grader or a multi-turn quality result.

## Enforced offline contract

- One durable maximum-cost reservation is committed before each mocked dispatch.
- Duplicate attempts are rejected explicitly; concurrent attempts stop while an
  unresolved reservation exists.
- SDK retries are zero and only attempt numbers 1–2 are admitted per turn.
- Missing, malformed, negative, or over-cap provider usage is ambiguous: the
  reservation stays open and the run stops for reconciliation.
- Confirmed usage within the 3,000-input/500-output caps settles the conservative
  maximum reservation. The reservation column has six-decimal precision, so it
  is intentionally not rewritten with a potentially rounded token-level value.
- Timeouts, malformed responses, and unknown citations remain ambiguous after
  dispatch. They are never silently retried.
- Persisted handoff state suppresses dispatch; the normal public-chat reply path
  also rechecks handoff immediately before persistence so late takeover wins.

## Finite blockers before an owner-approved real evaluation

1. Verify an exact tokenizer mapping for the final approved model. Installed
   `tiktoken` does not currently map `gpt-6-luna`; byte/character estimates are
   not accepted for admission.
2. Re-verify the exact model identifier, Responses schema, service tier, cache
   policy, and current official prices immediately before approval.
3. Verify the owner's API project can access that exact model using a securely
   configured restricted credential. No credential exists in this source path.
4. Implement and review a real transport that returns provider usage without
   weakening the disconnected adapter, zero-SDK-retry rule, or ambiguous-outcome
   reconciliation behavior.
5. Obtain explicit owner approval for the provider, model, credential method,
   50-turn case set, and US$0.10 hard ceiling before any metadata or generation
   request.

Mocks verify contracts and failure handling only. They do not demonstrate model
quality, factual entailment, account access, pricing accuracy, or paid spend.
