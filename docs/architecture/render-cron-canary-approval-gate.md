# CXOps AI — Render Cron synthetic canary approval gate

**Source-only proposal. No Render Cron has been provisioned, billed, scheduled or activated by this change.** Tracks [worker issue #40](https://github.com/zrasooli94/cxops-ai/issues/40), [draft PR #41](https://github.com/zrasooli94/cxops-ai/pull/41) and [draft PR #42](https://github.com/zrasooli94/cxops-ai/pull/42). This document belongs to an additional stacked draft review PR. Reviewers must record the exact head/base SHAs after every update.

## 1. What the owner's budget approval means

The owner accepted an **initial USD $2/month operating target**, with a **USD $6/month escalation threshold** for the intended Render Cron architecture. This is a planning/budget decision **only** and does not authorize creating a billable Cron service, entering credentials, enabling its schedule, running migrations, executing production jobs, modifying the GitHub worker, merging PRs or making the repo private.

**$6 is not a Render-enforced hard billing cap.** Render charges Cron compute by active runtime, with a $1/month per-Cron-service minimum. On 0.5c-512mb the listed rate is $0.00016 per active minute. Historical GitHub Actions job durations are not Render measurements; building the service and future runs can have additional cost. At 288 nominal runs/day and a 240-second bound, active worker compute alone could be approximately $5.53 for 30 days or $5.71 for 31 days before manual runs, abnormal container lifespan, builds or outbound usage. A $2 estimate is an alert target, not a guarantee.

**Separate authorization is required for every paid provisioning or activation milestone.** Until then, use this source-only runbook and offline tests.

## 2. Provider behavior that matters for safety

Render Cron requires an explicit UTC schedule and command when the service is created. A configured service can begin running automatically. An "inert Cron canary" refers to **the no-op script**, not to an existing provider-side OFF switch. Provisioning even an isolated synthetic Cron may incur cost and needs distinct owner approval.

Render documents single-run protection for **one Cron service**. That does **not** prevent overlapping runs of the current GitHub Actions production worker; the Postgres claim fencing and SLA advisory locking remain necessary. A manual trigger when a scheduled run is active causes Render to **cancel the current run**. Render does not publish a fixed scheduled-start latency SLA. No assumed scheduled-at environment variable should be used as measured evidence unless verified from Render's actual data.

Do not add render.yaml or an infrastructure auto-provision file to this draft. It must not change a deployed Render service, GitHub cron, PR #41/#42 or Phase 1Y.

## 3. Inert canary implementation

Proposed command for a *future separately approved synthetic-only Cron service*:

    python -m scripts.render_cron_synthetic_canary

The program:
- Exits code **78** and emits no success telemetry unless the **exact** environment variable CXOPS_RENDER_CRON_SYNTHETIC_ONLY=true is set. Default is disabled.
- Refuses ENVIRONMENT=production or ENVIRONMENT=staging, recognized database, encryption, OAuth, AI/provider, webhook and release-activation variables, even if an unsafe variable points to a test host.
- Imports Python standard-library modules only; it has no application/worker imports, database requests, network client, provider calls, filesystem writes, shell subprocesses or customer data. Never point it at a production environment group.
- Logs one JSON event (cxops_render_cron_canary_v1) containing only actual UTC start/finish, random run ID, monotonic elapsed milliseconds, process peak RSS MiB and explicit synthetic/zero-side-effect markers.
- Optionally sleeps for at most **one second** for a bounded synthetic check. That is **not representative** of the 240-second production worker memory and load.

**Never substitute the real production launcher sh scripts/run_render_cron_worker.sh during a synthetic canary**. That launcher passes production preflight then runs actual SLA, integration-job and chat-expiry work.

## 4. Proposed Render canary configuration — NOT YET AUTHORIZED

Review these configuration fields with the owner and an independent human reviewer **before a paid creation action**:

| Field | Isolated proposal | Must not happen |
|---|---|---|
| Source | Exact reviewed synthetic branch/commit; access scoped to this repo | Implicit deployment from master / live production ref |
| Region | Singapore | Cross-region switching |
| Type and size | Render Cron 0.5c-512mb; billable | New instance upgrades/add-ons |
| Schedule | UTC */5 * * * * **only once approved for synthetic Cron provisioning** | Production job command on initial creation |
| Command | python -m scripts.render_cron_synthetic_canary | scripts.worker, migrations or production preflight |
| Variables | CXOPS_RENDER_CRON_SYNTHETIC_ONLY=true only, plus standard Render metadata | DATABASE_URL, OPENAI_API_KEY, ENCRYPTION_KEYS, Nhost/Neon, Zendesk, notifications or secret groups |
| Auto deploy | OFF / pinned revision where supported | Production or unrelated branch push deployment |
| Observed output | Synthetic JSON only | Real ticket, database, integration, message, network or provider side effect |

The owner must explicitly approve the **specific** service creation and 24-hour Cron schedule, with a documented ability to suspend it and clear instructions to stop on unexpected billing or unsafe configuration. Do not assume a small monthly target is a provider-side spend limit.

## 5. Evidence and offline analysis

Only after isolated paid-canary authorization, observe **at least 24 continuous hours** (288 nominal UTC five-minute slots); extend to 48–72 hours after anomalies. Preserve versioned evidence without secrets:

1. Render service ID, exact Git SHA, plan, region, schedule, command and redacted environment settings; owner authorizes this exact topology.
2. Render **Runs** history/export covering **all scheduled, failed, canceled, delayed and manually triggered events**, with UTC observed start/end and nonzero exits; reconcile event count to expected schedule.
3. Extract *JSON-only* synthetic stdout events from Render logs. Keep original logs privately for audit; never paste raw environment variables or credentials into chat or GitHub.
4. Measure observed run distribution, missed and duplicate five-minute **start-time buckets**, p95 observed UTC phase, p95 process peak RSS and provider-reported charges.
5. Run the offline canary report, e.g. with a complete UTC 24-hour window (inputs are not fetched remotely):

    python scripts/analyze_render_cron_canary.py --input evidence/canary-json-only.jsonl --start 2026-10-19T00:00:00Z --end 2026-10-20T00:00:00Z

The report returns **NO_GO** for missing/duplicate observed buckets, p95 clock phase >60s or p95 process RSS >=384 MiB; **INCOMPLETE** for windows under 24 hours; or **OBSERVATION_ONLY** if those observable checks appear clean. It **never returns a production GO or certifies true schedule latency**. A delayed invocation could land in a later five-minute bucket, and canceled runs might lack application logs. Only Render's independent Runs history and any trusted scheduling metadata can close that gap. The no-op process RSS is not proof that provider-heavy real work fits 512 MiB.

Alert on anomalous lag, unsuccessful/canceled runs, missed buckets, memory approaching 384 MiB and a projected $2 monthly spend; escalate before $6. Render may not expose an enforceable hard cron-compute spending cap.

Before moving beyond the synthetic canary, independently validate the actual bounded Python executor on disposable PostgreSQL with representative synthetic workloads, cancellation, competing GitHub claims, stale-claim recovery, SLA scan, public-chat expiry parity and resource limits. **No live customer or provider traffic** during that test.

## 6. Human security review and staged release

A qualified identifiable human reviewer must independently inspect the exact **PR #41 + #42 + synthetic canary PR** merged design (or current stacked equivalent). Review credentials and fail-closed behavior; Render service isolation and schedule cancellation; secrets; tenant/job boundaries; Postgres fencing; recovery; observability accuracy; resource limits; projected billing and rollback. A successful AI rereview is *not* formal human approval. The owner may acknowledge risks directly but cannot submit a supposed independent human review from a relative's GitHub identity without that person actually conducting it.

Human-review record:

- Reviewer identity, relevant software/security qualifications, review date in UTC: __________
- Exact reviewed PR #41 head/base SHA: __________ / __________
- Exact reviewed PR #42 head/base SHA: __________ / __________
- Exact reviewed canary PR head/base SHA: __________ / __________
- Security scope, tests checked, severity-ranked findings, mitigation evidence: __________
- Residual risks, operational assumptions, conditions before live acceptance: __________
- Decision personally submitted by reviewer: APPROVE SOURCE FOR ISOLATED CANARY / CONDITIONAL / HOLD
- Separate approval to spend/provision service: YES / NO — **default NO**
- Separate approval for production secrets, schedule activation, worker cutover: YES / NO — **default NO**

**Passing source code and human source approval do not automatically authorize paid infrastructure or production activation.**

## 7. Future cutover and rollback — both remain unauthorized

Only after the owner approves a synthetic Cron canary and then a distinct production canary:
1. Maintain the existing GitHub worker as primary; confirm Postgres claim safety during any permitted shadow overlap and explicitly test the Render single-run guarantee against the additional GitHub worker.
2. Record seven or more days of safe production shadow with p95 start-lag measured from credible provider evidence, job backlog, p95/peak worker RSS <384 MiB, failure rate, claim recovery, canceled run handling and updated cost estimate.
3. Request a separate owner approval to enable the production Render worker schedule and share any secrets. Disable the GitHub schedule only after a verified cutover; retain manual GitHub fallback and observe another seven days.
4. If unsafe: prevent subsequent Render Cron runs using supported suspend/schedule settings, **do not manually trigger while active**; inspect outstanding pending/retry/processing state and wait for stale-claim fences before manual draining. Keep database evidence and the existing GitHub worker recovery option.
5. Keep Phase 1Y ticket writes OFF and draft PRs unmerged; GitHub privatization is a separate workflow once default-branch CI and Render/Vercel private access have been verified.

**Disposition:** synthetic canary source only. Budget target accepted. Real deployment, scheduled execution, paid provisioning, credentials, production cutover, GitHub visibility change, and independent human signoff are still HOLD.

Sources: [Render Cron](https://render.com/docs/cronjobs) · [Pricing](https://render.com/pricing) · [Render logs](https://render.com/docs/logging) · [Render service metrics](https://render.com/docs/service-metrics).
