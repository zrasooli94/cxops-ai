# CXOps AI: private GitHub transition — preparation only

**Status: PREPARED ON DRAFT PHASE 1Y BRANCH; VISIBILITY NOT CHANGED.**
This is not production-release approval. Phase 1Y ticket writes remain OFF,
PR #38 / PR #36 remain draft and unmerged, and no deployed service changes.

## Verified starting topology

- Repository: personal account \`zrasooli94/cxops-ai\`; public; default \`master\`.
- Phase 1Y: draft PR #38 stacked on draft Phase 1W PR #36. This private
  readiness workflow change exists **only on the Phase 1Y feature branch**
  until separately reviewed and integrated. It is **not** on \`master\`.
- Vercel \`cxops-ai\`: linked to the GitHub personal repository in Hobby team
  \`zrasooli95\`, owned by Vercel account \`zrasooli94\`.
  Private personal-repo deployments should be eligible for the owner; verify
  Git author and deploy after visibility change, no automatic guarantee.
- Render: production and separate Free Phase 1W/1Y staging services use the
  same GitHub repository. Phase 1Y autoDeploy OFF. After privatization,
  Render's GitHub App needs explicit private-repo access, then verify
  source retrieval and service health **without redeploying production**.
- The current \`.github/workflows/production-worker.yml\` belongs to
  \`master\`, runs on cron **every five minutes**, and uses production
  credentials via GitHub Actions Secrets. Do not change or disable it merely
  to achieve private visibility.
- Volunteer reviewer Issue #39 becomes inaccessible to community outsiders
  when private. An identifiable independent human security review still
  requires invited access or a properly redacted review packet.

## SECURITY: a non-licensed private-repo replacement

GitHub CodeQL / CodeQL CLI cannot be used on GitHub Free private source without
GitHub Code Security licensing. GitHub's CodeQL workflow is kept active for
public code and intentionally SKIPPED on private code; it must not be used
there without a permitted license.

The **Private-Compatible SAST (Semgrep CE)** CI job runs on both public and
private code with the open-source Community Edition:
- Pinned Semgrep version, \`p/security-audit\` community ruleset downloaded
  without account login. \`semgrep scan\`, not \`semgrep ci\`/AppSec Platform.
- \`--metrics=off\`; no code, findings or tokens uploaded to Semgrep.
- Blocking on all ERROR findings, any WARNING affecting security-sensitive
  shared/staging code, scanner errors, missing scan coverage, and bad JSON.
- Only rule IDs/severity/paths reach GitHub job summaries. Raw source-bearing
  scan JSON stays in the ephemeral runner and is not uploaded as an artifact.
- CI Required Checks depends on Semgrep **success**, along with full tests,
  isolated security suites, Docker, API contract, lint/typecheck and Gitleaks.
- Gitleaks GitHub Action updated to v3 / Node24; personal repositories do not
  need an organization license. Do not transfer repo to an org silently.
- This is **not equivalent to CodeQL**: manual security review, dependency
  alerts and negative authorization testing remain necessary.

## CRITICAL: private Actions minutes and live production worker

GitHub Free includes **2,000 hosted Actions minutes/month** for private repos
(with limited artifact/cache storage). The existing worker's five-minute
schedule means up to about **8,640 workflow invocations in a 30-day month**,
before any CI, tests, previews or optional workflows. Even brief runs are
material to the quota. If minutes are exhausted, CI and production worker
may stop; if billing is enabled, unwanted charges may accrue.

**HARD PRE-SWITCH GATE:** The owner must explicitly approve and verify a
replacement worker execution model or acceptable cadence with a tested
fallback and an Action-minute budget. Do not auto-disable, reschedule,
move, or redeploy the production worker under this preparatory milestone.
Recommended: evaluate a reliable, appropriately funded dedicated worker for
commercial traffic rather than relying on indefinite free cron invocations.

Also check private GitHub Free plan's branch protection limits; do not assume
server-enforced required checks. Existing CI jobs and PR reviews remain useful
but are not a substitute for access controls.

## Suggested safe order, subject to owner approval

1. Confirm this draft source-only change passes both the **new Semgrep CE job**
   and existing full CI/CodeQL while repository is public.
2. Investigate and triage all Semgrep findings without deleting tests,
   bypassing the scanner, or weakening authentication/tenant protections.
3. Review GitHub repository and account Actions usage, payment/spending caps,
   Secrets/variables and worker schedule on the **default branch**. No need to
   retrieve or expose values; inspect key names/presence only.
4. Choose and test the alternative production worker execution/cost model.
   This is a separate operational approval; preserve worker continuity.
5. Ensure private-compatible CI is safely available on the intended
   protected/default branches. This PR alone does not alter \`master\`.
6. Audit access: minimal GitHub collaborators, Vercel owner/commit authors,
   Render private GitHub App installation, connected ChatGPT/GitHub tools.
7. Reconcile open public issues, code disclosures, external clones/forks,
   workflows, licenses, URLs and reviewers. Making a repo private cannot undo
   earlier public source copies.
8. Owner explicitly changes GitHub Settings -> General -> Danger Zone ->
   Change repository visibility -> Private **after all gates**.
9. Verify: Vercel Git integration, Preview SSO/private Git authors, Render
   private source connectivity, CI/Gitleaks/Semgrep on private source,
   CodeQL private skip, production worker continues and Actions usage stays
   within approved budget. Confirm production alias/deployment identity and
   Phase 1Y fail-closed ticket-write flags remain unchanged.
10. On failure, stop downstream deploys and restore prior known-good config
    or repository visibility only with owner approval. Do not automatically
    republish formerly private source.

## Ownership and release isolation

This task authorizes no production workers, credentials, databases, prices,
accounts, aliases, cron schedules, PR merges or visibility changes. It does
not constitute formal independent human Phase 1Y security signoff.

## Initial Semgrep CE grammar limitation

The first scan covered 457 tracked runtime files and found no rule hits.
Semgrep's strict parser flagged four partial TSX parses outside all security
paths (dashboard, knowledge, observability, transformation). The parser gate
permits only these exact known diagnostic file paths; any new parse error,
security finding, or missing required scan coverage still blocks CI. Existing
TypeScript/ESLint/Next tests cover those files. Semgrep cannot prove safety
of unparsed sections; retain independent human review and recheck on upgrade.

## Private-minute optimization (Phase 1Y draft only)

The prior Phase 1Y feature branch ran full CI and CodeQL twice per commit:
one `push` plus one `pull_request` generated-merge test. The draft branch
now relies on the **PR merge-result** workflow, which re-runs when its stacked
base or head changes. Master/main push CI is unchanged. Optional agent/RAG
provider evaluation jobs are skipped entirely on PRs and private repositories
(no empty database service minutes and no private provider usage). All
mandatory security/contract/backend/frontend, Docker, SAST and secret checks
still run on PR merge refs. Check required aggregates before any merger.

## Optional evaluation cost policy

On pull requests and private repositories, the optional provider-evaluation
jobs never start and never receive provider secrets. The existing mandatory
`CI Required Checks` job prints explicit RAG and Agent skip explanations,
so the previous no-silent-skip safety requirement remains enforced without
initializing two separate PostgreSQL runners. On permitted public push
checks, evaluations still require their exact two dedicated secrets and a
pinned organization ID. The regression tests validate both behaviors.
