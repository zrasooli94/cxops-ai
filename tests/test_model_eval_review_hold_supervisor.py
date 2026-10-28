from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.model_eval_review_hold_lease import acquire_review_hold_lease
from app.services.model_eval_review_hold_supervisor import ReviewHoldSupervisor


def _write_hold(directory: Path, *, expired: bool = False, claimed: bool = False) -> tuple[Path, str]:
    expiry = datetime.now(UTC) + (-timedelta(minutes=1) if expired else timedelta(hours=1))
    manifest = {
        "organization_id": 1,
        "run_id": "offline-rag-candidate",
        "reviewed_source_commit": "reviewed-commit",
        "public_chat_session_ids": list(range(11, 21)),
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
                "fixture_conversation_id": f"conversation-{index}",
                "session_id": 10 + index,
                "token": f"synthetic-private-token-{index}",
            }
            for index in range(1, 11)
        ],
    }
    name = f".{digest}.json.claimed" if claimed else f"{digest}.json"
    path = directory / name
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return path, digest


def _supervisor(directory: Path) -> ReviewHoldSupervisor:
    return ReviewHoldSupervisor(
        directory, current_source_commit=lambda: "reviewed-commit"
    )


@pytest.mark.asyncio
async def test_expiry_becomes_invalid_before_cleanup_and_retries_db_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    original, digest = _write_hold(tmp_path, expired=True)
    supervisor = _supervisor(tmp_path)
    calls = 0

    async def unavailable(_hold):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic database outage")
        return ()

    monkeypatch.setattr(supervisor, "_close_exact_sessions", unavailable)
    first = await supervisor.reconcile_once()
    cleanup = tmp_path / f".{digest}.json.cleanup"
    assert not original.exists()
    assert cleanup.exists()
    assert any(event.status.endswith("cleanup_pending") for event in first)

    second = await supervisor.reconcile_once()
    assert not cleanup.exists()
    assert any(event.status == "cleanup_complete" for event in second)


@pytest.mark.asyncio
async def test_claimed_hold_with_live_owner_is_not_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    claimed, digest = _write_hold(tmp_path, claimed=True)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    lease = acquire_review_hold_lease(directory_fd, digest)
    os.close(directory_fd)
    assert lease is not None
    supervisor = _supervisor(tmp_path)
    closed = 0

    async def close(_hold):
        nonlocal closed
        closed += 1
        return ()

    monkeypatch.setattr(supervisor, "_close_exact_sessions", close)
    try:
        events = await supervisor.reconcile_once()
        assert events[0].status == "live_claim_active"
        assert claimed.exists()
        assert closed == 0
    finally:
        lease.close()

    events = await supervisor.reconcile_once()
    assert events[0].status == "abandoned_claim_cleanup_complete"
    assert closed == 1
    assert not claimed.exists()


@pytest.mark.asyncio
async def test_concurrent_monitors_have_one_cleanup_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    _write_hold(tmp_path, expired=True)
    first = _supervisor(tmp_path)
    second = _supervisor(tmp_path)
    cleaned = 0

    async def close(_hold):
        nonlocal cleaned
        await asyncio.sleep(0)
        cleaned += 1
        return ()

    monkeypatch.setattr(first, "_close_exact_sessions", close)
    monkeypatch.setattr(second, "_close_exact_sessions", close)
    await asyncio.gather(first.reconcile_once(), second.reconcile_once())
    assert cleaned == 1
    assert not list(tmp_path.glob("*.cleanup"))


@pytest.mark.asyncio
async def test_rejection_and_source_drift_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    _write_hold(tmp_path)
    supervisor = _supervisor(tmp_path)

    async def rejected(_hold):
        return "rejected"

    async def close(_hold):
        return ()

    monkeypatch.setattr(supervisor, "_latest_decision", rejected)
    monkeypatch.setattr(supervisor, "_close_exact_sessions", close)
    events = await supervisor.reconcile_once()
    assert events[0].status == "rejected_cleanup_complete"

    _write_hold(tmp_path)
    drifted = ReviewHoldSupervisor(tmp_path, current_source_commit=lambda: "changed")
    monkeypatch.setattr(drifted, "_close_exact_sessions", close)
    events = await drifted.reconcile_once()
    assert events[0].status == "source_drift_cleanup_complete"


def test_monitor_source_has_no_provider_or_secret_construction() -> None:
    source = Path("scripts/monitor_model_eval_review_holds.py").read_text()
    forbidden = (
        "httpx",
        "operator_entered_api_key",
        "installed_development_evaluation_provider",
        "NoRetryHttpClient",
        "OPENAI_API_KEY]",
    )
    assert not any(item in source for item in forbidden)


def test_persistent_lease_path_prevents_third_inode_acquisition(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    first = acquire_review_hold_lease(directory_fd, "manifest")
    assert first is not None
    first.close()
    second = acquire_review_hold_lease(directory_fd, "manifest")
    assert second is not None
    third = acquire_review_hold_lease(directory_fd, "manifest")
    try:
        assert third is None
        assert (tmp_path / ".manifest.owner-lock").exists()
    finally:
        second.close()
        os.close(directory_fd)


@pytest.mark.asyncio
async def test_session_binding_drift_is_rejected_without_cleanup(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    path, _digest = _write_hold(tmp_path, expired=True)
    payload = json.loads(path.read_text())
    payload["bindings"][0]["session_id"] = 999
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    events = await _supervisor(tmp_path).reconcile_once()
    assert events[0].status == "invalid_hold"
    assert path.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_remains_durable_across_supervisor_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    _write_hold(tmp_path, expired=True)
    first = _supervisor(tmp_path)

    async def partial_failure(_hold):
        return (17,)

    monkeypatch.setattr(first, "_close_exact_sessions", partial_failure)
    events = await first.reconcile_once()
    assert events[0].status == "expired_cleanup_pending"
    assert events[0].cleanup_failures == (17,)
    assert len(list(tmp_path.glob(".*.json.cleanup"))) == 1

    restarted = _supervisor(tmp_path)

    async def recovered(_hold):
        return ()

    monkeypatch.setattr(restarted, "_close_exact_sessions", recovered)
    events = await restarted.reconcile_once()
    assert events[0].status == "cleanup_complete"
    assert not list(tmp_path.glob(".*.json.cleanup"))


@pytest.mark.asyncio
async def test_interruption_during_cleanup_preserves_durable_pending_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    original, digest = _write_hold(tmp_path, expired=True)
    supervisor = _supervisor(tmp_path)
    started = asyncio.Event()

    async def interrupted(_hold):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(supervisor, "_close_exact_sessions", interrupted)
    task = asyncio.create_task(supervisor.reconcile_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not original.exists()
    assert (tmp_path / f".{digest}.json.cleanup").exists()


@pytest.mark.asyncio
async def test_claim_ack_equivalent_live_lease_is_not_cleanup_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    claimed, digest = _write_hold(tmp_path, claimed=True)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    live = acquire_review_hold_lease(directory_fd, digest)
    os.close(directory_fd)
    assert live is not None
    supervisor = _supervisor(tmp_path)

    async def must_not_close(_hold):
        raise AssertionError("live claimed sessions must not be cleaned")

    monkeypatch.setattr(supervisor, "_close_exact_sessions", must_not_close)
    events = await supervisor.reconcile_once()
    try:
        assert events[0].status == "live_claim_active"
    finally:
        live.close()
    assert claimed.exists()


@pytest.mark.asyncio
async def test_supervisor_rejects_symlinked_hold_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    supervisor = _supervisor(linked)
    with pytest.raises(Exception, match="directory is unsafe"):
        await supervisor.reconcile_once()
