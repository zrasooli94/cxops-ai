# Local real-backend chatbot demo

This development-only demo uses the production visitor-widget HTTP contract,
the real FastAPI persistence and handoff services, and a disposable PostgreSQL
database. Its responder is deterministic lexical retrieval, not an LLM. It
makes no embedding or chat-model request and copies only authored synthetic
knowledge into answers.

## Safety boundary

- Use a disposable PostgreSQL database on `localhost` or `127.0.0.1` only.
- `PUBLIC_CHAT_OFFLINE_PROVIDER_ENABLED=true` is rejected in production.
- The seed script refuses production and non-loopback database hosts.
- The tenant, business and knowledge are fictional. No RISPU configuration,
  customer data, production secret, external action or hosted service is used.
- Unsupported, transactional and low-confidence questions enter the existing
  human-handoff state. Once handed off, later visitor messages are persisted
  without another automated answer.

## Run

After installing the normal Python and frontend dependencies, initialize and
migrate a disposable local database. Then seed the synthetic tenant:

```bash
DATABASE_URL=postgresql+asyncpg://localhost:55432/cxops_local \
ENVIRONMENT=development \
python -m scripts.bootstrap_offline_chat_demo
```

Start the backend with the explicit development provider gate:

```bash
DATABASE_URL=postgresql+asyncpg://localhost:55432/cxops_local \
ENVIRONMENT=development \
PUBLIC_CHAT_OFFLINE_PROVIDER_ENABLED=true \
PUBLIC_CHAT_ASYNC_ENABLED=false \
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend from `frontend/`:

```bash
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000/chat/backend-demo`. This route is unavailable in
production builds. Conversation rows remain in the disposable database, so a
widget session restores its transcript after a page reload.

Suggested checks:

1. Ask `What are your workshop opening hours?` and confirm a cited answer.
2. Ask `What about that?` and confirm the same topic is recovered from the
   immediately preceding answered customer turn.
3. Ask `ساعات کاری تعمیرگاه چیست؟` and confirm Persian knowledge is included.
4. Ask `Can I book a snowboard repair?` and confirm human handoff.
5. Send another message after handoff and confirm it is stored with no
   automated reply.

## Local business-owner studio

The seeder also creates a synthetic owner membership. Start Next.js with the
same development-only HS256 secret as the API and the organization id printed
by the seeder:

```bash
cd frontend
BACKEND_API_URL=http://127.0.0.1:8000 \
AUTH_JWT_SECRET='<same development-only API secret>' \
LOCAL_DEMO_ORGANIZATION_ID='<seeded organization id>' \
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000/chat/owner-studio`. The studio saves assistant
identity, greeting, appearance, and the synthetic FAQ pack to the disposable
tenant. Its inbox reads persisted conversations and supports staff claim,
reply, release, and resolution. Language is a browser-local preview preference
until the production configuration contract gains a tenant language field.

The local identity bridge is rejected in production, accepts only loopback
hostnames, forwards only an explicit route allowlist, and keeps its five-minute
token server-side. Every forwarded request still passes normal backend
membership, tenant, and capability checks. The FAQ endpoints independently
return 404 unless the API is non-production, the offline provider is enabled,
and the database host is loopback.

Run the isolated browser interaction check while both services are running:

```bash
node scripts/headless_owner_studio_e2e.mjs
```

It uses a separate headless Chrome process, creates a fresh synthetic visitor
session, verifies save/reload for settings and FAQs, visitor polling and reload
continuity, staff claim/reply/release/resolve, and that a visitor message after
takeover does not receive an automated reply. It also verifies the owner studio
at a 390 px mobile viewport with Persian direction, rejects horizontal overflow,
and checks the tenant-derived conversation summary. It writes desktop and mobile
screenshots under `artifacts/`.

The inbox summary shows persisted conversation count, conversations awaiting a
reply, active handoffs, and resolved conversations from the same tenant-scoped
responses already used by the inbox. It is a current local snapshot, not a
historical reporting pipeline.

## Reproducible start and stop

Use two terminals after bootstrapping the disposable database. Set one random,
development-only `AUTH_JWT_SECRET` in both terminals; never reuse a production
secret. Start the API with the backend command above (plus that secret), then
start the frontend with `BACKEND_API_URL`, the same secret, and the seeded local
organization id. The owner studio is at
`http://127.0.0.1:3000/chat/owner-studio`; the visitor-only demo is at
`http://127.0.0.1:3000/chat/backend-demo`.

