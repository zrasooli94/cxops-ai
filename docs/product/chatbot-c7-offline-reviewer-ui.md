# CXOps AI — C7 Offline Claim Review Studio

**21 October 2026 | DRAFT / SOURCE ONLY / PRODUCTION RELEASE HOLD**

This milestone turns the C6 review spreadsheet into a **usable local browser
experience**, separate from the customer-facing widget. The current C7
workspace is a standalone HTML/CSS/JavaScript application at
`tools/chatbot-claim-review/index.html`, not a deployed route and
not a paid SaaS. It does not collect real customer data.

## Launching and reviewing

1. Use the **existing C6 Python scorer** to create an empty sheet from the
   versioned synthetic fixture (in the repo root):

   ```bash
   python -m scripts.chatbot_claim_review prepare --out ./c6-synthetic-review.csv
   ```

   Never overwrite an existing annotated sheet. The file contains 11 fictional
   cases and 12 claims. It has **no reviewer labels**.

2. Double-click `tools/chatbot-claim-review/index.html` to open the
   **offline interface**. The page loads only the adjacent `review.css`,
   `review-core.js` and `app.js`. If a particular browser blocks local
   sibling scripts through file URL policy, host this same folder on
   `127.0.0.1` **only**, without a remote proxy, e.g. from that folder:

   ```bash
   python3 -m http.server 8765 --bind 127.0.0.1
   ```

   Then open `http://127.0.0.1:8765/` locally. This is a local-only fallback,
   not a production deployment. The app itself never makes a network request.

3. Reviewer A chooses **Reviewer A**, enters their own reviewer identifier,
   and selects a **private copy** of the original blank CSV. The review role
   and identity lock after import to prevent accidental cross-role edits.
   Reviewer B independently uses another copy, the **Reviewer B** role and
   their own reviewer identifier. Do not share or open the other reviewer’s
   partially annotated file.
4. Read each claim, complete answer, and **cited source passage**.
   Choose `supported`, `contradicted`, `unsupported` or `unclear`.
   For the first two, highlight an **exact quotation of 8+ characters**
   within the cited source and press **Use selected text as quote**, or type
   an exact quote manually. For the latter two, provide a written rationale
   of 10+ characters. The UI never assigns labels automatically.
5. Use **Download review CSV** to save the work. When all claims meet the
   form requirements, the filename says `completed`; if not, it says
   `draft`. A draft can be re-imported under the same role/reviewer ID.
   **Completed means all local fields are filled, not that the review is
   correct, independent, or approved for production.**
6. Follow the existing C6 workflow to combine the two private, independently
   completed CSVs and have a distinct third reviewer adjudicate disputes:

   ```bash
   python -m scripts.chatbot_claim_review combine \
     --reviewer-one reviewer-A-completed.csv \
     --reviewer-two reviewer-B-completed.csv \
     --out combined-private.csv

   python -m scripts.chatbot_claim_review score \
     --sheet combined-private.csv \
     --report sanitized-result.json \
     --require-complete
   ```

   C6 Python verifies **the actual source SHA-256 fingerprints**, reviewer
   columns, evidence excerpts, distinct *claimed* identities and complete
   adjudication. Browser C7 checks only a plausible fingerprint shape;
   without a separately loaded canonical dataset it cannot verify the source
   hash. Never interpret a browser-accepted CSV as cryptographically
   authenticated.

## Privacy, isolation and data handling

- **No network:** no `fetch`, XMLHttpRequest, WebSocket, beacon, tracker,
  CDN, OAuth, API key, model call, font request or third-party connection.
  The HTML has an explicit `connect-src 'none'` CSP.
- **No persistent browser storage:** no LocalStorage, IndexedDB, cookie or
  background synchronization. Review data is kept in memory for this tab;
  closing a changed session triggers a save warning. Download the CSV.
- **No HTML injection:** cited text, claims and answers are rendered using
  `textContent`, never HTML or untrusted CSS. CSV quote parsing follows
  RFC-4180 conventions and supports multiline Persian/Unicode passages.
- **Formula/CSV safety:** spreadsheet-style formula-leading reviewer input
  and imported unprotected source fields are rejected; exports quote every
  cell. The underlying C6 Python dataset CSV already escapes any leading
  spreadsheet formula in protected source fields.
- **Single reviewer role per import:** rejects populated columns belonging
  to the other reviewer or adjudicator, rejects wrong reviewer ID, foreign
  dataset identifiers, malformed citation IDs, unexpected rows and
  invalid column counts. Every protected field is carried through unchanged
  into the exported CSV.
- **Bounded:** 12 MB import file, 500 claims, evidence length 1,000, rationale
  length 500, reviewer ID 80. Only the explicitly fictional
  `c6-fictional-faq-20261021` CSV is accepted by this first release.
- **Blind review is procedural:** distinct reviewer names are self-attested.
  The browser cannot verify that two humans worked independently or that a
  label is a sound interpretation of the cited material.
- **Never use real customer data** in this offline, public-repository pilot;
  changing the dataset name to a synthetic slug is not a PII detector.

## Verification plan

The C7 PR-only GitHub Actions lane must check:
- Actual Node syntax for the offline JS modules and tests.
- Pure CSV parser/serializer (quoted commas, embedded quotation marks,
  multi-line Persian passages, malformed/truncated input) and guarded author
  role/evidence validation.
- A Node DOM event harness exercising the actual browser app import,
  plain-text display, selection-to-quotation, navigation, progress and
  private draft download without a real backend.
- **Cross-language compatibility:** Python C6 prepares the actual blank
  worksheet, the C7 JS imports/exports two **fabricated CI-only** reviewer
  files, then C6 combines and scores those CSVs without database or provider
  access; the output **must remain NOT CERTIFIED**.
- C6's existing 56 offline tests plus pinned Ruff checks for unchanged C6
  scorer and test file.

This is **not** a real Safari/Android/VoiceOver/browser-usability certification.
Real iOS/Android/desktop pilot testing must follow through an authorized
local test tenant/fixture before deployment. No claim that the UI is an
authenticated production evidence reviewer or that it certifies real AI
model responses.

## Product next milestone

Design authenticated tenant-scoped review records and independent reviewer
permissions, then separately authorize an actual low-cost real-model
evaluation against safely prepared synthetic business knowledge. Add source
versioned per-claim tracing in the normal chatbot evaluation dashboard and
a human reviewer queue. No production auto-reply change until qualified
security/product approval of those gates.

**RELEASE HOLD.** No merges, Vercel/Render production updates, paid services,
secret transfer, Phase 1Y ticket writes, Render Cron activation, or repository
visibility changes are authorized by this C7 source-only milestone.
