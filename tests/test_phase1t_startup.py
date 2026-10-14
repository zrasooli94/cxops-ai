"""Phase 1T - start-up cost, boot instrumentation and fail-closed start-up."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_python(code: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "DATABASE_URL": "postgresql+asyncpg://t:t@127.0.0.1:1/t",
            "OPENAI_API_KEY": "test-not-a-key",
            **env,
        },
    )


# --- the provider SDK is off the start-up path --------------------------------


def test_importing_the_application_does_not_load_the_provider_sdk():
    proc = _run_python(
        "import sys, app.main\n"
        "print(sorted(m for m in ('openai','langchain_openai') if m in sys.modules))"
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    assert proc.stdout.strip() == "[]", proc.stdout


def test_provider_clients_are_built_on_first_use_and_stay_assignable():
    proc = _run_python(
        "import sys\n"
        "from app.services.rag_service import rag_service\n"
        "from app.services.embedding_service import embedding_service\n"
        "from app.services.agent_workflow_service import agent_workflow_service\n"
        "assert 'langchain_openai' not in sys.modules\n"
        "fake = object()\n"
        "rag_service.llm = fake\n"
        "assert rag_service.llm is fake\n"
        "assert 'langchain_openai' not in sys.modules\n"
        "client = embedding_service.client\n"
        "assert 'langchain_openai' in sys.modules\n"
        "assert client is embedding_service.client\n"
        "assert type(agent_workflow_service.decision_llm).__name__\n"
        "print('ok')\n"
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout.strip().endswith("ok")


@pytest.mark.asyncio
async def test_warmup_builds_clients_and_never_raises(monkeypatch):
    from app.services import provider_warmup

    built = []
    monkeypatch.setattr(provider_warmup, "_build_clients", lambda: built.append(1))
    assert await provider_warmup.warm_provider_clients() is not None
    assert built == [1]

    def broken():
        raise RuntimeError("no key")

    monkeypatch.setattr(provider_warmup, "_build_clients", broken)
    assert await provider_warmup.warm_provider_clients() is None


# --- boot instrumentation -------------------------------------------------------


def test_boot_metrics_report_only_after_startup_completes(monkeypatch):
    from app.core import build_info

    monkeypatch.setattr(build_info, "_boot_complete_age", None)
    monkeypatch.setattr(build_info, "_import_complete_age", None)
    assert build_info.boot_seconds() is None
    assert build_info.import_phase_seconds() is None


def test_import_phase_excludes_the_database_schema_check(monkeypatch):
    """app_import_seconds ends when lifespan begins; boot_seconds ends after the
    schema gate. The 4 s gap below is the schema check and must not be import."""
    from app.core import build_info

    monkeypatch.setattr(build_info, "_boot_complete_age", None)
    monkeypatch.setattr(build_info, "_import_complete_age", None)
    monkeypatch.setattr(build_info, "IMPORT_STARTED_AGE_SECONDS", 3.0)

    ages = iter([13.0, 17.26])  # import complete, then boot complete
    monkeypatch.setattr(build_info, "process_age_seconds", lambda: next(ages))

    build_info.mark_import_complete()
    assert build_info.boot_seconds() is None  # not booted yet
    assert build_info.import_phase_seconds() == 10.0

    build_info.mark_boot_complete()
    assert build_info.import_phase_seconds() == 10.0  # unchanged by the gate
    assert build_info.boot_seconds() == 17.3
    build_info.mark_import_complete()  # idempotent: first call wins
    assert build_info.import_phase_seconds() == 10.0


@pytest.mark.asyncio
async def test_lifespan_marks_import_before_and_boot_after_the_schema_gate(monkeypatch):
    from app import main as main_module
    from app.core import build_info, schema_gate
    from app.services import provider_warmup

    order: list[str] = []
    monkeypatch.setattr(main_module, "mark_import_complete", lambda: order.append("import"))
    monkeypatch.setattr(main_module, "mark_boot_complete", lambda: order.append("boot"))

    async def gate(_engine):
        order.append("gate")

    async def no_warmup():
        order.append("warmup")

    monkeypatch.setattr(schema_gate, "check_schema", gate)
    monkeypatch.setattr(provider_warmup, "warm_provider_clients", no_warmup)
    monkeypatch.setattr(build_info, "_boot_complete_age", None)

    async with main_module.lifespan(main_module.app):
        pass
    assert order[:3] == ["import", "gate", "boot"]


# --- exactly-once, thread-safe client construction ------------------------------


def test_lazy_attribute_builds_once_under_concurrent_first_access():
    import threading
    import time

    from app.core.lazy import LazyAttribute

    built: list[int] = []

    class Holder:
        @LazyAttribute
        def client(self):
            built.append(1)
            time.sleep(0.15)  # slow enough for every thread to miss together
            return object()

    holder = Holder()
    results: list[object] = []
    barrier = threading.Barrier(8)

    def reader():
        barrier.wait()
        results.append(holder.client)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1
    assert len({id(r) for r in results}) == 1


def test_lazy_attribute_is_assignable_and_class_access_returns_the_descriptor():
    from app.core.lazy import LazyAttribute

    class Holder:
        @LazyAttribute
        def client(self):
            raise AssertionError("must not be built once assigned")

    assert isinstance(Holder.client, LazyAttribute)
    holder = Holder()
    fake = object()
    holder.client = fake
    assert holder.client is fake


@pytest.mark.asyncio
async def test_first_request_during_warmup_shares_one_client_per_provider(monkeypatch):
    """The warm-up thread and an early request race for the same clients."""
    import asyncio
    import sys
    import threading
    import time
    import types

    from app.services import provider_warmup
    from app.services.agent_workflow_service import AgentWorkflowService
    from app.services.embedding_service import EmbeddingService
    from app.services.rag_service import RAGService

    constructed = {"chat": 0, "embeddings": 0}
    lock = threading.Lock()

    class SlowChat:
        def __init__(self, **kwargs):
            with lock:
                constructed["chat"] += 1
            time.sleep(0.2)

        def with_structured_output(self, *_a, **_k):
            return self

    class SlowEmbeddings:
        def __init__(self, **kwargs):
            with lock:
                constructed["embeddings"] += 1
            time.sleep(0.2)

    stub = types.SimpleNamespace(ChatOpenAI=SlowChat, OpenAIEmbeddings=SlowEmbeddings)
    monkeypatch.setitem(sys.modules, "langchain_openai", stub)

    rag, embeddings, workflow = RAGService(), EmbeddingService(), AgentWorkflowService()
    import app.services.agent_workflow_service as awf
    import app.services.embedding_service as emb
    import app.services.rag_service as rag_mod

    monkeypatch.setattr(rag_mod, "rag_service", rag)
    monkeypatch.setattr(emb, "embedding_service", embeddings)
    monkeypatch.setattr(awf, "agent_workflow_service", workflow)

    warm = asyncio.create_task(provider_warmup.warm_provider_clients())
    await asyncio.sleep(0.05)  # warm-up thread is mid-construction

    early = await asyncio.gather(
        asyncio.to_thread(lambda: rag.llm),
        asyncio.to_thread(lambda: embeddings.client),
        asyncio.to_thread(lambda: workflow.decision_llm),
        asyncio.to_thread(lambda: rag.llm),
    )
    assert await warm is not None
    assert constructed == {"chat": 2, "embeddings": 1}  # llm + decision_llm, one each
    assert early[0] is early[3] is rag.llm


def test_process_age_is_a_small_non_negative_number_or_unsupported():
    from app.core import build_info

    age = build_info.process_age_seconds()
    assert age is None or 0 <= age < 3600  # None on platforms without /proc


# --- fail closed: nothing binds when configuration or the database is wrong ----


@pytest.mark.asyncio
async def test_lifespan_refuses_to_serve_when_the_schema_gate_fails(monkeypatch):
    from app import main as main_module
    from app.core import schema_gate

    async def refuse(_engine):
        raise schema_gate.SchemaBehindError("behind")

    warmed = []

    async def never_called():
        warmed.append(1)

    monkeypatch.setattr(schema_gate, "check_schema", refuse)
    from app.services import provider_warmup

    monkeypatch.setattr(provider_warmup, "warm_provider_clients", never_called)

    with pytest.raises(schema_gate.SchemaBehindError):
        async with main_module.lifespan(main_module.app):
            pytest.fail("the application must not start serving")
    assert warmed == []


def test_start_script_refuses_to_launch_on_an_invalid_production_environment():
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        "ENVIRONMENT": "production",
        "DEBUG": "true",
        "AUTH_DEV_MODE": "true",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "PORT": "1",
    }
    env.pop("ENCRYPTION_KEYS", None)
    proc = subprocess.run(
        ["sh", str(REPO_ROOT / "scripts" / "start_render_api.sh")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "refusing to start" in combined
    assert "Starting CXOps API" not in combined  # never reached the launch line
    for failing in ("DEBUG", "AUTH_DEV_MODE", "ENCRYPTION_KEYS", "DATABASE_URL"):
        assert failing in combined, failing


def test_benchmark_script_runs_and_reports_the_sdk_is_not_imported():
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.benchmark_startup", "--runs", "1", "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    import json

    report = json.loads(proc.stdout)
    assert report["openai_loaded_at_import"] is False
    assert report["import_seconds"]["median"] > 0
