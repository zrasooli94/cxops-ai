"""Build the owner-only standalone review-supervisor runtime."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/standalone_review_hold_supervisor.py"
BOUND_PATHS = (
    "scripts/standalone_review_hold_supervisor.py",
    "scripts/package_review_hold_supervisor.py",
    "scripts/run_controlled_chat_model_evaluation.py",
    "app/services/model_eval_review_hold_lease.py",
    "app/services/model_eval_review_hold_supervisor.py",
)
LABEL = "com.cxops.model-eval-review-supervisor"


def _reviewed_commit() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *BOUND_PATHS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit("refusing to package uncommitted supervisor-bound source")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--hold-directory", type=Path, default=Path("/tmp/cxops-model-eval-review-holds"))
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", type=int, default=55440)
    parser.add_argument("--db-name", default="cxops_owner_review_disposable_test")
    parser.add_argument("--db-user", default=getpass.getuser())
    parser.add_argument("--backend-close-url", default="http://127.0.0.1:8000/public/chat/sessions/close")
    parser.add_argument("--psql", type=Path, default=Path("/opt/homebrew/bin/psql"))
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    if destination.is_symlink() or not stat.S_ISDIR(destination.lstat().st_mode):
        raise SystemExit("destination must be a real directory")
    runtime_source = destination / "supervisor.py"
    shutil.copyfile(SOURCE, runtime_source, follow_symlinks=False)
    os.chmod(runtime_source, 0o600)
    source_hash = hashlib.sha256(runtime_source.read_bytes()).hexdigest()
    config = {
        "package_version": "cxops-review-supervisor-v1",
        "runtime_source_sha256": source_hash,
        "reviewed_source_commit": _reviewed_commit(),
        "environment": "development",
        "provider_enabled": False,
        "hold_directory": str(args.hold_directory),
        "db_host": args.db_host,
        "db_port": args.db_port,
        "db_name": args.db_name,
        "db_user": args.db_user,
        "backend_close_url": args.backend_close_url,
        "psql_path": str(args.psql),
        "psql_realpath": str(args.psql.resolve()),
        "psql_sha256": hashlib.sha256(args.psql.resolve().read_bytes()).hexdigest(),
    }
    config_path = destination / "config.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    os.chmod(config_path, 0o600)
    logs = destination / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    os.chmod(logs, 0o700)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/python3",
            str(runtime_source),
            "--config",
            str(config_path),
            "--poll-seconds",
            "2",
        ],
        "WorkingDirectory": str(destination),
        "EnvironmentVariables": {
            "MODEL_EVAL_OPENAI_SOURCE_ENABLED": "false",
            "PUBLIC_CHAT_MODEL_EVAL_ENABLED": "false",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "supervisor.out.log"),
        "StandardErrorPath": str(logs / "supervisor.err.log"),
    }
    plist_path = destination / f"{LABEL}.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=True)
    os.chmod(plist_path, 0o600)
    print(json.dumps({"destination": str(destination), "reviewed_source_commit": config["reviewed_source_commit"], "runtime_source_sha256": source_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
