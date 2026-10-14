# Phase 1U-B - implementation readiness: promptfoo, Schemathesis, Ragas

**Status:** read-only assessment. Nothing here is implemented, installed in
the repository, or wired into CI. Facts marked (reg) come from the package
registries / project pages read on 2026-10-15; "estimated" marks my arithmetic.

## 1. What CXOps already has

| Asset | Size | Notes |
|---|---|---|
| Backend tests | ~2,060 test functions, ~2,700 collected cases, 114 files | Disposable PostgreSQL; fake models/embeddings; pytest-asyncio |
| RISPU question reliability suite | 123 cases (+ 74 certification tests) | Fake models by design: deterministic envelope only |
| Live RAG evaluation | `evals/rag_cases.json` 6, `automotive_rag_cases.json` 10 | `scripts/evaluate_rag.py`; expected sources/terms; **skipped in CI unless secrets exist** |
| Live agent evaluation | `agent_cases.json` 5, `automotive_agent_cases.json` 18 | `scripts/evaluate_agent.py`; same optional gate |
| HTTP API | FastAPI, 133 paths / 155 operations (83 GET, 56 POST, 13 PATCH, 3 DELETE); 9 unauthenticated `/public/*` operations | OpenAPI is generated but declares **no `securitySchemes`** (auth is dependency-based) |
| Prompts | decision prompt (22 rules), RAG answer prompt, repair prompt | exercised with fakes in CI; real model only in the optional jobs |

Known gaps this phase should address, from earlier phases:

1. The real model's wording, citation behaviour and injection resistance are
   never exercised in required CI (Phase 1R "not certifiable offline").
2. The production similarity floor (0.35) and margin (0.12) were never
   validated against real embeddings.
3. Public endpoints are tested with hand-written cases only; no generative
   input robustness testing of the 9 unauthenticated operations.
4. No response-schema conformance check between Pydantic models, the OpenAPI
   document and the frontend client.

## 2. Candidates

### promptfoo (reg: 0.124.1, MIT, Node >= 22.22, 76 runtime dependencies, ~32 MB unpacked)
* Purpose: prompt/LLM evaluation matrices, assertion types (contains, regex,
  JSON schema, LLM-rubric), red-team plugin generation, CI exit codes.
* Fit: closes gap 1. Convert `evals/rispu_question_reliability.json` into
  promptfoo tests against a **local HTTP provider** (the app's
  `/public/chat/messages`, real model, disposable DB, `EVALUATION_*` key), with
  assertions that the reply is grounded, cites nothing foreign and does not
  contain the forbidden promises already listed in the data file.
* Governance: the project page states it is now part of OpenAI. Treat as a
  dependency-risk item; pin an exact version, never `npx promptfoo@latest`.
* Telemetry/updates: documented to be disable-able by environment flags; verify
  in the spike and set them in CI.
* Cost (estimated): ~125 cases x 2 model calls x ~1.5k input tokens at
  gpt-4o-mini rates = about $0.08 per run; with an LLM-rubric grader about
  $0.20.
* Overlap: partial with `evaluate_agent.py`; promptfoo adds matrices,
  rubric assertions and red-team generation, not retrieval metrics.

### Schemathesis (reg: 4.31.0, MIT, Python >= 3.11)
* Purpose: schema-driven property-based API testing, stateful operation links,
  JUnit/HTML/HAR reports, replay and baseline for CI.
* Fit: closes gaps 3 and 4. Highest value targets: the 9 public operations
  (no 5xx, no stack trace, no cross-tenant data, bounded latency on malformed
  bodies/headers) and Pydantic response conformance for staff endpoints.
* Required groundwork: because `securitySchemes` is empty, auth must be
  supplied explicitly (HS256 test tokens as the suites already do); either
  declare the schemes in OpenAPI (small, non-breaking change) or configure
  headers per run.
