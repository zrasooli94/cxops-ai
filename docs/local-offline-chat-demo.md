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
