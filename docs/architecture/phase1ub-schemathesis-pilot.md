# Phase 1U-B — Schemathesis API contract pilot

**Status:** draft implementation, deployment prohibited until review. No model
calls or customer data. Scope: all **nine** `/public/*` operations.

## Why a separate CI-only fuzzing job

Our existing ~2,800 backend tests are strong hand-written cases. This pilot
adds Schemathesis 4.31.0's OpenAPI-driven malformed / boundary input generation
to the anonymous surface, without replacing pytest, introducing an LLM judge,
or changing production dependencies.

### Hard safety guarantees

1. Execute only with `CXOPS_API_CONTRACT_RUN=1`.
2. Fail before importing the API unless `DATABASE_URL` is an explicit
   local PostgreSQL asyncpg URL for **cxops_contract_disposable_test**, on
   port 5432 with disposable-only credentials.
3. Require `ENVIRONMENT=development`, `DEBUG=false`, test auth mode,
   **empty** OpenAI/evaluation credentials, and OFF lead capture, async chat,
   notification delivery, and onboarding knowledge setup.
4. Create the disposable DB exclusively in the dedicated CI job using a local
   pgvector/PostgreSQL 16 service. Migrate only that database.
5. Use `schemathesis.openapi.from_asgi("/openapi.json", app)` and `case.call()`
   to call the in-process FastAPI application. There is **no remote base URL**.
6. Public test input uses a deliberately invalid visitor bearer token; the
   fresh database has no registered widget key. Thus customer conversations,
   agents and tools are unreachable.
7. Use bounded generation (12 Hypothesis fuzz examples/operation, plus
   Schemathesis's examples and boundary coverage). Use deterministic seeds.
8. Check no unexpected 5xx (the deliberately disabled lead-submission POST may return 503), no tracebacks / echoed bearer credentials, bounded
   response size, and response schema for documented 200/202/422 responses.
   **Undocumented 401/403/404/429/503** are accepted as an existing OpenAPI
   documentation gap, not silently declared to conform.
9. Require explicit code review if the public surface changes from the
   nine reviewed operations. No network services, production DB, real provider,
   invitations, platform-admin, or webhook routes are in scope.

### What this first spike does NOT prove

- Authenticated staff API correctness (OpenAPI securitySchemes are absent;
  JWT headers/roles need a separate test fixture and explicit review).
- Successful full public-chat message, AI RAG answer, tenant-to-tenant identity
  or staff assignment flows (those are covered by existing deterministic tests).
- Stateful operation sequences / replay; run those only with a custom
  synthetic widget and fake models in a subsequent stage.
- Live-model behavior and RAG similarity threshold correctness.
- Exact runtime results until dedicated CI has executed.

### Review and rollout

Run the new `API Contract (Schemathesis)` CI job on PRs. It has its own
PostgreSQL service and no secrets; unlike the optional model-evaluation jobs,
it is a **required, real test**. Do not add Schemathesis to
`requirements.txt` or `requirements-lock.txt`: production images keep their
existing dependency set. Inspect failure reproduction and Hypothesis shrinks
without leaking generated content into public logs.

If the spike yields findings, fix them in an isolated follow-up PR; never
weaken tenant authorization or accept 5xx responses as a baseline.
Transitive dependency license and reproducible lock are future release gates.

## Discovery from first actual CI execution

- **Unsupported methods are generated intentionally.** Schemathesis uses cases
  such as `TRACE` against an operation normally declared as `GET` or
  `POST`. The pilot now asserts that those attempts are rejected (405) and
  still guards against 500 or response leakage. A request's actual method is
  not silently mistaken for the original OpenAPI operation method.
- **Expected disabled-service response.** When lead capture is explicitly OFF,
  `POST /public/leads` returns 503. The pilot permits that *one* known
  response, while unexpected 5xx remain release failures.
- **Known OpenAPI semantic gap.** The `LeadSubmission.website` field is
  represented as a string but the Pydantic implementation requires a valid
  domain; Schemathesis can generate schema-compliant values that the service
  rejects with 422. This is recorded for a follow-up OpenAPI description
  improvement. The pilot uses operation-level **response-schema-only**
  validation for documented 200/202/422 responses and does not claim to
  certify positive-request acceptance or every undocumented 4xx.
- **Provider warm-up without model secrets.** The ASGI lifespan emits a
  bounded warm-up error when `OPENAI_API_KEY` is absent; tests should verify
  that no model network calls occur. This is not live-model coverage.

The targeted pilot is evidence of anonymous API robustness under generated
inputs, *not* full production RAG behavior, business-workflow correctness, or
certification of the complete OpenAPI contract.
