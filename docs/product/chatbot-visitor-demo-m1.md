# Chatbot Visitor Demo M1 — source-only, local development

**21 October 2026** · Branch: `codex/chatbot-visitor-demo-m1` · Parent: chatbot C5 PR #55 at `9ee36482a6fd3b08db79f232d2701160e0eda8e6`

## Product outcome

An interactive **developer-only showroom** for the existing CXOps AI visitor chat widget, driven by an in-browser, fictional support business ("Aster Cycle Studio"). The widget component itself is reused, including its real composer, first-contact starter prompts, theme palettes, customer-side message history, asynchronous polling UI, keyboard interactions, basic RTL layout and human-handoff states. The browser demo has an editable business setup panel, English/Persian sample knowledge, and a separate simulated staff inbox for claiming and replying to a conversation.

This is the first practical **customer journey** milestone, not a certification of real AI conversation quality or server-side onboarding. Answers are deterministic keyword/topic matches using six authored, fictional knowledge entries — **not OpenAI responses, semantic retrieval, document ingestion or generated conversation understanding**. Context is deliberately limited to the previous recognized topic ("What about that?"), and is not a substitute for real multi-turn LLM evaluation.

No real users, tenants, staff, tickets, messages, customer records, database calls, email, Zendesk, third-party APIs or AI provider requests are created by the mock transport. The installer snippet shown is explicitly a non-functional placeholder until a separate onboarding/publishing milestone.

## Safe local preview — no database, server worker or model key

From a local checkout:

```sh
git fetch origin
git switch --track origin/codex/chatbot-visitor-demo-m1
cd frontend
npm ci
npm run dev
```

Open **http://localhost:3000/chat/product-demo**. The visitor support bubble is at the bottom-right of the page; alternatively click "Open visitor chat". To test business settings, edit the name, greeting and palette, then use "Apply settings & start fresh chat". To test knowledge, switch to Knowledge and edit an entry; changes apply to the next matched question. The Staff inbox panel shows the fictional conversation and supports simulated claim/reply.

The page returns 404 in a production Next runtime, including production-mode hosting previews. No external preview hosting is needed or authorized. The demo uses an injected fetch-compatible browser transport; the live embed continues using normal same-origin BFF calls. Its session key differs from the real customer widget's session key. Demo browser data is per-tab `sessionStorage`, not a database; closing the tab clears it, and no real agent is contacted.

### Walkthrough script

1. Open the bubble, observe first-contact suggestions, and send "What products or services do you offer?" (fictional knowledge is returned, with a clear demo source label).
2. Send "What are your opening hours?" and then "What about that?" (limited topic follow-up in demo).
3. Ask `ساعات کاری شما چیست؟` and inspect the Persian response direction. Edit the Persian hours text to see the change on the next message.
4. Ask "Do you offer overseas shipping?" — the deterministic demo lacks that information and changes state to `human_requested`.
5. In Staff inbox click "Simulate staff claiming conversation", type a human reply and send it; the real visitor widget receives the mocked reply by polling. The simulated staff system is not connected to CXOps accounts.
6. Minimize and restore the chat, inspect the message history, then use End conversation and start a new fictional session.
7. Repeat on a phone-sized browser and use a keyboard. These interactions require a human browser check; no browser QA is claimed solely from static code.

## Offline checks to run on the branch

```sh
cd frontend
node --experimental-strip-types --test src/lib/public-chat/demo.test.ts
npm test
npm run lint
npx tsc --noEmit
npm run build
```

The new deterministic transport tests cover fictional English/Persian answers, limited topic continuity, empty/missing facts, mock handoff, owner-configured knowledge, bearer token/session boundary, ID-based retry deduplication, cursor history, claim/reply, and browser-session persistence. They do not establish real AI semantic faithfulness, browser accessibility, or production integration. Check the actual command results before calling this milestone verified.

## Preserved release restrictions

- Source-only branch starting from PR #55; C6–C11 reviewer/audit PRs #56–#61 are untouched.
- No merge into `master`, production, Render Cron, live agents, ticket writes, real hosting, secrets, billing or paid AI models.
- No new public API routes; the demo implements the existing public-chat HTTP contract in memory and refuses unrelated fetch paths.
- Do **not** enable real chatbot auto-replies on the basis of this synthetic demo. Existing backend citations, confidence/eligibility and tenant isolation remain authoritative.

## Next product milestone after human browser confirmation

Connect the *same* widget to a disposable, approved non-production tenant using its actual public BFF/FastAPI flow. Seed fictional FAQs and policies through real knowledge ingestion, validate threaded real-session persistence and real staff queues, then (only after explicit owner authorization for API key, model choice and cost cap) run a labeled multilingual real-model quality exercise. Continue business-owner onboarding and design polish after the genuine chat loop is demonstrated. Reviewer C12 and Render worker changes remain on hold.
