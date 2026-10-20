# CXOps AI — Zendesk webhook writeback marker recovery (source-only)

**Scope: DRAFT / RELEASE HOLD.** This patch is stacked on draft PR #48. It
changes the proposed application source but does not merge, deploy, execute
real Zendesk, configure a webhook, or change Render/Vercel production.

## Why the previous check was unsafe

The webhook event job calls
\`ConversationIngestionService.ingest_zendesk_ticket\`, then previously looked
for \`CXOps Event: <invocation_id>\` in that function's returned comments.
The ingestion path, by default, calls
\`ZendeskClient.get_ticket_comments\`, which returns **at most one page of 100
oldest-first comments**. Therefore, after a private provider write succeeds
but the worker dies before \`writeback_completed\` commits, a busy ticket can
hide the acknowledged comment outside that single page. Re-execution would
send the same private note again.

The ticket-event \`created_at\` timestamp is stored in PostgreSQL **before**
the first provider write; this provides an event-specific lower-bound for
comment scanning. It does **not** provide an external atomic transaction.

## Proposed bounded, fail-closed change

- Keep the existing classified conversation ingestion step, preserving its
  incoming comment data behavior. Do **not** use its truncated snapshot to
  prove writeback-marker absence.
- Independently call the existing
  \`ZendeskClient.get_ticket_comments_page\` with the persisted organization
  ID and Zendesk ticket ID. Use the shared \`find_marker\` cursor walker in
  newest-first order. Never construct a provider URL from webhook payload.
- Require a comment with \`public is False\` and an **entire standalone line**
  equal to \`CXOps Event: <invocation_id>\`. A public quote, a substring
  embedded in prose, or an event-key prefix collision does not certify a
  completed private writeback. This visibility/exact-line requirement is
  opt-in: existing reply and agent scanner callers retain their defaults.
- Require global newest-to-oldest timestamp ordering **across all pages**, not
  merely within each page. Missing/malformed timestamps, unordered comments,
  invalid page data, absent/repeated cursor, or a 20-page budget limit are
  inconclusive. Provider errors never mean "absent".
- Stop at a proven boundary: \`marker_boundary(event.created_at)\`, which
  includes the existing 10-minute clock-skew allowance, or when the provider
  definitively ends the cursor pages. Continue scanning deeper when neither
  condition is reached. At most **20 pages × 100** comments, with an
  **aggregate 40-second timeout** on this marker lookup so that 20 slow
  20-second transport attempts cannot consume the entire 240-second one-shot
  worker budget.
- Raise \`WebhookWritebackUnverified\` for inconclusive pagination or
  deadline expiry. Do **not** issue a Zendesk ticket write and do **not**
  mark \`writeback_completed\`. Provider transport errors similarly propagate
  to worker retry/failure handling without authorizing a write.
- Only after a conclusive no-marker result is a private note written;
  the existing event-completion behavior remains unchanged.

The extra bounded provider lookup increases request cost relative to the old
single-page marker check. The scanner protects against missing *visible*
prior notes and many malformed pagination responses; it is **not a general
exactly-once guarantee**.

## Disposable test evidence / required acceptance

A dedicated \`tests/disposable/webhook_writeback_cases.py\` uses the **real**
\`ZendeskWebhookService.process\` and shared marker scanner with inert
ticket/automation/repository/provider fakes. It is explicitly guarded to
run only under the exact disposable GitHub Actions local PostgreSQL
environment, and its HTTP client is patched to reject real requests.

The fake returns correct newest-first 100-comment cursor pages and, for
conversation ingestion, a deliberately truncated oldest-first snapshot. Test:

1. First write and already-completed duplicate early exit.
2. **Provider accepted → local acknowledgment lost** (injected failure);
   300 old notes + 250 newer comments leave the marker outside the old
   ingestion snapshot but the new cursor scan recovers it at page 3.
   No second external fake note; completion recorded exactly once.
3. Marker depths 1, 100, 101, 250, and 1,999 without a repeat write.
4. Conclusive no-marker when the event boundary is reached.
5. Missing/repeated cursor, malformed page, and an ordering violation
   crossing a page boundary. Each blocks write and event completion.
6. Zendesk HTTP 429 and timeout simulations. Each blocks writing.
7. Public marker quotations, event-key prefix collisions, and private
   prose with an embedded marker. These do NOT falsely count as an
   authenticated private writeback.
8. Twenty-page budget exhaustion and total scan timeout both fail closed.
9. Run prior Zendesk reply/agent marker-scan regression tests, existing 168
   disposable worker/PostgreSQL tests, SIGTERM probe, synthetic memory
   measurement, and pinned Ruff on the exact PR head.

**No test sends email, Telegram, Zendesk, OpenAI, notifications or live
customer data.** Existing production credentials are never injected.

## Residual, unresolved release risks

1. A Zendesk write may have been accepted **but not yet visible** to the
   comments endpoint when the scanner receives an otherwise conclusive
   response. Provider eventual consistency makes a "not found" result weaker
   than a documented provider idempotency key. The patch cannot guarantee
   exactly-once effects under all replica/propagation delays.
2. Two workers with overlapping stale claims can both scan before either
   provider write becomes visible. SQL fencing protects the job row, not
   external Zendesk state. Consider a separately reviewed advisory lease
   or provider-side operation reconciliation for writeback before shadow.
3. A private note supplied by another authorized actor with an identical
   full-line marker could still look like our writeback. Provider author
   identity / authenticated receipt checking is outside this patch.
4. The webhook automation and ingestion may themselves contain other
   retry-sensitive work. This patch limits its proof to the private
   writeback marker decision.
5. One 40-second lookup bound is only part of the full job. The worker's
   240-second bound checks between claims; slow providers may overrun and
   defer chat expiry. A real Render container and provider SDKs have not
   been measured here.
6. The staff email/Telegram acceptance-before-ack replay gap demonstrated
   in draft PR #48 is **unresolved** and independent of this Zendesk fix.
7. Qualified human security review, provider-level receipts/idempotency,
   Render 512-MiB cgroup evaluation and owner-authorized paid synthetic
   canary remain separate release gates.

**Disposition:** targeted source-only fix and synthetic tests only.
**Production release HOLD.** Keep the inert synthetic Render Cron service
\`crn-dbauc361egvs73c65pgg\` SUSPENDED, the existing GitHub production
worker unchanged, PRs unmerged, Phase 1Y ticket writes off, and repository
visibility public. Do not transfer provider secrets or activate real jobs.
