from datetime import UTC, datetime, timedelta

import pytest

from app.services.model_eval_controller import EvaluationBlockedError
from app.services.model_eval_live_authorization import (
    LIVE_AUTHORIZATION_POLICY_VERSION,
    build_live_authorization_snapshot,
    validate_live_authorization_snapshot,
)


def _manifest():
    return {
        "purpose": "controlled_public_chat_model_evaluation",
        "organization_id": 7,
        "run_id": "candidate-run",
        "public_chat_session_ids": list(range(1, 11)),
        "reviewed_source_commit": "a" * 40,
        "frozen_fixture_sha256": "b" * 64,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    }


def test_live_authorization_binds_exact_manifest_subject_release_and_expiry():
    manifest = _manifest()
    expiry = datetime.now(UTC) + timedelta(minutes=20)
    snapshot = build_live_authorization_snapshot(
        manifest=manifest,
        release_decision_id=41,
        authorized_by_subject="owner:synthetic",
        expires_at=expiry,
    )
    assert snapshot["policy_version"] == LIVE_AUTHORIZATION_POLICY_VERSION
    validate_live_authorization_snapshot(
        snapshot,
        manifest=manifest,
        release_decision_id=41,
        authorized_by_subject="owner:synthetic",
        expires_at=expiry,
    )

    changed = dict(manifest)
    changed["reviewed_source_commit"] = "c" * 40
    with pytest.raises(EvaluationBlockedError, match="exact approved manifest"):
        validate_live_authorization_snapshot(
            snapshot,
            manifest=changed,
            release_decision_id=41,
            authorized_by_subject="owner:synthetic",
            expires_at=expiry,
        )


def test_expired_or_manifest_exceeding_authorization_is_rejected():
    manifest = _manifest()
    with pytest.raises(EvaluationBlockedError, match="current and within"):
        build_live_authorization_snapshot(
            manifest=manifest,
            release_decision_id=41,
            authorized_by_subject="owner:synthetic",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    with pytest.raises(EvaluationBlockedError, match="current and within"):
        build_live_authorization_snapshot(
            manifest=manifest,
            release_decision_id=41,
            authorized_by_subject="owner:synthetic",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
