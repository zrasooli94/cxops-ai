"""Provider-disabled local monitor for prepared evaluation review holds."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import stat
import subprocess
from pathlib import Path

from app.core.config import settings
from app.services.model_eval_review_hold_lease import acquire_review_hold_lease
from app.services.model_eval_review_hold_supervisor import ReviewHoldSupervisor

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOLD_DIRECTORY = Path("/tmp/cxops-model-eval-review-holds")
SOURCE_BOUND_PATHS = (
    "scripts/run_controlled_chat_model_evaluation.py",
    "scripts/monitor_model_eval_review_holds.py",
    "app/services/model_eval_review_hold_lease.py",
    "app/services/model_eval_review_hold_supervisor.py",
)


def _source_commit() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    source_diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *SOURCE_BOUND_PATHS],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *SOURCE_BOUND_PATHS],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if source_diff.returncode != 0 or untracked:
        return f"dirty:{commit}"
    return commit


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--hold-directory", type=Path, default=DEFAULT_HOLD_DIRECTORY)
    return parser.parse_args()


def _require_provider_disabled() -> None:
    if settings.environment != "development":
        raise RuntimeError("review-hold monitor is development-only")
    if settings.model_eval_openai_source_enabled:
        raise RuntimeError("model evaluation provider flag must remain disabled")
    if settings.public_chat_model_eval_enabled:
        raise RuntimeError("public-chat model evaluation flag must remain disabled")
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("EVALUATION_OPENAI_API_KEY"):
        raise RuntimeError("provider credentials are not accepted by the monitor")


async def _run(arguments: argparse.Namespace) -> int:
    _require_provider_disabled()
    try:
        arguments.hold_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        info = arguments.hold_directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or arguments.hold_directory.is_symlink()
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise RuntimeError("review-hold directory is unsafe") from None
    directory_fd = os.open(
        arguments.hold_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    singleton = acquire_review_hold_lease(
        directory_fd, "provider-disabled-supervisor", blocking=False
    )
    os.close(directory_fd)
    if singleton is None:
        raise RuntimeError("another review-hold monitor is active")
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(signal_number, stopping.set)
    supervisor = ReviewHoldSupervisor(
        arguments.hold_directory, current_source_commit=_source_commit
    )
    try:
        while True:
            events = await supervisor.reconcile_once()
            print(
                json.dumps(
                    {
                        "mode": "provider_disabled_review_hold_supervisor",
                        "events": [
                            {
                                "digest": event.digest,
                                "status": event.status,
                                "cleanup_failure_count": len(event.cleanup_failures),
                            }
                            for event in events
                        ],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if arguments.once:
                return 0
            try:
                await asyncio.wait_for(stopping.wait(), timeout=arguments.poll_seconds)
            except TimeoutError:
                continue
            return 0
    finally:
        singleton.close()


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
