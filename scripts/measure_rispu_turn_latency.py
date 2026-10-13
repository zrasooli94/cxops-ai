"""Reproducible local latency anatomy of one grounded RISPU chat turn (1R).

Runs the REAL application in-process (ASGI transport) against a DISPOSABLE
database with the approved RISPU knowledge loaded, and replaces only the three
provider boundaries with fixed-latency fakes (no network, no cost). It reports,
for the first (cold) and later (warm) messages of a fresh process:

* wall time per HTTP call;
* application-side time = wall time minus injected provider/DB-latency time;
* number and total time of embedding, decision-model and answer-model calls;
* number of database statements and (optionally) a modelled per-statement
  round trip, since each statement is a network hop to Neon in production;
* process import and application start-up time.

This isolates what the *application* contributes. It cannot reproduce Render
spin-up, Neon compute wake-up or OpenAI latency; those are injected as
parameters so their effect on the total is explicit, never assumed.

    DATABASE_URL=postgresql+asyncpg://...@127.0.0.1:55434/cxops_disposable_test \\
      .venv/bin/python -m scripts.measure_rispu_turn_latency --db-rtt-ms 0 --json

Refuses to run unless the database name ends in ``_disposable_test``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
import uuid
from typing import Any


def _guard() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url.split("?")[0].endswith("_disposable_test"):
        raise SystemExit("refusing to run: DATABASE_URL must name a *_disposable_test database")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    t_import = time.perf_counter()
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import delete, event

    from app.core.database import AsyncSessionLocal, engine
    from app.main import app, lifespan
    from app.models.base import Base
    from app.models.organization import Organization
    from app.models.public_chat import PublicChatConfiguration
    from app.services.agent_workflow_service import agent_workflow_service
    from app.services.embedding_service import embedding_service
    from app.services.knowledge_ingestion_service import KnowledgeIngestionService
    from app.services.public_chat_service import hash_digest
    from app.services.public_chat_turn_dispatcher import turn_dispatcher
    from app.services.rag_service import rag_service
    from app.tenant_onboarding import load_manifest_file
    from scripts.rispu_certification import (
        RISPU_MANIFEST,
        FaithfulFakeChat,
        LexicalEmbedder,
        PolicyAwareFakeDecision,
    )

    import_s = time.perf_counter() - t_import

    stats: dict[str, Any] = {
        "embed": {"n": 0, "s": 0.0},
        "decision": {"n": 0, "s": 0.0},
        "answer": {"n": 0, "s": 0.0},
        "db": {"n": 0, "s": 0.0},
        "workflow_build_s": 0.0,
    }
    rtt = args.db_rtt_ms / 1000.0

    class TimedEmbedClient(LexicalEmbedder):
        """Provider-level fake: the service's own cache stays in the path."""

        async def aembed_query(self, text: str) -> list[float]:
            await asyncio.sleep(args.embed_ms / 1000)
            stats["embed"]["n"] += 1
            stats["embed"]["s"] += args.embed_ms / 1000
            return self.vector(text)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [self.vector(t) for t in texts]

    class TimedChat(FaithfulFakeChat):
        async def ainvoke(self, messages: list[Any]) -> Any:
            await asyncio.sleep(args.answer_ms / 1000)
            stats["answer"]["n"] += 1
            stats["answer"]["s"] += args.answer_ms / 1000
            return await super().ainvoke(messages)

    class TimedDecision(PolicyAwareFakeDecision):
        async def ainvoke(self, messages: list[Any]) -> dict:
            await asyncio.sleep(args.decision_ms / 1000)
            stats["decision"]["n"] += 1
            stats["decision"]["s"] += args.decision_ms / 1000
            return await super().ainvoke(messages)

    embedding_service.client = TimedEmbedClient()  # type: ignore[assignment]
    rag_service.llm = TimedChat()  # type: ignore[assignment]
    agent_workflow_service.decision_llm = TimedDecision()  # type: ignore[assignment]
    from app.core.config import settings

    settings.rag_min_similarity = 0.12

    original_build = agent_workflow_service._build_workflow

    def timed_build(db: Any) -> Any:
        started = time.perf_counter()
        try:
            return original_build(db)
        finally:
            stats["workflow_build_s"] += time.perf_counter() - started

    agent_workflow_service._build_workflow = timed_build  # type: ignore[method-assign]

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(*_a: Any) -> None:
        stats["db"]["n"] += 1
        stats["db"]["s"] += rtt
        if rtt:
            time.sleep(rtt)  # modelled network round trip (blocking is fine here)

    manifest = load_manifest_file(RISPU_MANIFEST)
    assert manifest.public_chat is not None
    key = f"pk_live_{secrets.token_hex(20)}"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"rispu-latency-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.commit()
        db.add(
            PublicChatConfiguration(
                organization_id=org.id,
                public_widget_key_hash=hash_digest(key),
                display_name="RISPU Support",
                welcome_message="Hi",
                allowed_origins=list(manifest.public_chat.allowed_origins),
                enabled=True,
                max_message_length=2000,
                max_messages_per_minute=60,
                session_ttl_hours=24,
                grounded_auto_reply_enabled=True,
            )
        )
        await db.commit()
        for doc in manifest.knowledge:
            await KnowledgeIngestionService.ingest(
                db=db,
                organization_id=org.id,
                title=doc.title,
                content=doc.content,
                source=doc.source(),
                source_uri=doc.source_uri,
                metadata=doc.metadata(),
            )
    org_id = org.id

    def snapshot() -> dict[str, float]:
        return {
            "embed_n": stats["embed"]["n"],
            "decision_n": stats["decision"]["n"],
            "answer_n": stats["answer"]["n"],
            "db_n": stats["db"]["n"],
            "injected_s": stats["embed"]["s"]
            + stats["decision"]["s"]
            + stats["answer"]["s"]
            + stats["db"]["s"],
            "build_s": stats["workflow_build_s"],
        }

    out: dict[str, Any] = {"import_s": round(import_s, 3), "turns": []}
    t_start = time.perf_counter()
    async with lifespan(app):
        out["lifespan_start_s"] = round(time.perf_counter() - t_start, 3)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            origin = manifest.public_chat.allowed_origins[0]
            questions = [
                "Does RISPU develop mobile applications?",
                "Does RISPU offer SEO and Google Ads services?",
                "How can a business start a project enquiry?",
                "Start my project now.",
            ]
            t = time.perf_counter()
            resp = await client.post(
                "/public/chat/sessions",
                json={"public_widget_key": key},
                headers={"X-Embedding-Origin": origin},
            )
            token = resp.json()["session"]["token"]
            out["create_session_s"] = round(time.perf_counter() - t, 3)
            for question in questions:
                before = snapshot()
                t = time.perf_counter()
                r = await client.post(
                    "/public/chat/messages",
                    json={"client_message_id": uuid.uuid4().hex, "text": question},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Embedding-Origin": origin,
                    },
                )
                wall = time.perf_counter() - t
                after = snapshot()
                body = r.json()
                injected = after["injected_s"] - before["injected_s"]
                out["turns"].append(
                    {
                        "question_kind": "grounded" if not body.get("handoff") else "handoff",
                        "http_status": r.status_code,
                        "handoff": body.get("handoff"),
                        "wall_s": round(wall, 3),
                        "app_side_s": round(wall - injected, 3),
                        "embed_calls": after["embed_n"] - before["embed_n"],
                        "decision_calls": after["decision_n"] - before["decision_n"],
                        "answer_calls": after["answer_n"] - before["answer_n"],
                        "db_statements": after["db_n"] - before["db_n"],
                        "workflow_build_ms": round((after["build_s"] - before["build_s"]) * 1000, 2),
                    }
                )
                if body.get("handoff"):
                    break
    await turn_dispatcher.drain(timeout=5.0)

    async with AsyncSessionLocal() as db:
        for name in (
            "public_chat_sessions", "public_chat_configurations", "ticket_events",
            "agent_action_events", "knowledge_chunks", "ai_usage_reservations",
            "ai_request_logs", "integration_jobs", "agent_runs", "knowledge_documents",
            "conversation_messages", "conversations", "tickets", "customers",
            "customer_identities",
        ):
            table = Base.metadata.tables.get(name)
            if table is not None and "organization_id" in table.columns:
                await db.execute(delete(table).where(table.c.organization_id == org_id))
        await db.execute(delete(Organization).where(Organization.id == org_id))
        await db.commit()
    out["model"] = {
        "embed_ms": args.embed_ms,
        "decision_ms": args.decision_ms,
        "answer_ms": args.answer_ms,
        "db_rtt_ms_modelled": args.db_rtt_ms,
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embed-ms", type=float, default=250)
    parser.add_argument("--decision-ms", type=float, default=1500)
    parser.add_argument("--answer-ms", type=float, default=1200)
    parser.add_argument("--db-rtt-ms", type=float, default=0.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    _guard()
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=None if args.json else 2))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
