"""Before/after benchmark of the public-chat request path (Phase 1P.9).

Runs the real FastAPI app in-process (httpx ASGI transport) against a
DISPOSABLE PostgreSQL database, with the AI model replaced by a fixed-latency
fake so architecture effects are isolated from model speed and no paid call is
made. Compares:

* ``sync``  - the original contract: the HTTP request waits for the AI turn;
* ``async`` - Phase 1P.9: the request returns once the message is durable; the
  reply is fetched through the cursor endpoint at the server's suggested pace.

Reports, per tenant count: acceptance latency (p50/p95), time until the reply
is visible to the visitor (p50/p95), and database statements per accepted
message (each is a network round-trip to Neon in production).

    DATABASE_URL=postgresql+asyncpg://...@127.0.0.1:55432/cxops_disposable_test \\
      .venv/bin/python -m scripts.benchmark_public_chat --model-latency 3 --tenants 1 5 20

Refuses to run unless the database name ends in ``_disposable_test``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import statistics
import sys
import time
import uuid
from typing import Any


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered) + 0.5) - 1))
    return round(ordered[index], 1)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import delete, event, select

    from app.core.database import AsyncSessionLocal, engine
    from app.main import app
    from app.models.base import Base
    from app.models.organization import Organization
    from app.models.public_chat import PublicChatConfiguration
    from app.schemas.agent import AgentDecision
    from app.services.agent_workflow_service import agent_workflow_service
    from app.services.knowledge_search_service import KnowledgeSearchService
    from app.services.public_chat_service import hash_digest
    from app.services.public_chat_turn_dispatcher import turn_dispatcher

    decision = AgentDecision(
        action="no_action",
        reason="benchmark",
        requires_human_approval=False,
        response_draft="",
    )

    class FakeModel:
        async def ainvoke(self, _messages: list) -> dict:
            await asyncio.sleep(args.model_latency)
            return {"raw": None, "parsed": decision, "parsing_error": None}

    async def _no_search(*_a: Any, **_k: Any) -> list:
        return []

    agent_workflow_service.decision_llm = FakeModel()  # type: ignore[assignment]
    KnowledgeSearchService.search = _no_search  # type: ignore[method-assign]

    statements = {"n": 0}

    def _count(*_a: Any) -> None:
        statements["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    origin = "https://bench.example.test"
    prefix = f"bench-{uuid.uuid4().hex[:6]}-"
    results: dict[str, Any] = {"model_latency_s": args.model_latency, "runs": []}
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://bench")

    async def make_tenant() -> tuple[int, str]:
        async with AsyncSessionLocal() as db:
            org = Organization(name=f"{prefix}{uuid.uuid4().hex[:8]}")
            db.add(org)
            await db.flush()
            key = f"pk_live_{secrets.token_hex(20)}"
            db.add(
                PublicChatConfiguration(
                    organization_id=org.id,
                    public_widget_key_hash=hash_digest(key),
                    display_name="Bench",
                    welcome_message="Hi",
                    allowed_origins=[origin],
                    enabled=True,
                    max_message_length=4000,
                    max_messages_per_minute=60,
                    session_ttl_hours=24,
                )
            )
            await db.commit()
            org_id = org.id
        response = await client.post(
            "/public/chat/sessions",
            json={"public_widget_key": key},
            headers={"X-Embedding-Origin": origin},
        )
        response.raise_for_status()
        return org_id, response.json()["session"]["token"]

    try:
        for tenant_count in args.tenants:
            for mode in args.modes:
                tenants = [await make_tenant() for _ in range(tenant_count)]
                accept_ms: list[float] = []
                visible_ms: list[float] = []
                statements["n"] = 0

                failures: list[str] = []

                async def visitor(
                    token: str,
                    mode: str,
                    accept_ms: list[float],
                    visible_ms: list[float],
                    failures: list[str],
                ) -> None:
                    try:
                        await _visit(token, mode, accept_ms, visible_ms)
                    except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                        failures.append(type(exc).__name__)

                async def _visit(
                    token: str, mode: str, accept_ms: list[float], visible_ms: list[float]
                ) -> None:
                    headers = {"Authorization": f"Bearer {token}", "X-Embedding-Origin": origin}
                    started = time.perf_counter()
                    response = await client.post(
                        "/public/chat/messages",
                        json={
                            "client_message_id": uuid.uuid4().hex,
                            "text": "Where is my order?",
                            "delivery": mode,
                        },
                        headers=headers,
                    )
                    response.raise_for_status()
                    accepted = time.perf_counter()
                    accept_ms.append((accepted - started) * 1000)
                    if response.json().get("reply"):
                        visible_ms.append((accepted - started) * 1000)
                        return
                    cursor = 0
                    while True:
                        update = await client.get(
                            f"/public/chat/sessions/updates?after_id={cursor}", headers=headers
                        )
                        body = update.json()
                        cursor = body["cursor"]
                        if any(m["direction"] == "outbound" for m in body["messages"]):
                            visible_ms.append((time.perf_counter() - started) * 1000)
                            return
                        await asyncio.sleep(body["poll_after_ms"] / 1000)

                wall = time.perf_counter()
                await asyncio.gather(
                    *(
                        visitor(token, mode, accept_ms, visible_ms, failures)
                        for _org, token in tenants
                    )
                )
                await turn_dispatcher.drain(timeout=600)
                results["runs"].append(
                    {
                        "mode": mode,
                        "tenants": tenant_count,
                        "accept_ms_p50": _percentile(accept_ms, 50),
                        "accept_ms_p95": _percentile(accept_ms, 95),
                        "reply_visible_ms_p50": _percentile(visible_ms, 50),
                        "reply_visible_ms_p95": _percentile(visible_ms, 95),
                        "wall_s": round(time.perf_counter() - wall, 2),
                        "failed_requests": len(failures),
                        "failure_types": sorted(set(failures)),
                        "db_statements_per_message": round(statements["n"] / tenant_count, 1),
                    }
                )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
        await client.aclose()
        async with AsyncSessionLocal() as db:
            org_ids = list(
                (
                    await db.execute(
                        select(Organization.id).where(Organization.name.like(f"{prefix}%"))
                    )
                ).scalars()
            )
            for table in reversed(Base.metadata.sorted_tables):
                if "organization_id" in table.columns and org_ids:
                    await db.execute(delete(table).where(table.c.organization_id.in_(org_ids)))
            if org_ids:
                await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
            await db.commit()
        await engine.dispose()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model-latency", type=float, default=3.0)
    parser.add_argument("--tenants", type=int, nargs="+", default=[1, 5, 20])
    parser.add_argument("--modes", nargs="+", choices=["sync", "async"], default=["sync", "async"])
    args = parser.parse_args(argv)

    from sqlalchemy.engine import make_url

    from app.core.config import settings

    name = make_url(settings.database_url).database or ""
    if not name.endswith("_disposable_test"):
        print("refusing: benchmark only runs against a *_disposable_test database", file=sys.stderr)
        return 1
    results = asyncio.run(_run(args))
    print(json.dumps(results, indent=2))
    for mode in args.modes:
        values = [
            r["accept_ms_p50"]
            for r in results["runs"]
            if r["mode"] == mode and r["accept_ms_p50"] is not None
        ]
        if values:
            print(f"# {mode}: mean accept p50 {statistics.mean(values):.0f} ms", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