Stop the two foreground processes with `Ctrl-C` in their terminals. Stop the
disposable PostgreSQL instance using the same local database tool or container
command used to start it. Do not point these commands at a production or remote
database. To reset all synthetic state, recreate only the disposable
`cxops_local` database and rerun the bootstrap command.

Current local limitations: the language choice is browser-local; document
retrieval is lexical and text-only; scanned PDFs need OCR outside this demo;
analytics are point-in-time counts; the install host is a placeholder; and the
responder is deterministic rather than a genuine model. Paid-model evaluation
and credentials remain disabled pending explicit approval.

## Paid-model evaluation readiness

`scripts/plan_chat_model_evaluation.py` is deliberately plan-only: it never
creates a model client or sends a provider request. It expands bilingual
grounding, uncertainty, policy, and handoff scenarios, applies hard turn and
per-turn input/output token limits, and fails closed when the worst-case cost
at explicitly supplied rates exceeds the requested ceiling. For example:

```bash
python -m scripts.plan_chat_model_evaluation
```

The source-only baseline is `gpt-6-luna`, which the official model page lists
as a supported Chat Completions model for focused, high-volume work:
https://developers.openai.com/api/docs/models/gpt-6-luna. Standard token prices
were verified on 2026-10-21 as $0.10 input and $0.50 output per million tokens;
the plan conservatively uses the $0.125 30-minute cache-write input rate:
https://developers.openai.com/api/docs/pricing. The 50-turn plan caps input at
3,000 tokens and output at 500 tokens per turn. Its $0.0625 worst-case treats
the configured one retry as a second fully billed attempt for every turn (100
total attempts), rather than assuming failed attempts are free. The disconnected
`ModelEvaluationController` contract reserves a full attempt before its injected
mock transport, retains ambiguous-timeout reservations for manual reconciliation,
passes zero SDK retries, requires a model-matched token-counter contract, and rejects 12-hour cache
retention under the $0.10 ceiling because it leaves no headroom. The local
Responses contract adapter serializes retrieved business knowledge and bounded
conversation history, validates a strict structured response, and suppresses
generation for restored `human_requested`/`human_assigned` sessions. Its
transport is injectable and must declare itself provider-disconnected. This
contract does not prove factual entailment from a cited chunk, and a real
integration must atomically re-read handoff state immediately before dispatch;
the current restored-session test does not claim to close that race.

The installed `tiktoken` registry does not map `gpt-6-luna`, so exact full-input
token accounting deliberately fails closed. There is no real provider transport
or credential wiring, so the controller remains unusable for a real request and
`$0.0625` is a tested arithmetic bound at the supplied rates—not measured spend.
Before any real
run, the owner must still approve the provider, model, credential method, and
usage ceiling, and engineering must verify an exact tokenizer, current model and
pricing, account access, and the real transport. The normal production OpenAI
adapters (which this evaluation path does not invoke) apply `CHAT_MAX_OUTPUT_TOKENS`
(default 500), a 30-second timeout, one retry, and `reasoning_effort=none` for
the GPT-6 Chat Completions compatibility path.

The owner studio also accepts synthetic `.txt`, `.md`, `.markdown`, and
text-based `.pdf` files up to 512 KiB, with at most 20 local documents. Parsing
and lexical chunking happen locally; this path does not fetch URLs or create
embeddings. Uploaded documents remain tenant-scoped in the disposable database
and can be removed from the same screen. The setup screen shows a copyable
installation template containing a placeholder host; it does not publish or
activate a hosted widget.