* Safety: it issues create/update/delete calls, so only against a disposable
  database with the platform-admin, Zendesk and webhook routes excluded;
  public rate limits must be raised for the run, not bypassed in code.
* Cost: CI minutes only (no model calls if the fake-model fixtures are used).

### Ragas (reg: 0.4.3, Apache-2.0, Python >= 3.9)
* Purpose: RAG metrics (faithfulness, context precision/recall, answer
  relevancy) computed with an LLM judge (and embeddings for relevancy).
* Fit: closes gap 2 with measured numbers on the approved RISPU knowledge:
  sweep `rag_min_similarity` / margin / `top_k` against real embeddings and
  report faithfulness and context precision at each setting.
* Footprint: heavy tree (numpy, datasets, langchain-community, scikit-network,
  instructor, ...). Must live in a separate `requirements-eval.txt`, never in
  `requirements.txt` or the lock that Render installs.
* Telemetry: opt-out (`RAGAS_DO_NOT_TRACK=true`), so it must be set.
* Cost (estimated): ~125 cases x 4 metrics x 1-3 judge calls; roughly $0.2-0.4
  per full run at gpt-4o-mini rates. A weekly or on-demand cadence keeps this
  under a few dollars a month; nightly would be $6-12 and should not be the
  default against the $50 budget.
* Overlap: partial with `RAGEvaluationService` (expected sources/terms);
  Ragas adds a semantic faithfulness score the in-house checker cannot give.

## 3. Licensing and supply chain

MIT, MIT and Apache-2.0 are compatible with the codebase. Transitive licences
have **not** been audited (not tested); the spike must produce a
`pip-licenses` / `license-checker` report. None of the three ships to
production: promptfoo and Schemathesis are CI/dev tools, Ragas is an offline
script with its own requirements file. Judge-model calls send only synthetic
evaluation inputs and approved public knowledge, never production customer
text.

## 4. Proposed implementation plan (not started)

| Step | Change | Files (planned) | Gate |
|---|---|---|---|
| 1 | Schemathesis spike on public operations, disposable stack, fake models | `tests/test_openapi_contract.py` or `scripts/run_schemathesis.sh`, `requirements-dev.txt`, CI job `api-contract` | Zero 5xx, zero leaks on `/public/*`; baseline file for known findings |
| 2 | Optional declaration of auth schemes in OpenAPI | `app/main.py` / route dependencies | Existing tests unchanged; frontend client unaffected |
| 3 | promptfoo suite generated from the reliability JSON, real model, nightly or label-triggered | `evals/promptfoo/`, `scripts/build_promptfoo_config.py`, workflow gated like the existing `EVALUATION_ENABLED` jobs | Pinned version, telemetry off, cost report per run |
| 4 | Ragas threshold study, on demand | `scripts/ragas_threshold_study.py`, `requirements-eval.txt`, `docs/performance/` report | Written recommendation for the similarity floor; no production setting changed by the script |

Each step is its own PR, with the existing "optional live evaluation" gating so
a fork or a missing secret skips cleanly (the existing jobs report "skipped"
as pass, which should be made visibly different in step 3).

## 5. Risks

* **Cost creep**: LLM-judged suites multiply calls; enforce a per-run token
  cap and report cost in the job summary.
* **Flakiness**: model-graded assertions are nondeterministic; require
  repeatability (3 runs, majority) before a check can block a PR.
* **Data safety**: Schemathesis against anything but a disposable database is
  prohibited; the production URL must be impossible to configure in CI.
* **Dependency risk**: promptfoo's size (76 runtime deps) and ownership change;
  pin and review on each bump.
* **Signal duplication**: do not re-implement existing deterministic suites;
  new tools only where real-model or generative coverage is missing.

## 6. Readiness verdict

Ready to start with step 1 (Schemathesis spike: no model cost, no new runtime
dependency, closes a concrete gap). Steps 3 and 4 need an owner decision on
the evaluation-key budget and cadence before any code is written.
