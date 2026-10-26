# Controlled evaluation runner corrective readiness

Date: 2026-10-26 UTC

Scope: corrective follow-up to the independent review at
`/Users/zakerhussainrasooli/Documents/Codex/2026-10-26/task/cxops-fd79e724-independent-readiness-review.md`.
No provider request, credential, deployment, or paid operation was used.

## Corrected boundaries

1. The immutable activation manifest binds the canonical fixture SHA-256,
   evaluation-spec source SHA-256, runner source SHA-256, reviewed Git commit,
   and ten fresh sessions. The runner rejects critical-source working-tree
   drift and recomputes the identity after approval, before client/key setup.
2. CLI evidence is always `operator_supplied_unverified_by_dry_run`. The
   authenticated release service can promote it only on an exact match in the
   server-owned reviewed registry. The checked-in registry is empty because
   case 18041142 alone does not independently establish free counting and the
   tax/uplift values are planning assumptions. Live execution is therefore
   NO-GO without a separately reviewed source update containing legitimate
   evidence.
3. Each model answer carries a deterministic run/tenant/session/turn request
   identity. The runner queries only that exact tenant-owned log and records a
   real-provider observation only for `llm_called=true`, model `gpt-6-luna`, and
   the exact request ID. Mocked/stale logs are never model-quality evidence.

## Reproduction

Use a migrated disposable loopback PostgreSQL database whose name ends in
`_disposable_test`, leave provider keys empty, and run:

```sh
DATABASE_URL=postgresql+asyncpg://127.0.0.1:PORT/DB_disposable_test \
ENVIRONMENT=test JWT_SECRET=test-only-synthetic-jwt-secret-32chars \
OPENAI_API_KEY= EVALUATION_OPENAI_API_KEY= \
UV_CACHE_DIR=/tmp/cxops-uv-cache \
uv run pytest -q \
  tests/test_model_eval_billing_evidence.py \
  tests/test_model_eval_activation_plan.py \
  tests/test_run_controlled_chat_model_evaluation.py \
  tests/test_ai_evaluation_api.py \
  tests/test_public_chat.py \
  tests/test_model_eval_controller.py
```

Measured locally: 196 passed, two pre-existing warnings. The public-chat group
includes the full 50-turn mocked workflow through the real service and
PostgreSQL. It proves mechanics and persistence, not genuine model quality.

## Remaining external blockers

- independent count-billing evidence and independently reviewed all-in account
  charge treatment suitable for the server-owned registry;
- owner review of the exact post-commit manifest and a fresh authenticated
  `evaluation.manage` approval;
- current generation entitlement/account-credit/recharge confirmation and
  separate live-execution/key-entry authorization.

The conditional 100-generation-attempt planning bound remains US$0.06534
(US$0.055 base, 10% planning uplift, 8% Malaysia planning tax assumption), with
US$0.03466 headroom below US$0.10. Up to 100 count calls are separate; their
real billing treatment remains externally unresolved and is not assumed free.
