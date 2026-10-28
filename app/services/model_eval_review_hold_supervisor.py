"""Provider-disabled reconciliation for local evaluation review holds.

This module intentionally has no provider, HTTP, credential, or evaluation
execution imports.  It can only invalidate a hold and close its exact sessions.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.services.model_eval_review_hold_lease import acquire_review_hold_lease
from app.services.public_chat_service import PublicChatService

HOLD_VERSION = 1
EXPECTED_BINDING_COUNT = 10


class UnsafeReviewHold(RuntimeError):
    pass


@dataclass(frozen=True)
class HeldSession:
    fixture_conversation_id: str
    session_id: int
    token: str


@dataclass(frozen=True)
class ParsedHold:
    path: Path
    payload: dict[str, Any]
    digest: str
    organization_id: int
    run_id: str
    expires_at: datetime
    bindings: tuple[HeldSession, ...]


@dataclass(frozen=True)
class ReconcileEvent:
    digest: str
    status: str
    cleanup_failures: tuple[int, ...] = ()


def _canonical_digest(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_hold(path: Path) -> ParsedHold:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise UnsafeReviewHold("hold file ownership or permissions are unsafe")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    payload = json.loads(data)
    if payload.get("version") != HOLD_VERSION:
        raise UnsafeReviewHold("hold version is unsupported")
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        raise UnsafeReviewHold("hold manifest is missing")
    digest = _canonical_digest(manifest)
    stored_digest = str(payload.get("manifest_digest", ""))
    base_name = path.name
    for suffix in (".cleanup", ".claimed"):
        base_name = base_name.removesuffix(suffix)
    base_name = base_name.removeprefix(".").removesuffix(".json")
    if stored_digest != digest or base_name != digest:
        raise UnsafeReviewHold("hold digest is invalid")
    bindings = tuple(
        HeldSession(
            fixture_conversation_id=str(item["fixture_conversation_id"]),
            session_id=int(item["session_id"]),
            token=str(item["token"]),
        )
        for item in payload.get("bindings", [])
    )
    if (
        len(bindings) != EXPECTED_BINDING_COUNT
        or len({binding.session_id for binding in bindings}) != EXPECTED_BINDING_COUNT
        or any(not binding.token for binding in bindings)
    ):
        raise UnsafeReviewHold("hold session bindings are invalid")
    organization_id = int(manifest["organization_id"])
    run_id = str(manifest["run_id"])
    if (
        int(payload.get("organization_id", -1)) != organization_id
        or str(payload.get("run_id", "")) != run_id
        or tuple(manifest.get("public_chat_session_ids", ()))
        != tuple(binding.session_id for binding in bindings)
    ):
        raise UnsafeReviewHold("hold tenant, run, or session binding drifted")
    expiry = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    manifest_expiry = datetime.fromisoformat(
        str(manifest.get("expires_at", "")).replace("Z", "+00:00")
    )
    if manifest_expiry.tzinfo is None:
        manifest_expiry = manifest_expiry.replace(tzinfo=UTC)
    if manifest_expiry.astimezone(UTC) != expiry.astimezone(UTC):
        raise UnsafeReviewHold("hold expiry drifted from the immutable manifest")
    return ParsedHold(
        path=path,
        payload=payload,
        digest=digest,
        organization_id=organization_id,
        run_id=run_id,
        expires_at=expiry.astimezone(UTC),
        bindings=bindings,
    )


class ReviewHoldSupervisor:
    def __init__(
        self,
        directory: Path,
        *,
        current_source_commit: Callable[[], str],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.directory = directory
        self.current_source_commit = current_source_commit
        self.now = now

    def _safe_directory_fd(self) -> int:
        info = self.directory.lstat()
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise UnsafeReviewHold("review hold directory is unsafe")
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise UnsafeReviewHold("review hold directory permissions are unsafe")
        descriptor = os.open(
            self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != info.st_dev
            or opened.st_ino != info.st_ino
            or opened.st_uid != os.getuid()
            or opened.st_mode & 0o077
        ):
            os.close(descriptor)
            raise UnsafeReviewHold("review hold directory changed while opening")
        return descriptor

    async def _latest_decision(self, hold: ParsedHold) -> str | None:
        async with AsyncSessionLocal() as db:
            return await db.scalar(
                select(AIEvaluationReleaseDecision.decision)
                .join(
                    AIEvaluationRun,
                    (AIEvaluationRun.id == AIEvaluationReleaseDecision.candidate_run_id)
                    & (AIEvaluationRun.organization_id == hold.organization_id),
                )
                .where(
                    AIEvaluationRun.run_id == hold.run_id,
                    AIEvaluationRun.organization_id == hold.organization_id,
                    AIEvaluationReleaseDecision.organization_id
                    == hold.organization_id,
                )
                .order_by(
                    AIEvaluationReleaseDecision.created_at.desc(),
                    AIEvaluationReleaseDecision.id.desc(),
                )
                .limit(1)
            )

    async def _close_exact_sessions(self, hold: ParsedHold) -> tuple[int, ...]:
        failures: list[int] = []
        async with AsyncSessionLocal() as db:
            for binding in hold.bindings:
                try:
                    session = await PublicChatService.verify_session_for_close(
                        db, binding.token
                    )
                    if (
                        session.id != binding.session_id
                        or session.organization_id != hold.organization_id
                    ):
                        failures.append(binding.session_id)
                        continue
                    if session.status != "closed":
                        await PublicChatService.close_session(db, session)
                    await db.commit()
                except Exception:  # noqa: BLE001 - durable cleanup must retry later
                    await db.rollback()
                    failures.append(binding.session_id)
        return tuple(failures)

    def _remove_auxiliary(self, directory_fd: int, digest: str) -> None:
        names = (
            f".{digest}.json.claim-ack",
            f".{digest}.json.claim-lock",
        )
        for name in names:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

    async def _reconcile_cleanup(
        self, path: Path, *, lease_already_owned: bool = False
    ) -> ReconcileEvent:
        hold = _parse_hold(path)
        directory_fd = self._safe_directory_fd()
        lease = None
        try:
            if not lease_already_owned:
                lease = acquire_review_hold_lease(directory_fd, hold.digest)
                if lease is None:
                    return ReconcileEvent(hold.digest, "cleanup_in_progress")
            failures = await self._close_exact_sessions(hold)
            if failures:
                return ReconcileEvent(hold.digest, "cleanup_pending", failures)
            try:
                os.unlink(path.name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            self._remove_auxiliary(directory_fd, hold.digest)
        finally:
            if lease is not None:
                lease.close()
            os.close(directory_fd)
        return ReconcileEvent(hold.digest, "cleanup_complete")

    async def _transition_to_cleanup(
        self, path: Path, *, reason: str
    ) -> ReconcileEvent:
        hold = _parse_hold(path)
        directory_fd = self._safe_directory_fd()
        lease = None
        cleanup = self.directory / f".{hold.digest}.json.cleanup"
        try:
            lease = acquire_review_hold_lease(directory_fd, hold.digest)
            if lease is None:
                return ReconcileEvent(hold.digest, "live_claim_active")
            try:
                os.rename(
                    path.name,
                    cleanup.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return ReconcileEvent(hold.digest, "changed_concurrently")
            try:
                event = await self._reconcile_cleanup(
                    cleanup, lease_already_owned=True
                )
            except Exception:  # noqa: BLE001 - retain the durable cleanup file
                event = ReconcileEvent(hold.digest, "cleanup_pending")
            return ReconcileEvent(
                event.digest, f"{reason}_{event.status}", event.cleanup_failures
            )
        finally:
            if lease is not None:
                lease.close()
            os.close(directory_fd)

    async def reconcile_once(self) -> list[ReconcileEvent]:
        if not self.directory.exists():
            return []
        directory_fd = self._safe_directory_fd()
        os.close(directory_fd)
        events: list[ReconcileEvent] = []
        cleanup_paths = sorted(self.directory.glob(".*.json.cleanup"))
        for path in cleanup_paths:
            try:
                events.append(await self._reconcile_cleanup(path))
            except Exception:  # noqa: BLE001 - retry cleanup on the next poll
                events.append(ReconcileEvent(path.name, "cleanup_pending"))
        candidates = sorted(self.directory.glob("*.json")) + sorted(
            self.directory.glob(".*.json.claimed")
        )
        for path in candidates:
            try:
                hold = _parse_hold(path)
                manifest = hold.payload["manifest"]
                reason: str | None = None
                if path.name.endswith(".claimed"):
                    # Ownership is decided by the lease, never by a database
                    # read that can fail while a legitimate runner is active.
                    reason = "abandoned_claim"
                elif self.now().astimezone(UTC) >= hold.expires_at:
                    reason = "expired"
                elif manifest.get("reviewed_source_commit") != self.current_source_commit():
                    reason = "source_drift"
                elif await self._latest_decision(hold) == "rejected":
                    reason = "rejected"
                if reason is not None:
                    events.append(await self._transition_to_cleanup(path, reason=reason))
                else:
                    events.append(ReconcileEvent(hold.digest, "active"))
            except Exception:  # noqa: BLE001 - one malformed hold must not stop others
                events.append(ReconcileEvent(path.name, "invalid_hold"))
        return events
