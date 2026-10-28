from __future__ import annotations

import ast
import hashlib
import json
import os
import plistlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import package_review_hold_supervisor as packager
from scripts import standalone_review_hold_supervisor as standalone


def _config(tmp_path: Path) -> dict:
    psql = tmp_path / "psql"
    psql.write_bytes(b"synthetic-psql")
    psql.chmod(0o700)
    return {
        "package_version": standalone.PACKAGE_VERSION,
        "runtime_source_sha256": hashlib.sha256(
            Path(standalone.__file__).read_bytes()
        ).hexdigest(),
        "reviewed_source_commit": "a" * 40,
        "environment": "development",
        "provider_enabled": False,
        "hold_directory": str(tmp_path / "holds"),
        "db_host": "127.0.0.1",
        "db_port": 55440,
        "db_name": "synthetic_disposable_test",
        "db_user": "synthetic",
        "backend_close_url": "http://127.0.0.1:8000/public/chat/sessions/close",
        "psql_path": str(psql),
        "psql_realpath": str(psql.resolve()),
        "psql_sha256": hashlib.sha256(psql.read_bytes()).hexdigest(),
    }


def _write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    return path


def _write_hold(directory: Path, *, reviewed_commit: str) -> tuple[Path, str]:
    directory.mkdir(mode=0o700)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    manifest = {
        "organization_id": 1,
        "run_id": "offline-rag-candidate",
        "reviewed_source_commit": reviewed_commit,
        "public_chat_session_ids": list(range(1, 11)),
        "expires_at": expiry.isoformat(),
    }
    digest = hashlib.sha256(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    payload = {
        "version": 1,
        "manifest": manifest,
        "manifest_digest": digest,
        "organization_id": 1,
        "run_id": "offline-rag-candidate",
        "expires_at": expiry.isoformat(),
        "bindings": [
            {
                "fixture_conversation_id": f"fixture-{number}",
                "session_id": number,
                "token": f"synthetic-token-{number}",
            }
            for number in range(1, 11)
        ],
    }
    path = directory / f"{digest}.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return path, digest


def test_config_integrity_version_and_loopback_are_fail_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = _write_config(tmp_path, config)
    assert standalone._load_config(path)["provider_enabled"] is False

    config["package_version"] = "wrong"
    path.write_text(json.dumps(config))
    with pytest.raises(standalone.SupervisorBlocked, match="version"):
        standalone._load_config(path)

    config = _config(tmp_path)
    config["runtime_source_sha256"] = "0" * 64
    path.write_text(json.dumps(config))
    with pytest.raises(standalone.SupervisorBlocked, match="source integrity"):
        standalone._load_config(path)

    config = _config(tmp_path)
    config["backend_close_url"] = "http://127.0.0.1:8000@evil.example/public/chat/sessions/close"
    path.write_text(json.dumps(config))
    with pytest.raises(standalone.SupervisorBlocked, match="loopback"):
        standalone._load_config(path)


def test_standalone_source_has_only_stdlib_and_no_provider_path() -> None:
    source_path = Path(standalone.__file__)
    source = source_path.read_text()
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= {
        "__future__",
        "argparse",
        "fcntl",
        "hashlib",
        "json",
        "os",
        "re",
        "signal",
        "stat",
        "subprocess",
        "sys",
        "urllib",
        "dataclasses",
        "datetime",
        "pathlib",
        "typing",
    }
    assert "api.openai.com" not in source
    assert "OPENAI_API_KEY" not in source
    assert "Documents/Codex" not in source


def test_source_mismatch_invalidates_before_cleanup(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    supervisor = standalone.StandaloneSupervisor(config)
    hold_path, digest = _write_hold(
        Path(config["hold_directory"]), reviewed_commit="b" * 40
    )
    monkeypatch.setattr(supervisor, "_cleanup", lambda *_args, **_kwargs: ())
    events = supervisor.reconcile_once()
    assert events == [
        {
            "digest": digest,
            "status": "source_drift_cleanup_complete",
            "cleanup_failure_count": 0,
        }
    ]
    assert not hold_path.exists()


def test_packager_emits_private_documents_independent_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "runtime"
    monkeypatch.setattr(packager, "_reviewed_commit", lambda: "c" * 40)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "package",
            "--destination",
            str(destination),
            "--psql",
            "/opt/homebrew/bin/psql",
        ],
    )
    assert packager.main() == 0
    config = json.loads((destination / "config.json").read_text())
    plist = plistlib.loads(
        (destination / f"{packager.LABEL}.plist").read_bytes()
    )
    assert config["reviewed_source_commit"] == "c" * 40
    assert config["provider_enabled"] is False
    assert plist["ProgramArguments"][0] == "/usr/bin/python3"
    rendered = json.dumps({"config": config, "plist": plist})
    assert "Documents/Codex" not in rendered
    for path in destination.rglob("*"):
        assert not path.is_symlink()
    assert stat_mode(destination) == 0o700
    assert stat_mode(destination / "config.json") == 0o600
    assert stat_mode(destination / "supervisor.py") == 0o600


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
