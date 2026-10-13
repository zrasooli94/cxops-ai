"""Public-chat performance certification on disposable infrastructure (1P.9R).

Drives the real FastAPI app in-process (httpx ASGI transport) with the
application's own production-shaped connection pool (5 + 10, pre-ping)
against a DISPOSABLE PostgreSQL, with every provider replaced by a fake of
fixed latency (no network call, no cost). Measures each stage separately:

* acknowledgement      - visitor POST until the response (``accept``)
* queue wait           - job created until claimed by a worker task
* AI decision          - the agent analysis (fake decision model inside)
* RAG retrieval        - knowledge search (fake embedding + vector search)
* database             - summed statement time and statement count per message
* reply visible        - visitor send until the reply is seen by polling at
                         the server's suggested pace
* staff visible        - handoff stored -> visible on the 15 s inbox refresh
                         (MODELLED: availability + uniform 0..15 s refresh)
* pool / memory        - peak pooled connections checked out, peak RSS

Workloads: 1/5/20/50 tenants sending at once (two messages each, the second
after the first reply), a noisy tenant, concurrent replies, a crash of every
in-flight turn, and a sweep of turn-concurrency limits.

Results describe THIS machine with fake providers. They are evidence for the
design (queueing, fairness, pool safety, failure rate), not production
latency: production adds network round trips (browser -> Vercel -> Render ->
Neon) and real model time.

    .venv/bin/python -m scripts.certify_public_chat_performance --out results.json

Refuses to run unless the database name ends in ``_disposable_test``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import random
import resource
import secrets
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

STAFF_REFRESH_SECONDS = 15.0
# Token assumptions for the cost estimate (gpt-4o-mini list prices from
# settings): decision prompt incl. context, and a grounded RAG answer.
DECISION_TOKENS = (2500, 350)
RAG_TOKENS = (1500, 250)


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered) + 0.5) - 1))
    return round(ordered[k], 1)


def summary(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": pct(values, 50),
        "p95": pct(values, 95),
        "max": round(max(values), 1) if values else None,
        "n": len(values),
    }


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS bytes.
    return round(rss / (1024 * 1024) if platform.system() == "Darwin" else rss / 1024, 1)


class Recorder:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.stage: dict[str, list[float]] = defaultdict(list)
        self.db_ms = 0.0
        self.db_statements = 0
        self.pool_out = 0
        self.pool_peak = 0
        self.failures: list[str] = []
        self.messages = 0


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import delete, event, select

    from app.core import database
    from app.core.config import settings
    from app.core.db_connection import AnalysisCapacity
    from app.main import app
    from app.models.base import Base
    from app.models.integration_job import IntegrationJob
    from app.models.organization import Organization
    from app.models.public_chat import PublicChatConfiguration
    from app.repositories.integration_job_repository import IntegrationJobRepository
    from app.schemas.agent import AgentDecision
    from app.services import agent_workflow_service as workflow_module
    from app.services import public_chat_turn_dispatcher as dispatcher_module
    from app.services.knowledge_search_service import KnowledgeSearchService
    from app.services.public_chat_service import hash_digest

    rec = Recorder()
    engine = database.engine
    settings.public_chat_async_enabled = True
    settings.platform_monthly_ai_budget_usd = 1_000_000.0

    # ---- fake providers -------------------------------------------------
    answer = AgentDecision(
        action="no_action", reason="bench", requires_human_approval=False, response_draft=""
    )
    escalate = AgentDecision(
        action="escalate",
        reason="bench handoff",
        requires_human_approval=True,
        response_draft="",
    )

    class FakeDecisionModel:
        async def ainvoke(self, messages: list) -> dict:
            text = " ".join(str(m.content) for m in messages)
            await asyncio.sleep(args.decision_seconds)
            return {
                "raw": None,
                "parsed": escalate if "[handoff]" in text else answer,
                "parsing_error": None,
            }

    async def fake_search(*_a: Any, **_k: Any) -> list:
        started = time.perf_counter()
        await asyncio.sleep(args.retrieval_seconds)
        rec.stage["rag_retrieval_ms"].append((time.perf_counter() - started) * 1000)
        return []

    workflow_module.agent_workflow_service.decision_llm = FakeDecisionModel()  # type: ignore[assignment]
    KnowledgeSearchService.search = fake_search  # type: ignore[method-assign,assignment]

    original_analyze = workflow_module.agent_workflow_service.analyze

    async def timed_analyze(*a: Any, **k: Any) -> dict:
        started = time.perf_counter()
        try:
            return await original_analyze(*a, **k)
        finally:
            rec.stage["ai_decision_ms"].append((time.perf_counter() - started) * 1000)

    workflow_module.agent_workflow_service.analyze = timed_analyze  # type: ignore[method-assign]

    original_claim = IntegrationJobRepository.claim_by_id

    async def timed_claim(*a: Any, **k: Any):
        job = await original_claim(*a, **k)
        if job is not None and job.created_at is not None:
            rec.stage["queue_wait_ms"].append(
                (datetime.now(UTC) - job.created_at).total_seconds() * 1000
            )
        return job

    IntegrationJobRepository.claim_by_id = staticmethod(timed_claim)  # type: ignore[method-assign]

    # ---- database instrumentation --------------------------------------
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, _cursor, _stmt, _params, context, _many):
        conn.info.setdefault("t0", []).append(time.perf_counter())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, _cursor, _stmt, _params, context, _many):
        started = conn.info["t0"].pop()
        rec.db_ms += (time.perf_counter() - started) * 1000
        rec.db_statements += 1

    @event.listens_for(sync_engine.pool, "checkout")
    def _checkout(*_a: Any) -> None:
        rec.pool_out += 1
        rec.pool_peak = max(rec.pool_peak, rec.pool_out)

    @event.listens_for(sync_engine.pool, "checkin")
    def _checkin(*_a: Any) -> None:
        rec.pool_out -= 1

    origin = "https://bench.example.test"
    prefix = f"cert-{uuid.uuid4().hex[:6]}-"
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://cert")
    results: dict[str, Any] = {
        "environment": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "cpu_limit_note": args.cpu_note,
            "decision_seconds": args.decision_seconds,
            "retrieval_seconds": args.retrieval_seconds,
            "pool": "QueuePool 5 + 10 overflow, pre_ping (application default)",
        },
        "workloads": [],
    }

    def use_dispatcher(concurrency: int) -> Any:
        settings.public_chat_turn_max_concurrency = concurrency
        fresh = dispatcher_module.TurnDispatcher(
            max_concurrency=concurrency,
            per_tenant=settings.public_chat_turn_max_concurrency_per_tenant,
        )
        dispatcher_module.turn_dispatcher = fresh
        import app.api.routes.public_chat as route_module

        route_module.turn_dispatcher = fresh  # type: ignore[attr-defined]
        workflow_module.analysis_capacity = AnalysisCapacity(
            settings.agent_analysis_max_concurrency
        )
        return fresh

    async def make_tenant(sessions: int) -> tuple[int, list[str]]:
        async with database.AsyncSessionLocal() as db:
            org = Organization(name=f"{prefix}{uuid.uuid4().hex[:8]}")
            db.add(org)
            await db.flush()
            key = f"pk_live_{secrets.token_hex(20)}"
            db.add(
                PublicChatConfiguration(
                    organization_id=org.id,
                    public_widget_key_hash=hash_digest(key),
                    display_name="Cert",
                    welcome_message="Hi",
                    allowed_origins=[origin],
                    enabled=True,
                    max_message_length=4000,
                    max_messages_per_minute=120,
                    session_ttl_hours=24,
                    async_turns_enabled=True,
                )
            )
            await db.commit()
            org_id = org.id
        tokens = []
        for _ in range(sessions):
            response = await client.post(
                "/public/chat/sessions",
                json={"public_widget_key": key},
                headers={"X-Embedding-Origin": origin},
            )
            response.raise_for_status()
            tokens.append(response.json()["session"]["token"])
        return org_id, tokens

    async def visitor(token: str, texts: list[str], label: str) -> None:
        headers = {"Authorization": f"Bearer {token}", "X-Embedding-Origin": origin}
        cursor = 0
        for text in texts:
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/public/chat/messages",
                    json={
                        "client_message_id": uuid.uuid4().hex,
                        "text": text,
                        "delivery": "async",
                    },
                    headers=headers,
                )
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - counted
                rec.failures.append(type(exc).__name__)
                continue
            rec.messages += 1
            accepted = time.perf_counter()
            rec.stage["ack_ms"].append((accepted - started) * 1000)
            body = response.json()
            if body.get("processing_state") != "accepted":
                rec.stage[f"{label}_reply_visible_ms"].append((accepted - started) * 1000)
                continue
            deadline = started + args.reply_timeout
            while time.perf_counter() < deadline:
                update_response = await client.get(
                    f"/public/chat/sessions/updates?after_id={cursor}", headers=headers
                )
                payload = update_response.json()
                cursor = payload["cursor"]
                if any(m["direction"] == "outbound" for m in payload["messages"]):
                    visible = (time.perf_counter() - started) * 1000
                    rec.stage[f"{label}_reply_visible_ms"].append(visible)
                    rec.stage["reply_visible_ms"].append(visible)
                    if payload["status"] == "human_requested":
                        rec.stage["staff_available_ms"].append(visible)
                    break
                await asyncio.sleep(payload["poll_after_ms"] / 1000)
            else:
                rec.failures.append("reply_timeout")

    def collect(name: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        staff = [
            v + random.uniform(0, STAFF_REFRESH_SECONDS * 1000)
            for v in rec.stage.get("staff_available_ms", [])
        ]
        total = rec.messages + len(rec.failures)
        row: dict[str, Any] = {
            "workload": name,
            "messages": rec.messages,
            "failures": len(rec.failures),
            "failure_types": sorted(set(rec.failures)),
            "failure_rate": round(len(rec.failures) / total, 4) if total else 0.0,
            "ack_ms": summary(rec.stage["ack_ms"]),
            "queue_wait_ms": summary(rec.stage["queue_wait_ms"]),
            "ai_decision_ms": summary(rec.stage["ai_decision_ms"]),
            "rag_retrieval_ms": summary(rec.stage["rag_retrieval_ms"]),
            "reply_visible_ms": summary(rec.stage["reply_visible_ms"]),
            "staff_visible_ms_modelled": summary(staff),
            "db_ms_per_message": round(rec.db_ms / rec.messages, 1) if rec.messages else None,
            "db_statements_per_message": round(rec.db_statements / rec.messages, 1)
            if rec.messages
            else None,
            "pool_peak_checked_out": rec.pool_peak,
            "peak_rss_mb": peak_rss_mb(),
        }
        if extra:
            row.update(extra)
        for key, values in rec.stage.items():
            if key.endswith("_reply_visible_ms") and key != "reply_visible_ms":
                row[key] = summary(values)
        return row

    def texts_for(index: int) -> list[str]:
        # Every 4th visitor needs a human (handoff), the rest are answered;
        # the knowledge question exercises retrieval.
        first = "[handoff] I want to speak to a person" if index % 4 == 3 else (
            "What is your refund policy for a deposit?"
        )
        return [first, "Thanks - and how long does it take?"]

    try:
        dispatcher = use_dispatcher(args.concurrency)
        # ---- 1/5/20/50 tenants -------------------------------------------
        for tenants in args.tenants:
            rec.reset()
            groups = [await make_tenant(1) for _ in range(tenants)]
            rec.reset()
            started = time.perf_counter()
            await asyncio.gather(
                *(visitor(tokens[0], texts_for(i), "all") for i, (_o, tokens) in enumerate(groups))
            )
            await dispatcher.drain(timeout=600)
            results["workloads"].append(
                collect(f"{tenants}_tenants", {"wall_s": round(time.perf_counter() - started, 2)})
            )

        # ---- noisy tenant ---------------------------------------------------
        rec.reset()
        _noisy_org, noisy_tokens = await make_tenant(20)
        quiet = [await make_tenant(1) for _ in range(10)]
        rec.reset()
        noisy = [visitor(t, ["flood question?"], "noisy") for t in noisy_tokens]
        calm = [visitor(tokens[0], ["quiet question?"], "quiet") for _o, tokens in quiet]
        await asyncio.gather(*noisy, *calm)
        await dispatcher.drain(timeout=600)
        results["workloads"].append(collect("noisy_tenant_20_sessions_plus_10_quiet"))

        # ---- concurrent replies within one tenant ----------------------------
        rec.reset()
        _org, tokens = await make_tenant(10)
        rec.reset()
        await asyncio.gather(*(visitor(t, ["same moment?"], "burst") for t in tokens))
        await dispatcher.drain(timeout=600)
        results["workloads"].append(collect("one_tenant_10_concurrent_replies"))

        # ---- crash of every in-flight turn -----------------------------------
        rec.reset()
        crash_groups = [await make_tenant(1) for _ in range(20)]
        rec.reset()
        settings.public_chat_turn_stale_seconds = 3
        crash_started = time.perf_counter()
        visitors = asyncio.gather(
            *(visitor(tokens[0], ["during a crash?"], "crash") for _o, tokens in crash_groups)
        )
        await asyncio.sleep(args.decision_seconds / 2)
        for task in list(dispatcher._tasks):
            task.cancel()
        await asyncio.gather(*list(dispatcher._tasks), return_exceptions=True)
        dispatcher = use_dispatcher(args.concurrency)  # the restarted process
        recovered = await dispatcher.recover_on_startup()
        await visitors
        await dispatcher.drain(timeout=600)
        crash_org_ids = [org for org, _t in crash_groups]
        async with database.AsyncSessionLocal() as db:
            jobs = (
                await db.execute(
                    select(IntegrationJob.status).where(
                        IntegrationJob.organization_id.in_(crash_org_ids)
                    )
                )
            ).scalars().all()
        results["workloads"].append(
            collect(
                "crash_all_inflight_turns_20_tenants",
                {
                    "recovered_on_startup": recovered,
                    "jobs_completed": sum(1 for s in jobs if s == "completed"),
                    "jobs_total": len(jobs),
                    "wall_s": round(time.perf_counter() - crash_started, 2),
                },
            )
        )
        settings.public_chat_turn_stale_seconds = 120

        # ---- concurrency-limit sweep (20 simultaneous tenants) ---------------
        sweep = []
        for limit in args.sweep:
            dispatcher = use_dispatcher(limit)
            rec.reset()
            groups = [await make_tenant(1) for _ in range(20)]
            rec.reset()
            started = time.perf_counter()
            await asyncio.gather(
                *(visitor(tokens[0], ["sweep question?"], "sweep") for _o, tokens in groups)
            )
            await dispatcher.drain(timeout=600)
            row = collect(
                f"sweep_concurrency_{limit}",
                {
                    "turn_concurrency": limit,
                    "analysis_slots": workflow_module.analysis_capacity.limit,
                    "wall_s": round(time.perf_counter() - started, 2),
                },
            )
            sweep.append(row)
        results["concurrency_sweep"] = sweep
    finally:
        await client.aclose()
        async with database.AsyncSessionLocal() as db:
            org_ids = list(
                (
                    await db.execute(
                        select(Organization.id).where(Organization.name.like(f"{prefix}%"))
                    )
                ).scalars()
            )
            if org_ids:
                for table in reversed(Base.metadata.sorted_tables):
                    if "organization_id" in table.columns:
                        await db.execute(
                            delete(table).where(table.c.organization_id.in_(org_ids))
                        )
                await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
                await db.commit()
        await engine.dispose()

    results["cost_estimate"] = cost_estimate(settings)
    return results


def cost_estimate(settings: Any) -> dict[str, Any]:
    price_in = settings.llm_input_cost_per_million / 1_000_000
    price_out = settings.llm_output_cost_per_million / 1_000_000
    decision = DECISION_TOKENS[0] * price_in + DECISION_TOKENS[1] * price_out
    rag = RAG_TOKENS[0] * price_in + RAG_TOKENS[1] * price_out
    return {
        "basis": "ESTIMATE from assumed token counts x configured list prices",
        "assumed_tokens": {"decision": DECISION_TOKENS, "rag": RAG_TOKENS},
        "usd_per_turn_decision_only": round(decision, 6),
        "usd_per_turn_with_rag": round(decision + rag, 6),
        "usd_per_1000_turns_with_rag": round((decision + rag) * 1000, 3),
        "reservation_per_turn_usd": settings.public_chat_turn_reserved_cost_usd,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--decision-seconds", type=float, default=2.0)
    parser.add_argument("--retrieval-seconds", type=float, default=0.3)
    parser.add_argument("--tenants", type=int, nargs="+", default=[1, 5, 20, 50])
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--sweep", type=int, nargs="+", default=[2, 4, 6, 8])
    parser.add_argument("--reply-timeout", type=float, default=300.0)
    parser.add_argument("--cpu-note", default="unconstrained host")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    from sqlalchemy.engine import make_url

    from app.core.config import settings

    name = make_url(settings.database_url).database or ""
    if not name.endswith("_disposable_test"):
        print("refusing: certification only runs against a *_disposable_test database", file=sys.stderr)
        return 1
    random.seed(7)
    results = asyncio.run(run(args))
    text = json.dumps(results, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
