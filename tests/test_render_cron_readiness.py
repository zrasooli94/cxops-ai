"""Static safety contract for the source-only Render Cron entrypoint."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "scripts" / "run_render_cron_worker.sh"


def _code() -> list[str]:
    lines: list[str] = []
    for raw in ENTRYPOINT.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def test_entrypoint_is_source_only_and_does_not_define_a_schedule() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "cron:" not in source
    assert "schedule:" not in source
    assert "render.yaml" not in source


def test_preflight_is_fatal_and_runs_before_worker() -> None:
    code = _code()
    preflight = next(i for i, line in enumerate(code) if "preflight" in line)
    worker = next(i for i, line in enumerate(code) if "scripts.worker" in line)
    assert preflight < worker
    assert code[preflight].startswith("if ! ")
    assert any(re.fullmatch(r"exit [1-9][0-9]*", line) for line in code)


def test_worker_command_is_bounded_and_fails_visible() -> None:
    command = " ".join(_code())
    for required in (
        "exec python -m scripts.worker",
        "--once",
        "--max-jobs 25",
        "--max-seconds 240",
        "--exit-nonzero-on-unsuccessful",
        "--finalize-expired-public-chat",
        "--expiry-batch-size 50",
    ):
        assert required in command


def test_entrypoint_never_migrates_or_embeds_credentials() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "alembic" not in source
    assert "run_migrations" not in source
    assert "DATABASE_URL=" not in source
    assert "OPENAI_API_KEY=" not in source
    assert "ENCRYPTION_KEYS=" not in source
