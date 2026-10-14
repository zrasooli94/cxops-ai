"""Phase 1R - release diagnostics, start-up resilience, embedding memo, metrics."""

import asyncio
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import generate_latest

from app.core import build_info, metrics, schema_gate
from app.core.config import settings
from app.main import app
from app.services.embedding_service import QueryEmbeddingCache

SHA = "c9e55b757509f661edf6dcecaaa3bf186b6bf495"


# --- build identity ---------------------------------------------------------


def test_build_sha_accepts_only_plain_hex_commit_ids():
    assert build_info.build_sha({"RENDER_GIT_COMMIT": SHA}) == SHA
    assert build_info.build_sha({"GITHUB_SHA": SHA.upper()}) == SHA
    assert build_info.build_sha({"BUILD_SHA": "abc1234"}) == "abc1234"


@pytest.mark.parametrize(
    "value",
    [
        "main",
        "postgresql://user:password@host/db",
        "sk-" + "a" * 40,
        "abc12",  # too short
        "zz" * 20,
        SHA + "\n; rm -rf /",
        "",
    ],
)
def test_build_sha_drops_anything_that_is_not_a_commit_id(value):
    assert build_info.build_sha({"RENDER_GIT_COMMIT": value}) is None


def test_build_sha_priority_skips_malformed_earlier_value():
    env = {"BUILD_SHA": "not-a-sha", "RENDER_GIT_COMMIT": SHA}
    assert build_info.build_sha(env) == SHA


