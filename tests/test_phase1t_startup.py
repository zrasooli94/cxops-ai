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
    assert build_info.boot_seconds() is None
    assert build_info.import_phase_seconds() is None

    monkeypatch.setattr(build_info, "process_age_seconds", lambda: 41.26)
    monkeypatch.setattr(build_info, "IMPORT_STARTED_AGE_SECONDS", 3.0)
    build_info.mark_boot_complete()
    assert build_info.boot_seconds() == 41.3
    assert build_info.import_phase_seconds() == 38.3


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
