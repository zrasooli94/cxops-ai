# CXOps AI chatbot — C5 citation/source consistency safeguard

**21 October 2026 · Source-only DRAFT · RELEASE HOLD**

## Why this milestone exists

A valid [S1] citation proves that the answer named a retrieved source ID.
It **does not prove** that the source supports the answer. C2 showed a
fictional policy saying "Aster bicycle repairs are not free" while a fake
model answered "All Aster bicycle repairs are free. [S1]".

C5 now rejects this narrow direct contradiction **before publishing the
customer reply**. The existing human-handoff path handles the ineligible
result. No new AI model, provider call, approval or database migration.

## Proposed changes

- **Actual runtime guard:** app/services/public_answer_evidence.py detects
  explicit opposite negation on an identical English subject, copula
  (is/are/was/were), and a small closed set of policy predicates (free,
  automatic, available, guaranteed, included, eligible, refundable, allowed,
  permitted), comparing only *cited* source content. The comparison is
  deterministic, local and contains no provider SDK/network requests.
- **Public auto-reply path:** app/services/rag_reliability.py invokes the
  guard after existing tenant-scoped retrieval metadata and citation-ID
  checks. The known false-free answer returns explicit_source_conflict;
  output longer than 5,000 characters or cited source text longer than
  5,000 characters fails closed rather than exceeding inspection limits.
- **Generation prompt:** app/services/rag_service.py explicitly instructs
  the model to preserve negated restrictions rather than reversing them.
- **Operational visibility:** app/core/metrics.py uses closed, bounded labels
  for the new and previous C2 invalid-evidence reasons. Customer content
  never appears in metric labels.
- **API proof:** a new real public-chat HTTP test with scripted RAG model
  output verifies that the directly contradictory cited answer causes
  human_requested + normal handoff text, with zero outbound jobs.

## Exact scope and known limitations

**This is NOT semantic entailment or a universal fact checker.** It detects
only a *narrow exact-polarity pattern* in English, so all of these still
require independent evaluation and may pass the structural gate:

- Paraphrases ("repairs are complimentary" versus "repairs are not free"),
  shorter/different subjects, time/tense changes and indirect negation.
- Persian, Arabic and other languages that the English pattern does not
  support; different phrasing can evade the rule.
- Numbers/dates/prices, conditions, comparisons and contradictory versions.
- Multiple claims within one sentence with a citation supporting only one.
- A perfectly valid citation to a source unrelated to the model's answer.
- Retrieval of a stale or unauthorized document (rely on separate tenant
  search/authorization checks); prompt injection in documents.

False positives are also possible if superficially identical statements
have different temporal/policy conditions. The chosen conservative action
is staff handoff, not automatic contradiction repair or a claim of certainty.

The tests deliberately preserve examples of **false semantic claims that
this limited detector does not flag**. Those are unresolved release
quality risks, not missing CI successes.

## Offline acceptance

Run new evidence tests, previous C2 FAQ/citation and C3 follow-up checks,
existing real sync/async FastAPI public-chat and tenant tests, and pinned
Ruff with **disposable, loopback PostgreSQL 16** in a GitHub Actions PR
workflow. The existing strict C2 guard refuses remote DB URLs, missing CI
flags, OpenAI/Zendesk/notification credentials or enabled external actions.
Model answers and retrieval are synthetic/local fakes; no provider requests.

Green tests demonstrate narrow deterministic behavior, **not accuracy of a
real provider model**. Human-labelled semantic grading (with source text,
citation scope and independent reviewer) is a separate, necessary milestone.

## Later product steps

Create a source-only reviewer-labelled claim-evidence evaluation dataset,
then obtain separate approval for a small paid real-model synthetic
evaluation and desktop/mobile browser pilot. No automatic production
publication should be certified from syntactic citations alone.

**Safety:** PR draft/unmerged, master and production services unchanged,
Render synthetic Cron suspended, real production GitHub worker unchanged,
Phase 1Y writes off, repository public, no paid infra/secrets deployed.
