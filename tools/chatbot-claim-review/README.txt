CXOPS AI — OFFLINE CLAIM REVIEW STUDIO
======================================
C7 source-only evaluation preview · NOT production certification

START HERE
----------
1. Double-click index.html to open the offline reviewer screen.
   All design/scripts are local. No account, internet, database or AI key.
   If your browser blocks file:// sibling scripts, follow the localhost-only
   fallback in docs/product/chatbot-c7-offline-reviewer-ui.md.

2. Choose Reviewer A or Reviewer B and enter YOUR reviewer identifier.
   Example names used in CI are fabricated test data, not reviewers.
   For an independent review, two people must use different private copies.

3. Choose the included C6-SYNTHETIC-BLANK.csv.
   The worksheet contains only 11 fictional customer questions / 12 claims.
   It contains no human labels and NO real customers.

4. For every claim, compare the stated answer with the cited source.
   Choose Supported / Contradicted / Unsupported / Unclear.
   For the first two, quote at least 8 exact characters from the source.
   For the last two, provide a rationale of at least 10 characters.

5. Click "Download review CSV" and keep your private copy.
   A draft can be reopened under the same reviewer assignment.
   Do not upload finished files to public GitHub.

NEXT — PRIVATE TWO-REVIEWER COMBINE AND SCORE
--------------------------------------------
The browser does NOT validate SHA-256 fingerprint authenticity or human identity.
The authoritative C6 Python scorer in the repo does:

python -m scripts.chatbot_claim_review combine \
  --reviewer-one reviewer-A-completed.csv \
  --reviewer-two reviewer-B-completed.csv \
  --out combined-private.csv

python -m scripts.chatbot_claim_review score \
  --sheet combined-private.csv \
  --report result.json --require-complete

Disputes require adjudication by a distinct third reviewer on the combined
file before scoring can pass strict mode. Even then, the report ALWAYS says
production_certified=false and independent_human_identity_verified=false.

PRIVACY / LIMITATIONS
---------------------
- No API calls, analytics, CDNs, cookies or automatic browser storage.
- Nothing persists unless you download the CSV file.
- Only the included fictional C6 dataset is supported by this demo.
- NEVER load actual customer messages or personal data into this prototype.
- This UI is not real-model accuracy proof, an actual two-human review,
  or approval to deploy an automatically responding chatbot.

Source and full constraints:
docs/product/chatbot-c7-offline-reviewer-ui.md (repository file)
https://github.com/zrasooli94/cxops-ai/pull/57
