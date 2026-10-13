"""Public-chat performance certification on disposable infrastructure (1P.9R).

Two halves, so the server under test gets the CPU/memory budget alone:

``serve`` (run inside a container limited like Render Starter, e.g.
``docker run --cpus 0.5 --memory 512m``) starts the real application under
uvicorn with its production-shaped pool (5 + 10, pre-ping) and every
provider replaced by a fake of fixed latency (no network call, no cost). It
records server-side stage timings and resource use and exposes them on
``/__cert/*`` routes that exist only in this mode.

``drive`` (run outside the container) creates disposable tenants directly in
the disposable database, then acts as visitors over real HTTP:

* acknowledgement      - POST /messages until the response
* queue wait           - job created until claimed by a turn task (server)
* AI decision          - agent analysis incl. fake decision model (server)
* RAG retrieval        - knowledge search, fake embedding + vector (server)
* database             - statement time and count per message (server)
* reply visible        - send until the reply is seen, polling at the
                         server's suggested pace
* staff visible        - handoff stored -> visible on the 15 s inbox refresh
                         (MODELLED: availability + uniform 0..15 s)
* pool / CPU / memory  - peak pooled connections, time waiting for one,
                         server CPU seconds, sampled RSS

Workloads: 1/5/20/50 tenants at once (two messages each), a noisy tenant,
one tenant's concurrent replies with lost-response retries, allowance
exhaustion, a long conversation, a crash of every in-flight turn, and a sweep
of turn-concurrency limits. Every session is then checked for ordering,
duplicate messages/replies, unanswered messages and cross-tenant messages.

Results describe THIS machine with fake providers: evidence for the design
(queueing, fairness, pool safety, failure rate), not production latency.

    docker run --cpus 0.5 --memory 512m -p 127.0.0.1:18000:8000 ... \\
        python -m scripts.certify_public_chat_performance serve
    .venv/bin/python -m scripts.certify_public_chat_performance drive \\
        --target http://127.0.0.1:18000 --out results.json

Both halves refuse to run unless the database name ends in ``_disposable_test``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
ORIGIN = "https://bench.example.test"


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
    return round(rss / (1024 * 1024) if platform.system() == "Darwin" else rss / 1024, 1)


def current_rss_mb() -> float | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        return None
    return None


def write_json(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def require_disposable_database() -> None:
    from sqlalchemy.engine import make_url

    from app.core.config import settings

    name = make_url(settings.database_url).database or ""
    if not name.endswith("_disposable_test"):
        print("refusing: certification only runs against a *_disposable_test database", file=sys.stderr)
        raise SystemExit(1)


# ===========================================================================
# serve: the application under test (inside the constrained container)
# ===========================================================================


class ServerRecorder:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.stage: dict[str, list[float]] = defaultdict(list)
        self.db_ms = 0.0
        self.db_statements = 0
        self.pool_out = 0
        self.pool_peak = 0
        self.model_calls = 0
        self.rss_samples: list[float] = []
        self.cpu0 = cpu_seconds()
        self.wall0 = time.perf_counter()

    def snapshot(self) -> dict[str, Any]:
        wall = time.perf_counter() - self.wall0
        cpu = cpu_seconds() - self.cpu0
        return {
            "queue_wait_ms": summary(self.stage["queue_wait_ms"]),
            "ai_decision_ms": summary(self.stage["ai_decision_ms"]),
            "rag_retrieval_ms": summary(self.stage["rag_retrieval_ms"]),
            "pool_wait_ms": summary(self.stage["pool_wait_ms"]),
            "db_ms": round(self.db_ms, 1),
            "db_statements": self.db_statements,
            "pool_peak_checked_out": self.pool_peak,
            "model_calls": self.model_calls,
            "cpu_seconds": round(cpu, 2),
            "cpu_utilisation_of_one_core": round(cpu / wall, 3) if wall else None,
            "rss_mb_max_sampled": max(self.rss_samples) if self.rss_samples else None,
            "peak_rss_mb_process_lifetime": peak_rss_mb(),
        }


def serve(args: argparse.Namespace) -> int:
    require_disposable_database()
    import uvicorn
    from fastapi import APIRouter
    from sqlalchemy import event

    from app.core import database
    from app.core.config import settings
    from app.core.db_connection import AnalysisCapacity
    from app.main import app
    from app.repositories.integration_job_repository import IntegrationJobRepository
    from app.schemas.agent import AgentDecision
    from app.services import agent_workflow_service as workflow_module
    from app.services import public_chat_turn_dispatcher as dispatcher_module
    from app.services.knowledge_search_service import KnowledgeSearchService

    rec = ServerRecorder()
    settings.public_chat_async_enabled = True
    settings.platform_monthly_ai_budget_usd = 1_000_000.0

    answer = AgentDecision(
        action="no_action", reason="bench", requires_human_approval=False, response_draft=""
    )
    escalate = AgentDecision(
        action="escalate", reason="bench handoff", requires_human_approval=True, response_draft=""
    )

    class FakeDecisionModel:
        async def ainvoke(self, messages: list) -> dict:
            text = " ".join(str(m.content) for m in messages)
            rec.model_calls += 1
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

    sync_engine = database.engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, _cursor, _stmt, _params, _context, _many):
        conn.info.setdefault("t0", []).append(time.perf_counter())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, _cursor, _stmt, _params, _context, _many):
        rec.db_ms += (time.perf_counter() - conn.info["t0"].pop()) * 1000
        rec.db_statements += 1

    @event.listens_for(sync_engine.pool, "checkout")
    def _checkout(*_a: Any) -> None:
        rec.pool_out += 1
        rec.pool_peak = max(rec.pool_peak, rec.pool_out)

    @event.listens_for(sync_engine.pool, "checkin")
    def _checkin(*_a: Any) -> None:
        rec.pool_out -= 1

    pool = sync_engine.pool
    original_do_get = pool._do_get

    def timed_do_get() -> Any:
        started = time.perf_counter()
        try:
            return original_do_get()
        finally:
            rec.stage["pool_wait_ms"].append((time.perf_counter() - started) * 1000)

    pool._do_get = timed_do_get  # type: ignore[method-assign]

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

    use_dispatcher(args.concurrency)
    router = APIRouter(prefix="/__cert")

    @router.post("/reset")
    async def _reset() -> dict:
        rec.reset()
        return {"ok": True}

    @router.get("/metrics")
    async def _metrics() -> dict:
        return rec.snapshot()

    @router.post("/drain")
    async def _drain() -> dict:
        await dispatcher_module.turn_dispatcher.drain(timeout=600)
        return {"ok": True}

    @router.post("/dispatcher/{concurrency}")
    async def _dispatcher(concurrency: int) -> dict:
        use_dispatcher(concurrency)
        return {"analysis_slots": workflow_module.analysis_capacity.effective_limit()}

    @router.post("/stale/{seconds}")
    async def _stale(seconds: int) -> dict:
        settings.public_chat_turn_stale_seconds = seconds
        return {"ok": True}

    @router.post("/crash")
    async def _crash() -> dict:
        """Cancel every in-flight turn (the process dies), then behave like
        the restarted process: a fresh dispatcher and start-up recovery."""
        old = dispatcher_module.turn_dispatcher
        tasks = list(old._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        fresh = use_dispatcher(settings.public_chat_turn_max_concurrency)
        return {"cancelled": len(tasks), "recovered": await fresh.recover_on_startup()}

    app.include_router(router)

    async def _sample_rss() -> None:
        while True:
            value = current_rss_mb()
            if value is not None:
                rec.rss_samples.append(value)
            await asyncio.sleep(0.5)

    async def _run() -> None:
        sampler = asyncio.get_running_loop().create_task(_sample_rss())
        # Keep-alive as in scripts/start_render_api.sh (30 s), not uvicorn's 5 s.
        config = uvicorn.Config(
            app, host="0.0.0.0", port=args.port, log_level="warning", timeout_keep_alive=30
        )
        try:
            await uvicorn.Server(config).serve()
        finally:
            sampler.cancel()

    asyncio.run(_run())
    return 0


# ===========================================================================
# drive: visitors over HTTP (outside the container)
# ===========================================================================


class ClientRecorder:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.stage: dict[str, list[float]] = defaultdict(list)
        self.failures: list[str] = []
        self.messages = 0
        self.sessions: list[tuple[int, str]] = []
        self.wall0 = time.perf_counter()


async def drive(args: argparse.Namespace) -> int:
    require_disposable_database()
    import httpx
    from sqlalchemy import delete, select

    from app.core import database
    from app.models.base import Base
    from app.models.integration_job import IntegrationJob
    from app.models.organization import Organization
    from app.models.public_chat import PublicChatConfiguration
    from app.services.public_chat_service import hash_digest

    rec = ClientRecorder()
    prefix = f"cert-{uuid.uuid4().hex[:6]}-"
    client = httpx.AsyncClient(
        base_url=args.target,
        timeout=httpx.Timeout(120.0),
        limits=httpx.Limits(max_connections=400, max_keepalive_connections=400),
    )
    results: dict[str, Any] = {
        "environment": {
            "server": args.server_note,
            "driver": f"{platform.system()} {platform.machine()} python {platform.python_version()}",
            "decision_seconds": args.decision_seconds,
            "retrieval_seconds": args.retrieval_seconds,
            "pool": "QueuePool 5 + 10 overflow, pre_ping (application default)",
            "transport": "real HTTP to uvicorn (single worker process)",
        },
        "workloads": [],
    }

    async def admin(method: str, path: str) -> dict:
        response = await client.request(method, f"/__cert{path}")
        response.raise_for_status()
        return response.json()

    async def make_tenant(sessions: int, *, turn_limit: int | None = None) -> tuple[int, list[str]]:
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
                    allowed_origins=[ORIGIN],
                    enabled=True,
                    max_message_length=4000,
                    max_messages_per_minute=120,
                    session_ttl_hours=24,
                    async_turns_enabled=True,
                    monthly_ai_turn_limit=turn_limit,
                )
            )
            await db.commit()
            org_id = org.id
        tokens = []
        for _ in range(sessions):
            response = await client.post(
                "/public/chat/sessions",
                json={"public_widget_key": key},
                headers={"X-Embedding-Origin": ORIGIN},
            )
            response.raise_for_status()
            tokens.append(response.json()["session"]["token"])
        return org_id, tokens

    async def visitor(
        org_id: int, token: str, texts: list[str], label: str, *, resends: int = 0
    ) -> None:
        headers = {"Authorization": f"Bearer {token}", "X-Embedding-Origin": ORIGIN}
        rec.sessions.append((org_id, token))
        cursor = 0
        for raw_text in texts:
            text = f"[t{org_id}] {raw_text}"
            message_id = uuid.uuid4().hex
            payload = {"client_message_id": message_id, "text": text, "delivery": "async"}
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/public/chat/messages", json=payload, headers=headers
                )
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - counted
                rec.failures.append(f"send:{type(exc).__name__}")
                continue
            rec.messages += 1
            accepted = time.perf_counter()
            rec.stage["ack_ms"].append((accepted - started) * 1000)
            body = response.json()
            for _ in range(resends):  # lost response: retry with the SAME id
                again = await client.post("/public/chat/messages", json=payload, headers=headers)
                if again.status_code == 200 and again.json()["message_id"] != body["message_id"]:
                    rec.failures.append("duplicate_message_on_retry")
            if body.get("processing_state") != "accepted":
                rec.stage[f"{label}_reply_visible_ms"].append((accepted - started) * 1000)
                continue
            deadline = started + args.reply_timeout
            seen = False
            while time.perf_counter() < deadline:
                try:
                    poll = await client.get(
                        f"/public/chat/sessions/updates?after_id={cursor}", headers=headers
                    )
                    poll.raise_for_status()
                except Exception as exc:  # noqa: BLE001 - a failed poll is retried
                    rec.failures.append(f"poll:{type(exc).__name__}")
                    await asyncio.sleep(1.0)
                    continue
                update = poll.json()
                rec.stage["poll_interval_hint_ms"].append(update["poll_after_ms"])
                cursor = update["cursor"]
                if any(m["direction"] == "outbound" for m in update["messages"]):
                    visible = (time.perf_counter() - started) * 1000
                    rec.stage[f"{label}_reply_visible_ms"].append(visible)
                    rec.stage["reply_visible_ms"].append(visible)
                    if update["status"] == "human_requested":
                        rec.stage["staff_available_ms"].append(visible)
                    seen = True
                    break
                await asyncio.sleep(update["poll_after_ms"] / 1000)
            if not seen:
                rec.failures.append("reply_timeout")

    async def verify_sessions() -> dict[str, int]:
        violations: dict[str, int] = defaultdict(int)
        for org_id, token in rec.sessions:
            headers = {"Authorization": f"Bearer {token}", "X-Embedding-Origin": ORIGIN}
            cursor, messages, status = 0, [], "unknown"
            while True:
                page = (
                    await client.get(
                        f"/public/chat/sessions/updates?after_id={cursor}", headers=headers
                    )
                ).json()
                status = page["status"]
                if not page["messages"]:
                    break
                messages += page["messages"]
                cursor = page["cursor"]
            ids = [m["id"] for m in messages]
            inbound = [m["body"] for m in messages if m["direction"] == "inbound"]
            outbound = [m for m in messages if m["direction"] == "outbound"]
            if ids != sorted(ids):
                violations["out_of_order"] += 1
            if len(inbound) != len(set(inbound)):
                violations["duplicate_inbound"] += 1
            if any(not body.startswith(f"[t{org_id}] ") for body in inbound):
                violations["cross_tenant_message"] += 1
            if len(outbound) > len(inbound):
                violations["duplicate_reply"] += 1
            if status == "ai_active" and len(outbound) != len(inbound):
                violations["unanswered_message"] += 1
        return dict(violations)

    async def run_workload(name: str, scenario, extra: dict[str, Any] | None = None) -> dict:
        await admin("POST", "/reset")
        rec.reset()
        await scenario()
        await admin("POST", "/drain")
        server = await admin("GET", "/metrics")
        staff = [
            v + random.uniform(0, STAFF_REFRESH_SECONDS * 1000)
            for v in rec.stage.get("staff_available_ms", [])
        ]
        sends_failed = sum(1 for f in rec.failures if f.startswith("send:"))
        total = rec.messages + sends_failed
        row: dict[str, Any] = {
            "workload": name,
            "messages": rec.messages,
            "failures": len(rec.failures),
            "failure_types": sorted(set(rec.failures)),
            "send_failure_rate": round(sends_failed / total, 4) if total else 0.0,
            "wall_s": round(time.perf_counter() - rec.wall0, 2),
            "ack_ms": summary(rec.stage["ack_ms"]),
            "reply_visible_ms": summary(rec.stage["reply_visible_ms"]),
            "staff_visible_ms_modelled": summary(staff),
            "poll_interval_hint_ms": summary(rec.stage["poll_interval_hint_ms"]),
            "db_ms_per_message": round(server["db_ms"] / rec.messages, 1) if rec.messages else None,
            "db_statements_per_message": round(server["db_statements"] / rec.messages, 1)
            if rec.messages
            else None,
            **{k: v for k, v in server.items() if k not in ("db_ms", "db_statements")},
        }
        for key, values in rec.stage.items():
            if key.endswith("_reply_visible_ms") and key != "reply_visible_ms":
                row[key] = summary(values)
        if extra:
            row.update(extra)
        row["violations"] = await verify_sessions()
        results["workloads"].append(row)
        if args.out:
            write_json(args.out, results)
        print(
            f"{name}: msgs={row['messages']} fail={row['failures']} {row['failure_types']} "
            f"reply_p95={row['reply_visible_ms']['p95']} pool_peak={row['pool_peak_checked_out']} "
            f"cpu={row['cpu_utilisation_of_one_core']} rss={row['rss_mb_max_sampled']} "
            f"viol={row['violations']}",
            file=sys.stderr,
            flush=True,
        )
        return row

    def texts_for(index: int) -> list[str]:
        first = (
            "[handoff] I want to speak to a person"
            if index % 4 == 3
            else "What is your refund policy for a deposit?"
        )
        return [first, "Thanks - and how long does it take?"]

    try:
        await admin("POST", f"/dispatcher/{args.concurrency}")
        for tenants in args.tenants:
            groups = [await make_tenant(1) for _ in range(tenants)]

            async def _many(groups=groups) -> None:
                await asyncio.gather(
                    *(visitor(o, t[0], texts_for(i), "all") for i, (o, t) in enumerate(groups))
                )

            await run_workload(f"{tenants}_tenants_two_messages_each", _many)

        noisy_org, noisy_tokens = await make_tenant(args.noisy_sessions)
        quiet = [await make_tenant(1) for _ in range(10)]

        async def _noisy() -> None:
            await asyncio.gather(
                *(visitor(noisy_org, t, ["flood question?"], "noisy") for t in noisy_tokens),
                *(visitor(o, t[0], ["quiet question?"], "quiet") for o, t in quiet),
            )

        await run_workload(f"noisy_tenant_{args.noisy_sessions}_sessions_plus_10_quiet", _noisy)

        burst_org, burst_tokens = await make_tenant(10)

        async def _burst() -> None:
            await asyncio.gather(
                *(visitor(burst_org, t, ["same moment?"], "burst", resends=2) for t in burst_tokens)
            )

        await run_workload("one_tenant_10_concurrent_with_2_lost_response_retries", _burst)

        limited = [await make_tenant(1, turn_limit=1) for _ in range(10)]

        async def _budget() -> None:
            await asyncio.gather(
                *(visitor(o, t[0], ["first?", "second?", "third?"], "budget") for o, t in limited)
            )

        await run_workload(
            "allowance_exhaustion_10_tenants_limit_1_turn", _budget, {"expected_model_calls": 10}
        )

        long_org, (long_token,) = await make_tenant(1)

        async def _long() -> None:
            await visitor(
                long_org, long_token, [f"question {n}?" for n in range(args.long_turns)], "long"
            )

        await run_workload(f"long_conversation_{args.long_turns}_turns", _long)

        crash_groups = [await make_tenant(1) for _ in range(20)]
        crash_info: dict[str, Any] = {}

        async def _crash() -> None:
            await admin("POST", "/stale/3")
            visitors = asyncio.gather(
                *(visitor(o, t[0], ["during a crash?"], "crash") for o, t in crash_groups)
            )
            await asyncio.sleep(args.decision_seconds / 2)
            crash_info.update(await admin("POST", "/crash"))
            await visitors

        await run_workload("crash_all_inflight_turns_20_tenants", _crash, crash_info)
        await admin("POST", "/stale/120")
        async with database.AsyncSessionLocal() as db:
            statuses = (
                await db.execute(
                    select(IntegrationJob.status).where(
                        IntegrationJob.organization_id.in_([o for o, _t in crash_groups])
                    )
                )
            ).scalars().all()
        results["workloads"][-1]["jobs_completed"] = sum(1 for s in statuses if s == "completed")
        results["workloads"][-1]["jobs_total"] = len(statuses)

        for limit in args.sweep:
            slots = (await admin("POST", f"/dispatcher/{limit}"))["analysis_slots"]
            sweep_groups = [await make_tenant(1) for _ in range(args.sweep_tenants)]

            async def _sweep(sweep_groups=sweep_groups) -> None:
                await asyncio.gather(
                    *(visitor(o, t[0], ["sweep question?"], "sweep") for o, t in sweep_groups)
                )

            await run_workload(
                f"sweep_turn_concurrency_{limit}_{args.sweep_tenants}_tenants",
                _sweep,
                {"turn_concurrency": limit, "analysis_slots": slots},
            )
        await admin("POST", f"/dispatcher/{args.concurrency}")
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
                        await db.execute(delete(table).where(table.c.organization_id.in_(org_ids)))
                await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
                await db.commit()
        await database.engine.dispose()

    from app.core.config import settings

    results["cost_estimate"] = cost_estimate(settings)
    if args.out:
        write_json(args.out, results)
    return 0


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
    sub = parser.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    s.add_argument("--decision-seconds", type=float, default=2.0)
    s.add_argument("--retrieval-seconds", type=float, default=0.3)
    s.add_argument("--concurrency", type=int, default=4)
    d = sub.add_parser("drive")
    d.add_argument("--target", default="http://127.0.0.1:18000")
    d.add_argument("--decision-seconds", type=float, default=2.0, help="for the report only")
    d.add_argument("--retrieval-seconds", type=float, default=0.3, help="for the report only")
    d.add_argument("--tenants", type=int, nargs="+", default=[1, 5, 20, 50])
    d.add_argument("--concurrency", type=int, default=4)
    d.add_argument("--sweep", type=int, nargs="+", default=[2, 4, 6, 8])
    d.add_argument("--sweep-tenants", type=int, default=20)
    d.add_argument("--noisy-sessions", type=int, default=20)
    d.add_argument("--long-turns", type=int, default=40)
    d.add_argument("--reply-timeout", type=float, default=300.0)
    d.add_argument("--server-note", default="unconstrained")
    d.add_argument("--out", default="")
    args = parser.parse_args(argv)
    random.seed(7)
    if args.mode == "serve":
        return serve(args)
    return asyncio.run(drive(args))


if __name__ == "__main__":
    raise SystemExit(main())
