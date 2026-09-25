# Automotive Pilot — Consulting Case Study

## The Ask

A regional "A1" auto dealer network wants to see how CXOps AI would run their
support operation before committing real customers, credentials, or data. The
commercial ask: a believable dealership-shaped pilot that demonstrates value
with *zero* production risk.

## The Challenge

- No real customer data may enter the system; no real VINs, PII, or
  third-party credentials.
- Vehicle acquisitions and auto parts are different services with different
  SLA targets, yet the platform is generic by design — no automotive columns,
  no migrations, no specialized agent.
- The evaluation suite already supports 14–18 scenarios with strict thresholds;
  the pilot must be a first-class evaluation target, not a demo afterthought.

## Design Decision

Treat the dealership as a standard tenant. Everything A1 needs already exists
in the generic platform: organizations, SLA policies, queues, tickets,
conversations, a knowledge pack, the Coordinator/Knowledge/Action agent
pipeline, and the Phase 1J/1K evaluation pipeline. The pilot therefore adds no
new backend surface — only a dataset, a deterministic seeder, an optional
safe replay, a guarded static showroom page, and tests that prove the data is
coherent and safe.

## The A1 Tenant

A 40-ticket organization across two windows (current + previous):

- **Vehicle Acquisition** — valuation, inspection, offer, pickup, documents
  and first payment. 9 scenarios against 3 queues.
- **Auto Parts** — availability, ordering, pricing, compatibility, returns and
  warranty. 8 scenarios against 2 queues.
- **General Support** — cross-cutting questions plus a prompt-injection safety
  scenario.

Every customer is `@example.com`, every conversation is local, and the full
knowledge pack carries the disclaimer "Illustrative pilot policy — not legal or
production operating guidance."

## Workflow Value

For each of the 18 catalog scenarios the pipeline is expected to:

1. Classify intent (information / action / mixed / none).
2. Route to the right specialist path: coordinator → knowledge (informational),
   coordinator → action (operational), or coordinator → knowledge → action
   (mixed).
3. Decide a tool (`zendesk.send_reply`, `zendesk.add_internal_note`,
   `zendesk.update_ticket`, `human.review`, `none`).
4. Decide whether to auto-execute or require human review.

Only `documents-001` (an internal note) is allowed to auto-execute. Two cases
are deliberately risk-labeled: part compatibility (must not be confirmed
without vehicle/part details) and prompt injection (must not follow embedded
instructions).

## Security & Safety Alignment

- **Tenant safety**: the seeder is proven — via tests with decoy
  organizations — to touch only the pilot tenant.
- **External-execution safety**: the optional replay defaults to `--no-auto`,
  never reads external credentials, and keeps approval flows intact.
- **PII safety**: synthetic emails/phones; no real VINs; no third-party ids.
- **Truthfulness**: no fake AI runs, requests, or evaluation rows are ever
  seeded; live replays persist whatever the real pipeline decides.

## The Showroom

The control-center `Automotive Pilot` page is a static, capability-guarded
showroom: SLA policies, queues, the knowledge pack, the full 18-scenario
catalog with risk badges, the safe replay set, and a four-step demo
walkthrough. Navigation places it after Transformation with a dedicated `car`
icon.

## Demo Walkthrough

1. **Seed**: `python -m scripts.seed_automotive_pilot --with-knowledge-embeddings`
   creates the tenant idempotently (optional embeddings required).
2. **Browse**: the dashboard, tickets, inbox, operations, transformation and
   knowledge workspaces all operate on the pilot like any demo tenant.
3. **Replay (optional)**: `python -m scripts.run_automotive_pilot` runs the real
   agent over 4 safe tickets. Automatic queueing stays off, approvals apply.
4. **Evaluate**: the agent and RAG case sets are valid Phase 1J/1K inputs for
   the existing evaluation runners.

## Outcome

A believable automotive pilot delivered with **no new platform surface**: the
generic product stays generic while the demo is exactly as rich, safe and
evaluable as the customer asked for.