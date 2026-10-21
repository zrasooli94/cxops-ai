# CXOps AI — C6: Claim-by-claim human review workbench

**21 October 2026 · Offline / synthetic only · Draft PR / RELEASE HOLD**

The existing RAG evaluation engine measures source-title retrieval, expected
answer terms, citation validity, refusal and latency. Those metrics are useful
but **cannot determine whether each factual statement is supported by the
passage it cites**. C5 additionally catches a narrow English polarity reversal
("not free" vs "free"), not paraphrases or broader hallucinations.

C6 introduces a **two-reviewer worksheet and self-attested scorecard**.
It prepares the evidence for real human review, but **does not impersonate
a human reviewer or label any model answer as independently certified**.

## What exists

- `evals/chatbot_claim_review_synthetic.json`: 11 fictional support
  questions across two explicitly synthetic businesses, split into
  12 claim-level judgments with source IDs, complete cited passages and
  synthetic source revisions. Real customers and production tenant IDs
  are excluded.
- `scripts/chatbot_claim_review.py`: offline, stdlib-first CLI that:
  validates the dataset, fingerprints every claim/source snapshot,
  exports review CSV, separately merges independently completed reviewer
  sheets, requires dispute adjudication and reports candidate guard outcomes.
- `tests/test_chatbot_claim_review.py`: exercises the actual CLI functions
  with **fabricated reviewer test doubles**; it neither creates real human
  labels nor sends data to an external model or backend.
- Isolated read-only GitHub Actions checks: synthetic dataset, exported
  blank sheet, unfilled scorecard behavior, identity/evidence validation,
  report redaction, prior C5 guard regression and Ruff.

### How the review is supposed to work

Commands run locally from the repository root in Python 3.11+. The workflow
accepts **only** datasets explicitly marked `dataset_kind: synthetic`
and synthetic tenant slugs. The review CSV contains all fictional answer
text and source excerpts; **do not commit a filled-in sheet**.

1. Validate and produce a blank sheet. The tool refuses overwrite if an
   output already exists:

   ```bash
   python -m scripts.chatbot_claim_review validate
   python -m scripts.chatbot_claim_review prepare --out /tmp/cxops-c6-blank.csv
   ```

2. Distribute **separate private copies** of that blank sheet to two
   independent reviewers. Reviewer A fills ONLY `reviewer_1`,
   `reviewer_1_label`, `reviewer_1_evidence`, `reviewer_1_note`.
   Reviewer B fills ONLY the corresponding `reviewer_2` columns,
   without seeing A's judgments. They must not modify IDs, citations,
   answer text, source versions or fingerprints. Use secure local
   sharing, not public GitHub for completed forms.

3. Combine the two independently completed files:

   ```bash
   python -m scripts.chatbot_claim_review combine \
     --reviewer-one /tmp/reviewer-a.csv \
     --reviewer-two /tmp/reviewer-b.csv \
     --out /tmp/cxops-c6-combined.csv
   ```

   The CLI checks source-version fingerprints, reviewer slots, exact row
   set, evidence excerpts and that **claimed** reviewer IDs differ. It
   never overwrites original forms. It **cannot authenticate** those
   identities or cryptographically establish independent authorship.

4. If the labels disagree, give the **combined copy** to a third reviewer.
   Fill `adjudicator`, `adjudicated_label` and a nonempty
   `adjudication_note` for each disputed row. The adjudicator must
   use an identity distinct from both prior reviewers. Do not fake
   reviewer names, decisions or supporting evidence.

5. Score the combined synthetic sheet against the existing **C5 direct
   contradiction rule**:

   ```bash
   python -m scripts.chatbot_claim_review score \
     --sheet /tmp/cxops-c6-combined.csv \
     --report /tmp/cxops-c6-summary.json \
     --require-complete
   ```

   Exit code **2** means an incomplete/disputed sheet or invalid
   evidence. The JSON report contains only synthetic case/claim IDs,
   aggregate rates and fixed caution text—not reviewer names, answers,
   citations, source bodies or notes. Reports never overwrite an
   existing path.

## Reviewer rubric — *one claim at a time*

| Label | When it applies | Reviewer must supply |
|---|---|---|
| `supported` | The cited passage, in its stated scope/version, directly supports all parts of this one claim. | An **exact quoted excerpt** from a cited source |
| `contradicted` | One cited passage directly opposes this one claim, including explicit negation or incompatible condition. | An **exact quoted excerpt** showing the conflict |
| `unsupported` | The claim may or may not be true, but the cited passages **do not establish it**. | A written rationale (what evidence is missing) |
| `unclear` | Conditional, outdated, conflicting, missing-context or otherwise genuinely inconclusive evidence. | A written rationale; excluded from rate denominators |

Check **the cited passage**, not all accessible documents, public web,
prior customer turns or plausible general knowledge. If several
claims share one citation, assess **each claim separately**. If both
reviewers disagree, **do not silently use a majority vote**; require
third-party adjudication. Excerpt matching validates traceability only:
it cannot prove that a human interpreted the excerpt correctly.

## What the report actually measures

C5 currently emits only a deterministic **block** for clear lexical
direct-polarity contradictions; **absence of a block is not an approval**.

- **Unsafe candidate**: A manually labeled `contradicted` or
  `unsupported` claim that the C5 lexical guard would not block.
  This is a theoretical *potential* publication escape, **not proof
  a real customer actually received it**.
- **Unsafe blocked**: Contradicted/unsupported, and C5 detects a direct
  conflict. The latter is rare for unsupported content without an
  explicit negation.
- **Supported blocked**: A human-labeled supported claim that C5 would
  unnecessarily block (possible lexical false positive).
- **Supported candidate**: No C5 block; still not independently guaranteed
  accurate outside the reviewed passage.
- **Unclear**: Excluded from scored denominators; never counted as
  successfully verified support.

The JSON has `independent_human_identity_verified: false` and
`production_certified: false` **in every possible state**. Even two
completed reviewer columns only yield
`self_attested_complete_not_certified`. The code cannot authenticate
reviewers or upgrade a synthetic result into a real-model certification.

## Security, privacy and release requirements

- Offline CI does not connect to a backend, production DB, OpenAI,
  Zendesk, email or Telegram. No new SDK keys, paid jobs or networked
  AI evaluation. Tests fabricate reviewer identities **solely as test
  doubles**.
- The demo's `dataset_kind` / tenant-slug guard is a useful safety
  boundary but not an automatic PII detector. **Never paste real
  customer content into a fixture and call it synthetic.**
- CSV fields that look like spreadsheet formulas are escaped for
  Excel/Sheets safety. Each exported row carries a content digest, so
  changed source text, source revision, claim or answer invalidates
  previous reviews. Two reviewer sheets are kept separately; actual
  identity independence remains a procedural responsibility.
- The existing authoritative system for permissioned tenant evaluation
  and release approvals remains in `AIEvaluationService` and its
  schemas. This offline reviewer tool **does not mutate those database
  records, issue approvals or replace their production release gate**.
- To use actual real-model outputs and enterprise knowledge later,
  design authenticated, private, tenant-isolated reviewer storage,
  two-person role enforcement, source versioning and audit; obtain
  separate owner authorization before processing real customer data,
  using paid AI or integrating into production.

**Current release status: HOLD.** Keep all chatbot and worker PRs
unmerged, production GitHub worker unchanged, synthetic Render Cron
suspended, Phase 1Y ticket writes off, and repo visibility public.
