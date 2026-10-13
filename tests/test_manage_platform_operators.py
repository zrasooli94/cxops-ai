"""Phase 1Q - the platform-operator grant CLI is dry-run by default and
accepts only Nhost user ids (never an email address)."""

import uuid

import pytest
from sqlalchemy import delete

from app.models.commercial import PlatformOperator
from app.services.platform_operator_service import PlatformOperatorService
from scripts.manage_platform_operators import parse_args, run


def test_subject_must_be_a_uuid_not_an_email():
    with pytest.raises(SystemExit):
        parse_args(["grant", "--subject", "owner@example.com", "--granted-by", "me"])


@pytest.mark.asyncio
async def test_grant_is_dry_run_until_apply_and_revoke_is_immediate(db):
    subject = str(uuid.uuid4())
    try:
        dry = await run(db, parse_args(["grant", "--subject", subject, "--granted-by", "t"]))
        assert "dry run" in dry[0]
        assert await PlatformOperatorService.resolve(db, subject) is None

        applied = await run(
            db, parse_args(["grant", "--subject", subject, "--granted-by", "t", "--apply"])
        )
        assert applied == [f"{subject}: granted"]
        assert await PlatformOperatorService.resolve(db, subject) is not None

        twice = await run(
            db, parse_args(["grant", "--subject", subject, "--granted-by", "t", "--apply"])
        )
        assert "already" in twice[0]

        listing = await run(db, parse_args(["list"]))
        assert any(subject in line and "active" in line for line in listing)

        revoked = await run(
            db, parse_args(["revoke", "--subject", subject, "--revoked-by", "t", "--apply"])
        )
        assert revoked == [f"{subject}: revoked"]
        assert await PlatformOperatorService.resolve(db, subject) is None
    finally:
        await db.execute(delete(PlatformOperator).where(PlatformOperator.subject == subject))
        await db.commit()
