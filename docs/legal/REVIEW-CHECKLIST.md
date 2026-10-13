# Legal and first-customer review checklist (owner / counsel)

Status: **checklist only - nothing here is approved or published.** The two
drafts in this folder (`DRAFT-privacy-policy.md`, `DRAFT-terms-of-service.md`)
must not be linked from the website until every item below that applies is
signed off.

## A. Facts the owner must supply (no defaults are assumed)

- [ ] Legal entity name, registered address, company/ABN number
- [ ] Privacy contact email and postal address
- [ ] Jurisdictions served; applicable privacy law(s)
- [ ] Hosting/data regions for Vercel, Render, Neon, Nhost, OpenAI (from the
      actual account settings)
- [ ] OpenAI data-usage terms for the account (API data not used for training?
      DPA in place?) and Zero-Data-Retention status
- [ ] Whether CXOps is processor (customer chat data) and controller (leads,
      staff accounts); need for a Data Processing Agreement with customers
- [ ] Log retention at each hosting provider

## B. Policy decisions

- [ ] Lead retention (proposed: declined 90 days, approved 365 days,
      unanswered 180 days - `scripts/purge_expired_leads.py`, dry-run default)
- [ ] Expired/revoked invitation retention (proposed: 90 days; not automated)
- [ ] Customer data on termination: export window and deletion deadline
- [ ] Response time for access/erasure requests and complaint authority
- [ ] Liability cap, warranties, governing law, termination clauses (terms)
- [ ] Acceptable-use wording (chat widget must not collect sensitive data)
- [ ] Service levels: confirm "no SLA during early access"

## C. Operational safeguards already built (verify before publication)

- [ ] Lead export and erasure are platform-operator-only (`/platform-admin/leads/{id}/export`,
      `DELETE /platform-admin/leads/{id}`), logged by id, and the console asks
      for confirmation before erasure; erasure never touches a provisioned
      workspace.
- [ ] Consent wording shown on the form is served by the API with its
      version (`2026-10-early-access-v1`); operator-recorded leads are marked
      `operator-entered` (owner to document how verbal/email consent is kept).
- [ ] Logs contain identifiers and outcomes, not emails, names, message text,
      tokens or keys.
- [ ] Visitor IP addresses are not stored with conversations.

## D. Separate approvals that remain closed

- [ ] Pricing and plans (website says "pricing on request")
- [ ] Customer agreement template (order form + terms)
- [ ] Payment collection / provider (none integrated; invoicing manual)
- [ ] Public lead capture (`PUBLIC_LEAD_CAPTURE_ENABLED`)
- [ ] Publishing the privacy policy and terms (and linking them in the footer
      and on the early-access form)

## E. First paying customer - go/no-go

- [ ] Release deployed per `docs/runbooks/phase1q-release-runbook.md`; RISPU checks pass
- [ ] OpenAI hard limit ≤ platform AI ceiling
- [ ] Platform operator granted
- [ ] Identity verification active (`nhost_userinfo`) and tested with a test invitee
- [ ] Customer's Nhost account exists with a verified email
- [ ] Privacy policy and terms published; customer agreement signed
- [ ] Price agreed and invoiced outside the product
- [ ] Customer's exact domains added to `CXOPS_WIDGET_FRAME_ANCESTORS` and redeployed
- [ ] Conservative allowance set (e.g. 300 conversations / $3) and reviewed weekly
