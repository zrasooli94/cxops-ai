import json
from argparse import Namespace

import pytest

from app.core.config import settings
from scripts import run_offline_rag_prerequisite as command


@pytest.mark.asyncio
async def test_command_rejects_production_before_database_or_provider(monkeypatch, tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(SystemExit, match="development-only"):
        await command._run(
            Namespace(
                organization_id=1,
                subject="owner",
                cases=cases,
                mode="baseline",
                baseline_id=None,
            )
        )


def test_command_has_no_provider_import_or_credential_path():
    source = command.__file__
    text = open(source, encoding="utf-8").read()  # noqa: SIM115
    assert "model_eval_openai" not in text
    assert "httpx" not in text
    assert "api_key" not in text.casefold()
