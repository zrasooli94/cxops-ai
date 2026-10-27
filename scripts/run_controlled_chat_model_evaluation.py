"""Run the frozen bilingual evaluation through the local public-chat service.

The default command stops after creating ten fresh synthetic sessions and
printing the exact activation manifest.  A live continuation is possible only
with ``--execute-live`` after an authenticated manager records that unchanged
manifest.  The restricted key is then read once from a masked TTY prompt and is
never accepted via argv, environment, or a file.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.ai_evaluation_case import AIEvaluationCase
from app.models.ai_evaluation_live_authorization import AIEvaluationLiveAuthorization
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.ai_request_log import AIRequestLog
from app.models.ai_usage_reservation import AIUsageReservation
from app.services.model_eval_activation_plan import OperatorActivationPlan
from app.services.model_eval_billing_evidence import require_reviewed_billing_evidence
from app.services.model_eval_conditional_billing import (
    ALL_IN_EVIDENCE_ID,
    CONDITIONAL_BILLING_POLICY,
    CONDITIONAL_BILLING_POLICY_VERSION,
    COUNT_EVIDENCE_ID,
    OWNER_ACCEPTANCE_STATEMENT,
    conditional_acceptance_request,
    require_conditional_billing_acceptance,
)
from app.services.model_eval_controller import (
    EvaluationBlockedError,
    EvaluationPolicy,
    InputTokenCountPolicy,
)
from app.services.model_eval_development_lifecycle import (
    installed_development_evaluation_provider,
)
from app.services.model_eval_live_authorization import (
    LIVE_AUTHORIZATION_STATEMENT,
    live_authorization_request,
    validate_live_authorization_snapshot,
)
from app.services.model_eval_operator_secret import operator_entered_api_key
from app.services.public_chat_model_eval import (
    PersistedEvaluationActivationGuard,
    validate_activation_manifest,
)
from app.services.public_chat_service import PublicChatService
from app.services.tenant_ai_allowance_service import STATUS_RESERVED
from scripts.plan_chat_model_evaluation import expanded_turns, validate_mechanical_spec


class RunnerBlocked(RuntimeError):
    pass


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_SPEC_PATH = REPOSITORY_ROOT / "scripts/plan_chat_model_evaluation.py"
RUNNER_SOURCE_PATH = Path(__file__).resolve()
REVIEW_HOLD_DIRECTORY = Path("/tmp/cxops-model-eval-review-holds")
REVIEW_HOLD_VERSION = 1
SOURCE_BOUND_PATHS = (
    "alembic/versions/1q1a0001_add_eval_live_authorizations.py",
    "scripts/run_controlled_chat_model_evaluation.py",
    "scripts/plan_chat_model_evaluation.py",
    "app/api/routes/evaluations.py",
    "app/schemas/evaluation.py",
    "app/services/ai_evaluation_service.py",
    "app/models/ai_evaluation_live_authorization.py",
    "app/repositories/ai_evaluation_repository.py",
    "app/services/model_eval_activation_plan.py",
    "app/services/model_eval_billing_evidence.py",
    "app/services/model_eval_conditional_billing.py",
    "app/services/model_eval_controller.py",
    "app/services/model_eval_development_lifecycle.py",
    "app/services/model_eval_live_authorization.py",
    "app/services/model_eval_openai_adapter.py",
    "app/services/model_eval_openai_http_transport.py",
    "app/services/model_eval_operator_secret.py",
    "app/services/public_chat_model_eval.py",
    "app/services/public_chat_service.py",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_identity() -> dict[str, str]:
    fixture = json.dumps(
        expanded_turns(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
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
    if source_diff.returncode != 0:
        raise RunnerBlocked(
            "evaluation source differs from the reviewed Git commit"
        )
    return {
        "frozen_fixture_sha256": hashlib.sha256(fixture.encode()).hexdigest(),
        "evaluation_spec_sha256": _sha256_file(EVALUATION_SPEC_PATH),
        "runner_source_sha256": _sha256_file(RUNNER_SOURCE_PATH),
        "reviewed_source_commit": commit,
    }


@dataclass(frozen=True)
class SessionBinding:
    fixture_conversation_id: str
    session_id: int
    token: str


class NoRetryHttpClient:
    """One-shot HTTP client contract used by the reviewed development source."""

    max_retries = 0

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(follow_redirects=False, timeout=30.0)

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        return await self._client.post(
            url, json=json, headers=headers, timeout=timeout, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--widget-key", required=True)
    parser.add_argument("--embedding-origin", default="http://127.0.0.1:3000")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--count-evidence-id", required=True)
    parser.add_argument("--count-evidence-expires-at", required=True)
    parser.add_argument("--all-in-evidence-id", required=True)
    parser.add_argument("--all-in-evidence-expires-at", required=True)
    parser.add_argument("--additional-all-in-reserve-usd", type=float, required=True)
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="continue after authenticated approval and prompt for the key",
    )
    parser.add_argument(
        "--prepare-review-hold",
        action="store_true",
        help="create an expiring provider-disabled manifest and hold its sessions",
    )
    parser.add_argument(
        "--resume-review-hold",
        type=Path,
        help="claim an exact previously prepared hold for a separately authorized run",
    )
    return parser.parse_args()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RunnerBlocked("timestamps must include a timezone")
    return parsed.astimezone(UTC)


async def _create_sessions(arguments: argparse.Namespace) -> list[SessionBinding]:
    turns = expanded_turns()
    validate_mechanical_spec(turns)
    conversation_ids = list(dict.fromkeys(turn["conversation_id"] for turn in turns))
    bindings: list[SessionBinding] = []
    async with AsyncSessionLocal() as db:
        for conversation_id in conversation_ids:
            created = await PublicChatService.create_session(
                db, arguments.widget_key, arguments.embedding_origin
            )
            bindings.append(
                SessionBinding(
                    fixture_conversation_id=conversation_id,
                    session_id=created["session"].id,
                    token=created["token"],
                )
            )
    return bindings


async def _close_sessions(bindings: list[SessionBinding]) -> list[int]:
    """Best-effort local cleanup without ever printing retained session tokens."""
    failures: list[int] = []
    for binding in bindings:
        try:
            async with AsyncSessionLocal() as db:
                session = await PublicChatService.verify_session_for_close(
                    db, binding.token
                )
                await PublicChatService.close_session(db, session)
        except Exception:  # noqa: BLE001 - preserve the original stop outcome
            failures.append(binding.session_id)
            continue
    return failures


def _canonical_manifest_digest(manifest: dict[str, Any]) -> str:
    payload = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _review_hold_path(manifest_digest: str) -> Path:
    return REVIEW_HOLD_DIRECTORY / f"{manifest_digest}.json"


def _open_review_hold_directory(*, create: bool) -> int:
    if create:
        try:
            os.mkdir(REVIEW_HOLD_DIRECTORY, 0o700)
        except FileExistsError:
            pass
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(REVIEW_HOLD_DIRECTORY, flags)
    except OSError as exc:
        raise RunnerBlocked("review hold directory is unavailable or unsafe") from exc
    details = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise RunnerBlocked("review hold directory ownership or permissions are invalid")
    return descriptor


def _claimed_hold_name(path: Path) -> str:
    return f".{path.name}.claimed"


def _claim_lock_name(path: Path) -> str:
    return f".{path.name}.claim-lock"


def _claim_ack_name(path: Path) -> str:
    return f".{path.name}.claim-ack"


def _remove_review_hold_file(path: Path) -> None:
    descriptor = _open_review_hold_directory(create=False)
    try:
        os.unlink(path.name, dir_fd=descriptor)
    except FileNotFoundError:
        pass
    finally:
        os.close(descriptor)


def _read_review_hold_file(path: Path) -> bytes:
    descriptor = _open_review_hold_directory(create=False)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(path.name, flags, dir_fd=descriptor)
        try:
            details = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                raise RunnerBlocked(
                    "review hold ownership or permissions are invalid"
                )
            with os.fdopen(file_descriptor, "rb") as stream:
                file_descriptor = -1
                return stream.read()
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
    finally:
        os.close(descriptor)


def _acknowledge_review_hold_claim(claimed: Path) -> None:
    """Complete ownership transfer only after the guarded runner lifecycle starts."""
    state_sha256 = hashlib.sha256(_read_review_hold_file(claimed)).hexdigest()
    directory_descriptor = _open_review_hold_directory(create=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(
        _claim_ack_name(claimed.with_name(claimed.name.removeprefix(".").removesuffix(".claimed"))),
        flags,
        0o600,
        dir_fd=directory_descriptor,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(state_sha256.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(directory_descriptor)


def _write_review_hold(
    *,
    arguments: argparse.Namespace,
    bindings: list[SessionBinding],
    manifest: dict[str, Any],
) -> tuple[Path, str]:
    """Persist only the short-lived local session capability needed to resume.

    The path and manifest are safe to print; raw session tokens stay in the
    owner-only file and are removed by claim, cancellation, or expiry cleanup.
    """
    digest = _canonical_manifest_digest(manifest)
    path = _review_hold_path(digest)
    payload = {
        "version": REVIEW_HOLD_VERSION,
        "manifest_digest": digest,
        "manifest": manifest,
        "organization_id": arguments.organization_id,
        "run_id": arguments.run_id,
        "embedding_origin": arguments.embedding_origin,
        "expires_at": arguments.expires_at,
        "bindings": [
            {
                "fixture_conversation_id": item.fixture_conversation_id,
                "session_id": item.session_id,
                "token": item.token,
            }
            for item in bindings
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    directory_descriptor = _open_review_hold_directory(create=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_descriptor)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.unlink(path.name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(directory_descriptor)
    return path, hashlib.sha256(encoded).hexdigest()


def _load_claimed_review_hold(
    path: Path,
) -> tuple[Path, list[SessionBinding], dict[str, Any], dict[str, Any]]:
    """Atomically claim a hold so only one later process can resume it."""
    original_bytes = _read_review_hold_file(path)
    try:
        payload = json.loads(original_bytes)
        if payload.get("version") != REVIEW_HOLD_VERSION:
            raise RunnerBlocked("review hold version is unsupported")
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            raise RunnerBlocked("review hold manifest is missing")
        digest = _canonical_manifest_digest(manifest)
        if payload.get("manifest_digest") != digest or path.name != f"{digest}.json":
            raise RunnerBlocked("review hold manifest digest is invalid")
        expiry = _timestamp(str(payload.get("expires_at", "")))
        if expiry <= datetime.now(UTC):
            raise RunnerBlocked("review hold is expired")
        bindings = [
            SessionBinding(
                fixture_conversation_id=str(item["fixture_conversation_id"]),
                session_id=int(item["session_id"]),
                token=str(item["token"]),
            )
            for item in payload.get("bindings", [])
        ]
        if len(bindings) != 10 or any(not item.token for item in bindings):
            raise RunnerBlocked("review hold session binding is invalid")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerBlocked("review hold payload is invalid") from exc
    claimed = path.with_name(_claimed_hold_name(path))
    directory_descriptor = _open_review_hold_directory(create=False)
    lock_descriptor = -1
    try:
        lock_descriptor = os.open(
            _claim_lock_name(path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        try:
            os.stat(claimed.name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RunnerBlocked("review hold is already claimed")
        os.rename(
            path.name,
            claimed.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
    except FileExistsError:
        raise RunnerBlocked("review hold is already claimed") from None
    except FileNotFoundError:
        raise RunnerBlocked("review hold is unavailable") from None
    finally:
        if lock_descriptor >= 0:
            os.close(lock_descriptor)
            try:
                os.unlink(_claim_lock_name(path), dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        os.close(directory_descriptor)
    if _read_review_hold_file(claimed) != original_bytes:
        raise RunnerBlocked("review hold changed while being claimed")
    return claimed, bindings, manifest, payload


async def _latest_release_decision(
    *, organization_id: int, run_id: str
) -> str | None:
    async with AsyncSessionLocal() as db:
        return await db.scalar(
            select(AIEvaluationReleaseDecision.decision)
            .join(
                AIEvaluationRun,
                (AIEvaluationRun.id == AIEvaluationReleaseDecision.candidate_run_id)
                & (AIEvaluationRun.organization_id == organization_id),
            )
            .where(
                AIEvaluationRun.run_id == run_id,
                AIEvaluationRun.organization_id == organization_id,
                AIEvaluationReleaseDecision.organization_id == organization_id,
            )
            .order_by(
                AIEvaluationReleaseDecision.created_at.desc(),
                AIEvaluationReleaseDecision.id.desc(),
            )
            .limit(1)
        )


async def _wait_for_review_hold(
    *,
    path: Path,
    state_sha256: str,
    arguments: argparse.Namespace,
    poll_seconds: float = 1.0,
) -> str:
    """Hold until claimed, rejected, expired, or interrupted by the operator."""
    expiry = _timestamp(arguments.expires_at)
    while True:
        if (
            await _latest_release_decision(
                organization_id=arguments.organization_id,
                run_id=arguments.run_id,
            )
            == "rejected"
        ):
            return "rejected"
        try:
            _read_review_hold_file(path)
        except FileNotFoundError:
            claimed = path.with_name(_claimed_hold_name(path))
            try:
                claimed_bytes = _read_review_hold_file(claimed)
            except FileNotFoundError:
                return "cancelled_or_deleted"
            except RunnerBlocked:
                return "invalid_claim"
            ack = path.with_name(_claim_ack_name(path))
            try:
                ack_bytes = _read_review_hold_file(ack)
            except FileNotFoundError:
                if datetime.now(UTC) >= expiry:
                    return "expired"
                await asyncio.sleep(poll_seconds)
                continue
            except RunnerBlocked:
                return "invalid_claim"
            if hashlib.sha256(claimed_bytes).hexdigest() != state_sha256:
                return "invalid_claim"
            if ack_bytes != state_sha256.encode("ascii"):
                return "invalid_claim"
            return "claimed_for_separately_authorized_resume"
        except RunnerBlocked:
            return "invalid_hold"
        if datetime.now(UTC) >= expiry:
            return "expired"
        await asyncio.sleep(poll_seconds)


def _build_plan(
    arguments: argparse.Namespace, bindings: list[SessionBinding]
) -> tuple[dict[str, Any], InputTokenCountPolicy, EvaluationPolicy]:
    count_expiry = _timestamp(arguments.count_evidence_expires_at)
    all_in_expiry = _timestamp(arguments.all_in_evidence_expires_at)
    identity = _source_identity()
    if arguments.count_evidence_id != COUNT_EVIDENCE_ID:
        raise RunnerBlocked("count evidence does not match the conditional policy")
    if arguments.all_in_evidence_id != ALL_IN_EVIDENCE_ID:
        raise RunnerBlocked("all-in evidence does not match the conditional policy")
    if (
        arguments.additional_all_in_reserve_usd
        != CONDITIONAL_BILLING_POLICY.malaysia_tax_planning_allowance_usd
    ):
        raise RunnerBlocked("tax planning allowance does not match the conditional policy")
    plan = OperatorActivationPlan.build(
        environment=settings.environment,
        organization_id=arguments.organization_id,
        public_chat_session_ids=tuple(item.session_id for item in bindings),
        run_id=arguments.run_id,
        expires_at=_timestamp(arguments.expires_at),
        count_billing_status="unknown",
        count_cost_reservation_per_attempt_usd=(
            CONDITIONAL_BILLING_POLICY.count_reservation_per_attempt_usd
        ),
        count_billing_evidence_url="https://developers.openai.com/api/docs/guides/token-counting",
        count_billing_evidence_id=arguments.count_evidence_id,
        count_billing_evidence_expires_at=count_expiry,
        count_billing_independently_verified=False,
        additional_all_in_reserve_usd=arguments.additional_all_in_reserve_usd,
        all_in_evidence_id=arguments.all_in_evidence_id,
        all_in_evidence_expires_at=all_in_expiry,
        all_in_independently_verified=False,
        conditional_billing_policy_version=CONDITIONAL_BILLING_POLICY_VERSION,
        **identity,
    ).render()
    count_policy = InputTokenCountPolicy(
        endpoint=plan["activation_manifest"]["count_policy"]["endpoint"],
        model="gpt-6-luna",
        region="standard",
        reservation_per_attempt_usd=(
            CONDITIONAL_BILLING_POLICY.count_reservation_per_attempt_usd
        ),
        evidence_id=arguments.count_evidence_id,
        evidence_expires_at=count_expiry,
        billing_status="unknown",
        independently_verified=False,
        explicitly_approved=False,
        conditional_policy_version=CONDITIONAL_BILLING_POLICY_VERSION,
        conditional_evidence_sha256=(
            CONDITIONAL_BILLING_POLICY.support_evidence_sha256
        ),
    )
    policy = EvaluationPolicy(
        additional_all_in_reserve_usd=arguments.additional_all_in_reserve_usd
    )
    return plan, count_policy, policy


async def _require_approval(
    arguments: argparse.Namespace,
    bindings: list[SessionBinding],
    proposed_manifest: dict[str, Any],
) -> tuple[dict[str, Any], InputTokenCountPolicy, EvaluationPolicy]:
    async with AsyncSessionLocal() as db:
        decision = await db.scalar(
            select(AIEvaluationReleaseDecision)
            .join(
                AIEvaluationRun,
                (AIEvaluationRun.id == AIEvaluationReleaseDecision.candidate_run_id)
                & (
                    AIEvaluationRun.organization_id
                    == AIEvaluationReleaseDecision.organization_id
                ),
            )
            .where(
                AIEvaluationRun.run_id == arguments.run_id,
                AIEvaluationRun.organization_id == arguments.organization_id,
                AIEvaluationReleaseDecision.organization_id
                == arguments.organization_id,
            )
            .order_by(
                AIEvaluationReleaseDecision.created_at.desc(),
                AIEvaluationReleaseDecision.id.desc(),
            )
            .limit(1)
        )
        if decision is None or decision.decision != "approved":
            raise EvaluationBlockedError(
                "latest durable evaluation decision is not approved"
            )
        manifest = (
            decision.comparison_snapshot.get("provider_activation")
            if decision is not None
            and isinstance(decision.comparison_snapshot, dict)
            else None
        )
        if not isinstance(manifest, dict):
            raise EvaluationBlockedError("durable evaluation approval is missing")
        approved_manifest = validate_activation_manifest(manifest)
        if proposed_manifest.get("conditional_billing_policy") is not None:
            expected_approved_manifest = validate_activation_manifest(
                require_conditional_billing_acceptance(
                    proposed_manifest,
                    acceptance=conditional_acceptance_request(),
                    accepted_by_subject=decision.decided_by_subject,
                )
            )
        else:
            expected_approved_manifest = validate_activation_manifest(
                require_reviewed_billing_evidence(proposed_manifest)
            )
        if approved_manifest != expected_approved_manifest:
            raise EvaluationBlockedError(
                "approved manifest differs from the exact printed proposal"
            )
        current_identity = _source_identity()
        if any(
            approved_manifest.get(key) != value
            for key, value in current_identity.items()
        ):
            raise EvaluationBlockedError(
                "approved fixture or source changed after review"
            )
        approved_count = approved_manifest["count_policy"]
        count_policy = InputTokenCountPolicy(
            endpoint=approved_count["endpoint"],
            model=approved_count["model"],
            region=approved_count["region"],
            reservation_per_attempt_usd=approved_count[
                "reservation_per_attempt_usd"
            ],
            evidence_id=approved_count["evidence_id"],
            evidence_expires_at=_timestamp(approved_count["evidence_expires_at"]),
            billing_status=approved_count["billing_status_claim"],
            independently_verified=(
                approved_count["evidence_verification"] == "independently_verified"
            ),
            explicitly_approved=True,
            conditional_policy_version=(
                CONDITIONAL_BILLING_POLICY_VERSION
                if approved_manifest.get("conditional_billing_policy") is not None
                else None
            ),
            conditional_evidence_sha256=(
                CONDITIONAL_BILLING_POLICY.support_evidence_sha256
                if approved_manifest.get("conditional_billing_policy") is not None
                else None
            ),
        )
        policy = EvaluationPolicy(
            additional_all_in_reserve_usd=approved_manifest[
                "additional_all_in_reserve_usd"
            ]
        )
        for binding in bindings:
            await PersistedEvaluationActivationGuard.require(
                db,
                organization_id=arguments.organization_id,
                run_id=arguments.run_id,
                public_chat_session_id=binding.session_id,
                runtime_count_policy=count_policy,
                runtime_evaluation_policy=policy,
            )
    return approved_manifest, count_policy, policy


async def _require_live_authorization(
    arguments: argparse.Namespace,
    approved_manifest: dict[str, Any],
) -> AIEvaluationLiveAuthorization:
    """Require the latest distinct authorization before key/client/dispatch."""
    async with AsyncSessionLocal() as db:
        run = await db.scalar(
            select(AIEvaluationRun).where(
                AIEvaluationRun.run_id == arguments.run_id,
                AIEvaluationRun.organization_id == arguments.organization_id,
            )
        )
        if run is None:
            raise EvaluationBlockedError("evaluation run is unavailable")
        release = await db.scalar(
            select(AIEvaluationReleaseDecision)
            .where(
                AIEvaluationReleaseDecision.organization_id
                == arguments.organization_id,
                AIEvaluationReleaseDecision.candidate_run_id == run.id,
            )
            .order_by(
                AIEvaluationReleaseDecision.created_at.desc(),
                AIEvaluationReleaseDecision.id.desc(),
            )
            .limit(1)
        )
        authorization = await db.scalar(
            select(AIEvaluationLiveAuthorization)
            .where(
                AIEvaluationLiveAuthorization.organization_id
                == arguments.organization_id,
                AIEvaluationLiveAuthorization.candidate_run_id == run.id,
            )
            .order_by(
                AIEvaluationLiveAuthorization.created_at.desc(),
                AIEvaluationLiveAuthorization.id.desc(),
            )
            .limit(1)
        )
        if release is None or release.decision != "approved":
            raise EvaluationBlockedError(
                "latest durable evaluation decision is not approved"
            )
        if authorization is None or authorization.decision != "authorized":
            raise EvaluationBlockedError(
                "latest live evaluation authorization is not authorized"
            )
        if authorization.release_decision_id != release.id:
            raise EvaluationBlockedError(
                "live authorization does not bind the latest release decision"
            )
        if authorization.organization_id != arguments.organization_id:
            raise EvaluationBlockedError("live authorization tenant is invalid")
        validate_live_authorization_snapshot(
            authorization.authorization_snapshot,
            manifest=approved_manifest,
            release_decision_id=release.id,
            authorized_by_subject=authorization.authorized_by_subject,
            expires_at=authorization.expires_at,
        )
        return authorization


async def _execute_turns(
    bindings: list[SessionBinding],
    *,
    organization_id: int,
    approved_run_id: str,
    activation_manifest: dict[str, Any],
    embedding_origin: str,
    require_live_authorization: Any = None,
) -> dict[str, Any]:
    by_fixture = {item.fixture_conversation_id: item for item in bindings}
    outcomes: list[dict[str, Any]] = []
    fixture = expanded_turns()
    fixture_sha256 = hashlib.sha256(
        json.dumps(
            fixture, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    execution_run_id = f"chat-eval-{uuid4().hex}"
    live_authorization_check = require_live_authorization or _require_live_authorization
    try:
        for turn in fixture:
            await live_authorization_check(
                argparse.Namespace(
                    organization_id=organization_id,
                    run_id=approved_run_id,
                ),
                activation_manifest,
            )
            binding = by_fixture[turn["conversation_id"]]
            async with AsyncSessionLocal() as db:
                result = await PublicChatService.send_message(
                    db,
                    session_token=binding.token,
                    embedding_origin=embedding_origin,
                    text=turn["prompt"],
                    client_message_id=f"eval-{turn['turn_id']}",
                    delivery="sync",
                )
                unresolved_generation = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(AIUsageReservation)
                        .where(
                            AIUsageReservation.organization_id == organization_id,
                            AIUsageReservation.reservation_key.like(
                                f"model-eval:{organization_id}:{approved_run_id}:%"
                            ),
                            AIUsageReservation.status == STATUS_RESERVED,
                        )
                    )
                    or 0
                )
                unresolved_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(AIUsageReservation)
                        .where(
                            AIUsageReservation.organization_id == organization_id,
                            AIUsageReservation.reservation_key.like(
                                f"model-eval-count:{organization_id}:{approved_run_id}:%"
                            ),
                            AIUsageReservation.status == STATUS_RESERVED,
                        )
                    )
                    or 0
                )
                provider_log = await db.scalar(
                    select(AIRequestLog)
                    .where(
                        AIRequestLog.organization_id == organization_id,
                        AIRequestLog.feature == "public_chat_model_eval",
                        AIRequestLog.request_id
                        == result.get("_model_eval_request_id", ""),
                    )
                    .limit(1)
                )
            reply = result.get("reply")
            behavior = turn["expected_behavior"]
            forbidden_absent = not any(
                claim.casefold() in (reply or "").casefold()
                for claim in turn["forbidden_claims"]
            )
            if behavior == "silence_after_handoff":
                behavior_ok = reply is None
            elif behavior == "handoff":
                behavior_ok = bool(result.get("handoff"))
            elif behavior == "grounded_answer":
                behavior_ok = (
                    bool(reply)
                    and not result.get("handoff")
                    and provider_log is not None
                    and bool(provider_log.sources)
                )
            else:
                behavior_ok = bool(result.get("handoff")) or (
                    bool(reply) and provider_log is not None
                )
            exact_real_provider_observed = bool(
                provider_log is not None
                and provider_log.llm_called is True
                and provider_log.model == "gpt-6-luna"
                and provider_log.request_id
                == result.get("_model_eval_request_id")
            )
            outcomes.append(
                {
                    "turn_id": turn["turn_id"],
                    "expected_behavior": behavior,
                    "status": result["status"],
                    "reply_present": reply is not None,
                    "handoff": bool(result.get("handoff")),
                    "protocol_passed": behavior_ok and forbidden_absent,
                    "provider_path_observed": exact_real_provider_observed,
                    "mock_mechanics_observed": provider_log is not None
                    and not exact_real_provider_observed,
                    "citation_contract_enforced": provider_log is not None
                    and bool(provider_log.sources),
                }
            )
            if unresolved_generation or unresolved_count:
                raise RunnerBlocked(
                    "an ambiguous provider outcome requires reservation reconciliation"
                )
    except (BaseException, KeyboardInterrupt) as exc:
        await _persist_protocol_results(
            execution_run_id=execution_run_id,
            organization_id=organization_id,
            approved_run_id=approved_run_id,
            activation_manifest=activation_manifest,
            fixture_sha256=fixture_sha256,
            outcomes=outcomes,
            succeeded=False,
            error_category="interrupted" if isinstance(exc, KeyboardInterrupt) else "blocked",
        )
        raise
    await _persist_protocol_results(
        execution_run_id=execution_run_id,
        organization_id=organization_id,
        approved_run_id=approved_run_id,
        activation_manifest=activation_manifest,
        fixture_sha256=fixture_sha256,
        outcomes=outcomes,
        succeeded=len(outcomes) == 50,
        error_category=None,
    )
    return {
        "mode": "live_provider_evaluation",
        "execution_run_id": execution_run_id,
        "turns_attempted": len(outcomes),
        "sessions": len(bindings),
        "protocol_pass_rate": (
            sum(bool(item["protocol_passed"]) for item in outcomes) / len(outcomes)
            if outcomes
            else 0
        ),
        "quality_scores": None,
        "outcomes": outcomes,
    }


async def _persist_protocol_results(
    *,
    execution_run_id: str,
    organization_id: int,
    approved_run_id: str,
    activation_manifest: dict[str, Any],
    fixture_sha256: str,
    outcomes: list[dict[str, Any]],
    succeeded: bool,
    error_category: str | None,
) -> None:
    """Persist bounded protocol evidence, never customer text or credentials."""
    if not outcomes:
        return
    activation_manifest_sha256 = hashlib.sha256(
        json.dumps(
            activation_manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    async with AsyncSessionLocal() as db:
        passed = sum(bool(item["protocol_passed"]) for item in outcomes)
        run = AIEvaluationRun(
            run_id=execution_run_id,
            organization_id=organization_id,
            target_type="agent",
            status="succeeded" if succeeded else "failed",
            model="gpt-6-luna",
            embedding_model="none-lexical",
            corpus_revision={"frozen_fixture_sha256": fixture_sha256},
            trigger_source="manual",
            requested_by_subject="system:controlled-evaluation-runner",
            input={
                "synthetic_only": True,
                "fixture_sha256": fixture_sha256,
                "approved_prerequisite_run_id": approved_run_id,
                "activation_manifest_sha256": activation_manifest_sha256,
                "quality_scores": None,
            },
            pass_rate=passed / len(outcomes),
            metrics={
                "turns_attempted": len(outcomes),
                "protocol_passed": passed,
                "quality_measured": False,
            },
            error=error_category,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        db.add(run)
        await db.flush()
        for outcome in outcomes:
            db.add(
                AIEvaluationCase(
                    run_id=run.id,
                    organization_id=organization_id,
                    case_id=outcome["turn_id"],
                    case_type="agent",
                    input={"synthetic_fixture_turn_id": outcome["turn_id"]},
                    expected={"behavior": outcome["expected_behavior"]},
                    actual={
                        "status": outcome["status"],
                        "reply_present": outcome["reply_present"],
                        "handoff": outcome["handoff"],
                    },
                    dimensions={
                        "protocol_passed": outcome["protocol_passed"],
                        "citation_contract_enforced": outcome[
                            "citation_contract_enforced"
                        ],
                        "provider_path_observed": outcome[
                            "provider_path_observed"
                        ],
                        "mock_mechanics_observed": outcome[
                            "mock_mechanics_observed"
                        ],
                        "quality_measured": False,
                    },
                    fingerprint=hashlib.sha256(
                        f"{fixture_sha256}:{outcome['turn_id']}".encode()
                    ).hexdigest(),
                )
            )
        await db.commit()


async def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    if settings.environment != "development":
        raise RunnerBlocked("controlled evaluation is development-only")
    if arguments.prepare_review_hold and arguments.resume_review_hold is not None:
        raise RunnerBlocked("review hold preparation and resume are mutually exclusive")
    if arguments.prepare_review_hold and arguments.execute_live:
        raise RunnerBlocked("review hold preparation cannot execute live")
    if arguments.resume_review_hold is not None and not arguments.execute_live:
        raise RunnerBlocked("review hold resume requires separately authorized live mode")
    if not arguments.execute_live and (
        settings.model_eval_openai_source_enabled
        or settings.public_chat_model_eval_enabled
    ):
        raise RunnerBlocked("preflight requires both provider flags disabled")
    if arguments.execute_live and not (
        settings.model_eval_openai_source_enabled
        and settings.public_chat_model_eval_enabled
    ):
        raise RunnerBlocked("live continuation requires both process-scoped flags")

    bindings: list[SessionBinding] = []
    hold_path: Path | None = None
    claimed_path: Path | None = None
    hold_state_sha256: str | None = None
    saved_manifest: dict[str, Any] | None = None
    cleanup_owned = True
    if arguments.resume_review_hold is not None:
        claimed_path, bindings, saved_manifest, hold_payload = (
            _load_claimed_review_hold(arguments.resume_review_hold)
        )
        if (
            hold_payload.get("organization_id") != arguments.organization_id
            or hold_payload.get("run_id") != arguments.run_id
            or hold_payload.get("embedding_origin") != arguments.embedding_origin
            or hold_payload.get("expires_at") != arguments.expires_at
        ):
            await _close_sessions(bindings)
            _remove_review_hold_file(claimed_path)
            raise RunnerBlocked("review hold invocation differs from prepared binding")
    else:
        bindings = await _create_sessions(arguments)
    try:
        plan, count_policy, policy = _build_plan(arguments, bindings)
        if saved_manifest is not None and plan["activation_manifest"] != saved_manifest:
            raise RunnerBlocked("review hold manifest or source changed after preparation")
        if claimed_path is not None:
            _acknowledge_review_hold_claim(claimed_path)
        fixture_json = json.dumps(
            expanded_turns(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        preview = {
            "mode": (
                "offline_preflight"
                if not arguments.execute_live
                else "awaiting_approval"
            ),
            "fixture_sha256": hashlib.sha256(fixture_json.encode()).hexdigest(),
            "sessions": [
                {
                    "fixture_conversation_id": item.fixture_conversation_id,
                    "session_id": item.session_id,
                }
                for item in bindings
            ],
            "expected_operations": {
                "turns": 50,
                "maximum_count_requests": 100,
                "maximum_generation_requests": 100,
                "sdk_retries": 0,
            },
            "activation_manifest": plan["activation_manifest"],
            "activation_manifest_sha256": _canonical_manifest_digest(
                plan["activation_manifest"]
            ),
            "approval_status": "not_checked",
            "evidence_status": "operator_proposal_pending_authenticated_review",
            "conditional_billing_acceptance_required": {
                "statement": OWNER_ACCEPTANCE_STATEMENT,
                "request": conditional_acceptance_request(),
                "authenticated_subject_supplied_by_server": True,
                "live_execution_authorized": False,
            },
            "separate_live_authorization_required": {
                "statement": LIVE_AUTHORIZATION_STATEMENT,
                "request": live_authorization_request(),
                "authenticated_subject_supplied_by_server": True,
                "release_approval_is_not_live_authorization": True,
            },
            "cost_assumptions": plan["budget"],
            "real_model_quality_measured": False,
        }
        if arguments.prepare_review_hold:
            hold_path, hold_state_sha256 = _write_review_hold(
                arguments=arguments,
                bindings=bindings,
                manifest=plan["activation_manifest"],
            )
            preview["mode"] = "provider_disabled_review_hold"
            preview["review_hold_path"] = str(hold_path)
            preview["automatic_execution"] = False
        print(json.dumps(preview, indent=2, ensure_ascii=False, sort_keys=True))
        if arguments.prepare_review_hold:
            try:
                outcome = await _wait_for_review_hold(
                    path=hold_path,
                    state_sha256=hold_state_sha256,
                    arguments=arguments,
                )
            except KeyboardInterrupt:
                outcome = "cancelled"
            if outcome == "claimed_for_separately_authorized_resume":
                cleanup_owned = False
            else:
                _remove_review_hold_file(hold_path)
                _remove_review_hold_file(
                    hold_path.with_name(_claimed_hold_name(hold_path))
                )
                _remove_review_hold_file(
                    hold_path.with_name(_claim_ack_name(hold_path))
                )
            return {
                "status": f"review_hold_{outcome}",
                "activation_manifest_sha256": preview[
                    "activation_manifest_sha256"
                ],
                "cleanup_pending": cleanup_owned,
            }
        if not arguments.execute_live:
            return {"status": "preflight_complete_no_provider_access", **preview}

        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise RunnerBlocked("live continuation requires a private interactive terminal")
        input(
            "Record the exact printed manifest through the authenticated evaluation.manage "
            "release-decision API, then press Enter to re-check approval: "
        )
        approved_manifest, count_policy, policy = await _require_approval(
            arguments, bindings, plan["activation_manifest"]
        )
        print("approval_status=approved_exact_manifest", file=sys.stderr)

        await _require_live_authorization(arguments, approved_manifest)
        print("live_authorization_status=authorized_exact_manifest", file=sys.stderr)

        client = NoRetryHttpClient()
        try:
            with operator_entered_api_key() as secret:
                async with installed_development_evaluation_provider(
                    run_id=arguments.run_id,
                    http_client=client,
                    bearer_token=secret,
                    count_policy=count_policy,
                    evaluation_policy=policy,
                    explicit_operator_activation=True,
                ):
                    return await _execute_turns(
                        bindings,
                        organization_id=arguments.organization_id,
                        approved_run_id=arguments.run_id,
                        activation_manifest=approved_manifest,
                        embedding_origin=arguments.embedding_origin,
                    )
        finally:
            await client.aclose()
    finally:
        cleanup_failures = (
            await _close_sessions(bindings) if cleanup_owned and bindings else []
        )
        if cleanup_owned:
            if hold_path is not None:
                _remove_review_hold_file(hold_path)
                _remove_review_hold_file(
                    hold_path.with_name(_claimed_hold_name(hold_path))
                )
                _remove_review_hold_file(
                    hold_path.with_name(_claim_ack_name(hold_path))
                )
            if claimed_path is not None:
                _remove_review_hold_file(claimed_path)
                original = claimed_path.with_name(
                    claimed_path.name.removeprefix(".").removesuffix(".claimed")
                )
                _remove_review_hold_file(
                    original.with_name(_claim_ack_name(original))
                )
        if cleanup_failures:
            print(
                f"cleanup_warning_count={len(cleanup_failures)}",
                file=sys.stderr,
            )


def main() -> None:
    try:
        result = asyncio.run(_run(_arguments()))
    except (EvaluationBlockedError, RunnerBlocked, KeyboardInterrupt) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
