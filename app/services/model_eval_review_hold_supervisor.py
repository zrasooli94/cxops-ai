"""Provider-disabled reconciliation for local evaluation review holds.

This module intentionally has no provider, HTTP, credential, or evaluation
execution imports.  It can only invalidate a hold and close its exact sessions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
from app.models.public_chat import PublicChatSession
from app.services.model_eval_review_hold_lease import acquire_review_hold_lease
from app.services.public_chat_service import PublicChatService

HOLD_VERSION = 1
EXPECTED_BINDING_COUNT = 10
RUN_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
FIXTURE_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


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


def _positive_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 1:
        raise UnsafeReviewHold(f"{field} must be a positive integer")
    return value


def _strict_bindings(payload: dict[str, Any]) -> tuple[HeldSession, ...]:
    raw_bindings = payload.get("bindings")
    if not isinstance(raw_bindings, list) or len(raw_bindings) != EXPECTED_BINDING_COUNT:
        raise UnsafeReviewHold("hold session bindings are invalid")
    bindings: list[HeldSession] = []
    for raw in raw_bindings:
        if not isinstance(raw, dict) or set(raw) != {
            "fixture_conversation_id",
            "session_id",
            "token",
        }:
            raise UnsafeReviewHold("hold session bindings are invalid")
        fixture_id = raw["fixture_conversation_id"]
        token = raw["token"]
        if not isinstance(fixture_id, str) or not FIXTURE_CONVERSATION_ID.fullmatch(
            fixture_id
        ):
            raise UnsafeReviewHold("hold fixture identity is invalid")
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            raise UnsafeReviewHold("hold session token is invalid")
        bindings.append(
            HeldSession(
                fixture_conversation_id=fixture_id,
                session_id=_positive_int(raw["session_id"], field="session id"),
                token=token,
            )
        )
    if (
        len({binding.fixture_conversation_id for binding in bindings})
        != EXPECTED_BINDING_COUNT
        or len({binding.session_id for binding in bindings})
        != EXPECTED_BINDING_COUNT
    ):
        raise UnsafeReviewHold("hold session bindings are invalid")
    return tuple(bindings)


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
    bindings = _strict_bindings(payload)
    organization_id = _positive_int(
        payload.get("organization_id"), field="organization id"
    )
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        raise UnsafeReviewHold("hold run identity is invalid")
    session_ids = manifest.get("public_chat_session_ids")
    if (
        not isinstance(session_ids, list)
        or len(session_ids) != EXPECTED_BINDING_COUNT
        or any(type(item) is not int or item < 1 for item in session_ids)
        or len(set(session_ids)) != EXPECTED_BINDING_COUNT
        or tuple(session_ids) != tuple(binding.session_id for binding in bindings)
    ):
        raise UnsafeReviewHold("hold tenant, run, or session binding drifted")
    payload_expiry = payload.get("expires_at")
    manifest_expiry_value = manifest.get("expires_at")
    if not isinstance(payload_expiry, str) or not isinstance(
        manifest_expiry_value, str
    ):
        raise UnsafeReviewHold("hold expiry is invalid")
    expiry = datetime.fromisoformat(payload_expiry.replace("Z", "+00:00"))
    manifest_expiry = datetime.fromisoformat(
        manifest_expiry_value.replace("Z", "+00:00")
    )
    if (
        expiry.tzinfo is None
        or manifest_expiry.tzinfo is None
        or manifest_expiry.astimezone(UTC) != expiry.astimezone(UTC)
    ):
        raise UnsafeReviewHold("hold expiry drifted from the immutable manifest")
    reviewed_commit = manifest.get("reviewed_source_commit")
    if not isinstance(reviewed_commit, str) or re.fullmatch(
        r"[0-9a-f]{40}", reviewed_commit
    ) is None:
        raise UnsafeReviewHold("hold reviewed source identity is invalid")
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

    async def _verify_database_binding(self, hold: ParsedHold) -> None:
        expected_tokens = {
            binding.session_id: hashlib.sha256(binding.token.encode()).hexdigest()
            for binding in hold.bindings
        }
        async with AsyncSessionLocal() as db:
            run = await db.scalar(
                select(AIEvaluationRun).where(
                    AIEvaluationRun.organization_id == hold.organization_id,
                    AIEvaluationRun.run_id == hold.run_id,
                )
            )
            if run is None or run.status != "succeeded":
                raise UnsafeReviewHold("hold evaluation run binding is unavailable")
            result = await db.scalars(
                select(PublicChatSession).where(
                    PublicChatSession.organization_id == hold.organization_id,
                    PublicChatSession.id.in_(tuple(expected_tokens)),
                )
            )
            sessions = list(result.all())
        if len(sessions) != EXPECTED_BINDING_COUNT:
            raise UnsafeReviewHold("hold session database binding is unavailable")
        for session in sessions:
            if (
                expected_tokens.get(session.id) != session.token_hash
                or session.status != "ai_active"
                or session.expires_at.tzinfo is None
                or session.expires_at.astimezone(UTC) < hold.expires_at
            ):
                raise UnsafeReviewHold("hold session database binding is invalid")

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
                else:
                    await self._verify_database_binding(hold)
                    if await self._latest_decision(hold) == "rejected":
                        reason = "rejected"
                if reason is not None:
                    events.append(await self._transition_to_cleanup(path, reason=reason))
                else:
                    events.append(ReconcileEvent(hold.digest, "active"))
            except Exception:  # noqa: BLE001 - one malformed hold must not stop others
                events.append(ReconcileEvent(path.name, "invalid_hold"))
        return events
