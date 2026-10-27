"""Exact, separately authenticated live-evaluation authorization contract."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.services.model_eval_controller import EvaluationBlockedError

LIVE_AUTHORIZATION_POLICY_VERSION = "cxops-live-evaluation-authorization-v1"
LIVE_AUTHORIZATION_STATEMENT = (
    "I authorize one live, development-only 50-turn CXOps evaluation for this "
    "exact approved manifest, tenant, run, ten sessions, source identity, billing "
    "policy and expiry. I understand this is separate from release approval and "
    "that a later revocation stops subsequent provider requests."
)
LIVE_AUTHORIZATION_STATEMENT_SHA256 = hashlib.sha256(
    LIVE_AUTHORIZATION_STATEMENT.encode("utf-8")
).hexdigest()


def canonical_manifest(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest(manifest).encode("utf-8")).hexdigest()


def live_authorization_request() -> dict[str, Any]:
    return {
        "policy_version": LIVE_AUTHORIZATION_POLICY_VERSION,
        "statement_sha256": LIVE_AUTHORIZATION_STATEMENT_SHA256,
    }


def build_live_authorization_snapshot(
    *,
    manifest: dict[str, Any],
    release_decision_id: int,
    authorized_by_subject: str,
    expires_at: datetime,
) -> dict[str, Any]:
    if not authorized_by_subject.strip():
        raise EvaluationBlockedError("authenticated live authorization is required")
    if expires_at.tzinfo is None:
        raise EvaluationBlockedError("live authorization expiry must be timezone-aware")
    expiry = expires_at.astimezone(UTC)
    manifest_expiry = datetime.fromisoformat(
        str(manifest.get("expires_at", "")).replace("Z", "+00:00")
    )
    if expiry <= datetime.now(UTC) or expiry > manifest_expiry.astimezone(UTC):
        raise EvaluationBlockedError(
            "live authorization expiry must be current and within manifest expiry"
        )
    return {
        "policy_version": LIVE_AUTHORIZATION_POLICY_VERSION,
        "statement_sha256": LIVE_AUTHORIZATION_STATEMENT_SHA256,
        "release_decision_id": release_decision_id,
        "manifest_sha256": manifest_sha256(manifest),
        "provider_activation": manifest,
        "authorized_by_subject": authorized_by_subject,
        "expires_at": expiry.isoformat(),
    }


def validate_live_authorization_snapshot(
    snapshot: dict[str, Any],
    *,
    manifest: dict[str, Any],
    release_decision_id: int,
    authorized_by_subject: str,
    expires_at: datetime,
) -> None:
    expected = build_live_authorization_snapshot(
        manifest=manifest,
        release_decision_id=release_decision_id,
        authorized_by_subject=authorized_by_subject,
        expires_at=expires_at,
    )
    if canonical_manifest(snapshot) != canonical_manifest(expected):
        raise EvaluationBlockedError(
            "live authorization differs from the exact approved manifest"
        )