@pytest.mark.asyncio
async def test_version_endpoint_reports_build_and_uptime_without_secrets(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", SHA)
    monkeypatch.delenv("BUILD_SHA", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/version")
    assert response.status_code == 200
    body = response.json()
    assert body["build_sha"] == SHA
    assert body["uptime_seconds"] >= 0
    assert set(body) == {
        "service", "version", "environment", "build_sha", "started_at", "uptime_seconds",
    }
    dumped = json.dumps(body).lower()
    for forbidden in ("database", "password", "secret", "token", "postgres"):
        assert forbidden not in dumped


@pytest.mark.asyncio
async def test_version_build_sha_is_null_when_unknown(monkeypatch):
    for name in build_info._SHA_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.get("/version")).json()
    assert body["build_sha"] is None


# --- schema gate start-up resilience ----------------------------------------


@pytest.mark.asyncio
async def test_schema_gate_recovers_from_a_hung_first_connect(monkeypatch):
    monkeypatch.setattr(settings, "schema_gate_attempt_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "schema_gate_retry_delay_seconds", 0.0)
    calls = {"n": 0}

    async def flaky(_engine):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(5)  # the database is still waking up
        return frozenset({"rev"})

    monkeypatch.setattr(schema_gate, "read_current", flaky)
    started = time.perf_counter()
    result = await schema_gate.read_current_with_retry(object())  # type: ignore[arg-type]
    assert result == frozenset({"rev"})
    assert calls["n"] == 2
    assert time.perf_counter() - started < 1.0


@pytest.mark.asyncio
async def test_schema_gate_gives_up_after_bounded_attempts(monkeypatch):
    monkeypatch.setattr(settings, "schema_gate_attempt_timeout_seconds", 0.02)
    monkeypatch.setattr(settings, "schema_gate_retry_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "schema_gate_max_attempts", 3)
    calls = {"n": 0}

    async def hung(_engine):
        calls["n"] += 1
        await asyncio.sleep(5)

    monkeypatch.setattr(schema_gate, "read_current", hung)
    with pytest.raises(TimeoutError):
        await schema_gate.read_current_with_retry(object())  # type: ignore[arg-type]
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_behind_schema_is_not_retried_and_still_refuses(monkeypatch):
    calls = {"n": 0}
    heads, known = schema_gate.build_revisions()
    older = next(iter(known - heads))  # a revision this build knows is behind

    async def behind(_engine):
        calls["n"] += 1
        return frozenset({older})

    monkeypatch.setattr(schema_gate, "read_current", behind)
    with pytest.raises(schema_gate.SchemaBehindError):
        await schema_gate.check_schema(object(), enforce=True)  # type: ignore[arg-type]
    assert calls["n"] == 1


# --- query embedding memo ---------------------------------------------------


def test_embedding_cache_hits_expires_and_evicts(monkeypatch):
    cache = QueryEmbeddingCache(max_entries=2, ttl_seconds=10)
    k1, k2, k3 = (cache.key(t) for t in ("a", "b", "c"))
    assert cache.get(k1) is None
    cache.put(k1, [1.0])
    cache.put(k2, [2.0])
    assert cache.get(k1) == [1.0]
    cache.put(k3, [3.0])  # evicts k2 (least recently used)
    assert cache.get(k2) is None and cache.get(k3) == [3.0]
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 11)
    assert cache.get(k1) is None


def test_embedding_cache_disabled_and_key_is_not_the_text():
    cache = QueryEmbeddingCache(max_entries=0, ttl_seconds=10)
    key = cache.key("secret customer question")
    cache.put(key, [1.0])
    assert cache.get(key) is None
    assert "secret" not in key


@pytest.mark.asyncio
async def test_embedding_service_calls_provider_once_per_distinct_text(monkeypatch):
    from app.services.embedding_service import EmbeddingService

    service = EmbeddingService()
    calls = []

    class Client:
        async def aembed_query(self, text):
            calls.append(text)
            return [0.1, 0.2]

    service.client = Client()  # type: ignore[assignment]
    first = await service.embed_text("hello world")
    second = await service.embed_text("hello world")
    await service.embed_text("different")
    assert first == second
    assert calls == ["hello world", "different"]


# --- metrics vocabulary -----------------------------------------------------


def test_answer_decision_metric_label_is_a_closed_set():
    metrics.record_public_chat_answer_decision(outcome="grounded_reply")
    metrics.record_public_chat_answer_decision(outcome="a customer typed this")
    text = generate_latest().decode()
    assert 'outcome="grounded_reply"' in text
    assert 'outcome="other"' in text
    assert "customer typed" not in text


def test_turn_stage_metric_ignores_unknown_stages():
    metrics.record_public_chat_turn_stage(stage="agent", seconds=0.5)
    metrics.record_public_chat_turn_stage(stage="user-supplied", seconds=1)
    text = generate_latest().decode()
    assert 'stage="agent"' in text
    assert "user-supplied" not in text


# --- Phase 1S: bounded model clients, write-path round trips ----------------


def test_openai_clients_are_bounded():
    from app.services.agent_workflow_service import agent_workflow_service
    from app.services.embedding_service import embedding_service
    from app.services.rag_service import rag_service

    assert settings.openai_request_timeout_seconds <= 60
    assert settings.openai_max_retries <= 2
    assert rag_service.llm.request_timeout == settings.openai_request_timeout_seconds
    assert rag_service.llm.max_retries == settings.openai_max_retries
    assert embedding_service.client.request_timeout == settings.openai_request_timeout_seconds
    assert agent_workflow_service.decision_llm is not None


@pytest.mark.asyncio
async def test_refresh_if_stale_skips_clean_objects_and_refreshes_expired_ones():
    from sqlalchemy import inspect as sa_inspect

    from app.core.db_connection import refresh_if_stale

    class FakeDb:
        def __init__(self):
            self.refreshed = []

        async def refresh(self, instance):
            self.refreshed.append(instance)

    class Row:
        pass

    from sqlalchemy.orm import Mapper  # noqa: F401  (documents the inspect contract)

    db = FakeDb()
    clean = type("S", (), {"expired": False, "expired_attributes": set()})()
    stale = type("S", (), {"expired": False, "expired_attributes": {"updated_at"}})()
    expired = type("S", (), {"expired": True, "expired_attributes": set()})()

    import app.core.db_connection as module

    original = module.inspect
    try:
        for state, should_refresh in ((clean, False), (stale, True), (expired, True)):
            module.inspect = lambda _obj, _state=state: _state  # type: ignore[assignment]
            before = len(db.refreshed)
            await refresh_if_stale(db, Row())  # type: ignore[arg-type]
            assert (len(db.refreshed) - before == 1) is should_refresh
    finally:
        module.inspect = original
    assert sa_inspect is not None


@pytest.mark.asyncio
async def test_a_repository_write_needs_no_follow_up_select():
    """INSERT ... RETURNING populates id/created_at; no refresh SELECT follows."""
    import uuid

    from sqlalchemy import delete, event

    from app.core.database import AsyncSessionLocal, engine
    from app.models.ai_request_log import AIRequestLog
    from app.models.organization import Organization
    from app.services.ai_observability_service import AIObservabilityService

    statements: list[str] = []

    def capture(_conn, _cursor, statement, *_rest):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    async with AsyncSessionLocal() as db:
        org = Organization(name=f"phase1s-write-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.commit()
        try:
            event.listen(engine.sync_engine, "before_cursor_execute", capture)
            try:
                log = await AIObservabilityService.record(
                    db,
                    organization_id=org.id,
                    request_id=uuid.uuid4().hex,
                    question="q",
                    answer="a",
                    grounded=True,
                    llm_called=False,
                    retrieval_count=0,
                    best_similarity=None,
                    sources=[],
                    latency_ms=1.0,
                    feature="phase1s",
                )
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", capture)
            assert log.id is not None and log.created_at is not None
            assert statements == ["INSERT"], statements
        finally:
            await db.execute(delete(AIRequestLog).where(AIRequestLog.organization_id == org.id))
            await db.execute(delete(Organization).where(Organization.id == org.id))
            await db.commit()


@pytest.mark.asyncio
async def test_ready_reports_database_round_trip_without_leaking_anything():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/ready")
    body = response.json()
    assert response.status_code == 200
    assert body["checks"] == {"database": "ok"}
    timing = body["timing_ms"]
    assert set(timing) == {"database_first_statement", "database_round_trip"}
    assert all(isinstance(v, int | float) and v >= 0 for v in timing.values())
    dumped = json.dumps(body).lower()
    for forbidden in ("postgres", "password", "127.0.0.1", "cxops_disposable"):
        assert forbidden not in dumped
