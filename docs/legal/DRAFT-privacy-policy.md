# DRAFT — CXOps AI Privacy Policy

> **Status: DRAFT for owner and legal review. Not published. Not legal advice.**
> Items in `[[OWNER/LEGAL: …]]` are facts the business has not provided and
> must not be guessed. Statements describing system behaviour are accurate for
> the Phase 1Q-R codebase and must be re-checked before publication.

Effective date: `[[OWNER/LEGAL: date]]`
Controller: `[[OWNER/LEGAL: legal entity name, registered address, company number]]`
Contact for privacy requests: `[[OWNER/LEGAL: privacy contact email]]`
Applicable law / jurisdictions served: `[[OWNER/LEGAL: e.g. Australia (Privacy Act 1988 / APPs), EU/UK GDPR if applicable]]`

## 1. Who this policy covers

* **Prospects** who submit an early-access / demo request on our website.
* **Customer users** (business owners and their staff) who sign in to CXOps AI.
* **Website visitors of our customers** who chat with a customer's assistant.
  For this data the customer business is `[[OWNER/LEGAL: controller — confirm
  that CXOps acts as processor for customer chat data and reference the DPA]]`.

## 2. What we collect and why

| Who | Data | Purpose | Basis `[[LEGAL: confirm]]` |
|---|---|---|---|
| Prospect | Business name, your name, work email, website (optional), business type, optional message, the consent wording version and time | Review the request and contact you about it | Consent |
| Prospect | Submission count for repeated requests | Prevent duplicate and abusive submissions | Legitimate interests |
| Customer user | Sign-in identity from our identity provider (Nhost), email address, organization membership and role | Provide and secure the service | Contract |
| Customer user | Configuration changes (who/when/which fields, without values) | Security and audit | Legitimate interests |
| Visitor of a customer site | Chat messages, session timing, handoff status | Provide the chat service on behalf of the customer | `[[LEGAL]]` |
| All | Operational logs containing identifiers, timings and outcomes — **not** message text, emails, tokens or keys | Reliability and security | Legitimate interests |

We do **not** store visitor IP addresses with conversations, and we do not use
lead data for marketing lists.

## 3. AI processing

Replies are generated with OpenAI models. The visitor's message and relevant
excerpts of the customer's knowledge base are sent to OpenAI to produce a reply.
`[[OWNER/LEGAL: OpenAI data-usage terms for the account (e.g. API data not used
for training), data region, and whether a DPA is in place]]`.

## 4. Service providers (sub-processors)

| Provider | Purpose | Location `[[OWNER: confirm]]` |
|---|---|---|
| Vercel | Website and application hosting | `[[ ]]` |
| Render | API hosting | `[[ ]]` |
| Neon | Database (PostgreSQL) | `[[ ]]` |
| Nhost | User sign-in | `[[ ]]` |
| OpenAI | AI replies and knowledge embeddings | `[[ ]]` |

No email-delivery, CRM, analytics, advertising or payment provider is used at
the time of this draft.

## 5. Retention (proposed — requires approval)

| Data | Proposed retention | Mechanism |
|---|---|---|
| Declined early-access requests | 90 days after the last update | `scripts/purge_expired_leads.py` |
| Approved requests | 365 days after the last update (the customer workspace is unaffected) | same |
| Unanswered open requests | 180 days after the last submission | same |
| Expired / revoked team invitations | `[[OWNER: proposal: 90 days]]` | not yet automated |
| Customer workspace data | For the life of the contract, then `[[OWNER/LEGAL]]` | manual on termination |
| Operational logs | `[[OWNER: hosting provider log retention]]` | provider setting |

## 6. Your rights

You can ask for a copy of, correction of, or deletion of your data by writing
to `[[privacy contact]]`. For early-access requests an authorised CXOps
operator can export and erase the record in the operator console; erasure is
permanent. `[[LEGAL: response time commitment, complaint authority]]`.

## 7. Security

Access to customer data is restricted by organization membership and role;
platform operators are granted individually; widget keys and invitation tokens
are stored only as one-way hashes. We do not currently hold third-party
security certifications.

## 8. Changes

`[[LEGAL: notification approach]]`
