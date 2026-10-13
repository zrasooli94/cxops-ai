# CXOps AI — Business owner guide

For owners and administrators of a business workspace. Everything here
describes features that exist today; limitations are called out.

## 1. Joining your workspace

After your early-access request is approved, CXOps sends you an invitation
link (`…/invite#token=…`). Open it, sign in with the account for the invited
email address, and click **Accept invitation**. Links work once and expire
after 72 hours — ask for a new one if yours has expired.

> You need a CXOps sign-in for the invited address, and that address must be
> verified (you will have received a verification or sign-in email for it). If
> you are signed in with a different or unverified account, the invitation is
> refused but stays valid - sign in with the right account and try again. If
> you do not have a sign-in yet, your CXOps contact will set it up.

## 2. Setup (Your business → Setup)

Steps can be done in any order; progress is saved from your real settings, so
you can leave and return.

| Step | What to do | Required |
|---|---|---|
| Business information | Name, industry, website | Yes |
| Workspace and team | Invite colleagues (see §5) | No |
| Website domain | Every **exact** address that will show the chat, e.g. `https://example.com` **and** `https://www.example.com`. No wildcards, no paths. | Yes |
| Chatbot configuration | Assistant name, welcome message, colour theme (Teal, Midnight, Ocean, Forest) | Yes |
| Knowledge setup | Prepare hours, services, published prices, policies, top FAQs. Upload unlocks after approval. | Recommended |
| Installation | Copy the snippet (below) | Recommended |
| Preview | Static preview of the widget — no messages, no AI | — |
| Ready for approval | Submit; business details lock during review | Yes |

Until CXOps approves your workspace, the chatbot cannot answer visitors and no
AI usage is possible. After approval you decide when to switch it on.

## 3. Installing the widget

```html
<script src="https://<your CXOps address>/embed/loader.js" data-widget-key="pk_live_…" async></script>
```

1. Paste it just before `</body>` on every page (most website builders have a
   "custom code" or "footer scripts" setting).
2. Publish. The chat button appears bottom-right on approved domains once the
   chatbot is switched on.
3. The key is shown **once**, when the chatbot is created. If you lose it, an
   owner/administrator rotates it in **Chatbot settings** — the old key stops
   working immediately, so update your site straight away.

Troubleshooting: no button → check the chatbot is on, the page's address is
listed exactly (including `www.`), and your workspace is approved. The
Overview shows **Installation: detected** once a visitor has opened a chat.

## 4. Daily use (Your business → Overview)

* **Chatbot** — Live / Off, approved domains, last visitor chat.
* **Handoff queue** — conversations waiting for a person; open the queue to
  assign, reply and resolve.
* **Knowledge** — number of documents; keep them current.
* **Recent conversations** — latest visitor chats (content stays in the inbox).
* **AI usage this month** — AI conversations used vs allowance, estimated cost
  vs budget. Figures are estimates, not an invoice. When a limit is reached,
  new messages go straight to your team.
* **Team** — members and pending invitations.

Sections you are not permitted to see say "Not available for your role".

## 5. Team access (Your business → Team)

| Role | Can do |
|---|---|
| Owner | Everything, including ownership hand-over and team access |
| Administrator | Settings, knowledge, chatbot, most team members (not owners/admins) |
| Supervisor | Day-to-day support, AI action review, knowledge |
| Support agent | Answer conversations, work the handoff queue |
| Viewer | Read-only conversations and reports |

* Invitations: enter an email and role; **copy the link shown once** and send
  it yourself (automatic invitation emails are not available yet). Only the
  person whose verified sign-in matches that address can accept it; a
  forwarded link does not work for anyone else. Revoke a
  pending invitation at any time. Re-inviting the same address replaces the
  old link.
* You can only give roles below your own (owners can give any role). Nobody
  can change their own role. A workspace always keeps at least one owner.
* Removing someone takes effect on their next action. Anyone can leave a
  workspace (except the last owner).
* If you belong to several businesses, switch with the organization picker;
  each business's data is shown only while it is selected.

## 6. Data and limits

* Visitor messages and relevant knowledge excerpts are processed by OpenAI to
  generate replies. Do not put information in your knowledge that you would
  not want shared with visitors or processed by an AI provider.
* Each business's data is separate; every request is checked against your
  membership and role.
* Not available yet: self-service sign-up, online billing, invitation emails,
  guaranteed response times, third-party certifications.
