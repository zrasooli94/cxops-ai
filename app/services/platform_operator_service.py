"""Platform-operator authority (Phase 1Q).

Platform operators review leads, approve tenants and set entitlements across
every organization. That authority is deliberately separate from tenant RBAC:

* it is keyed by the verified Nhost ``sub`` of the caller, never by an email
  address, a display name, a request header, or a JWT role claim;
* it is granted only by ``scripts/manage_platform_operators.py`` with direct
  database access - no HTTP endpoint can create, extend or revoke it;
* being an owner of any number of organizations confers none of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import PlatformOperator


@dataclass(frozen=True)
class PlatformOperatorContext:
    subject: str
    operator_id: int


class PlatformOperatorService:
    @staticmethod
    async def resolve(db: AsyncSession, subject: str) -> PlatformOperatorContext | None:
        """The active grant for ``subject``, or None (fail closed)."""
        if not subject:
            return None
        row = (
            await db.execute(
                select(PlatformOperator).where(
                    PlatformOperator.subject == subject,
                    PlatformOperator.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return PlatformOperatorContext(subject=row.subject, operator_id=row.id)

    @staticmethod
    async def grant(
        db: AsyncSession, *, subject: str, granted_by: str, note: str | None = None
    ) -> tuple[PlatformOperator, bool]:
        """Grant operator authority. Returns (row, created). Commits."""
        subject = subject.strip()
        if not subject:
            raise ValueError("subject is required")
        existing = await PlatformOperatorService.resolve(db, subject)
        if existing is not None:
            row = await db.get(PlatformOperator, existing.operator_id)
            assert row is not None
            return row, False
        row = PlatformOperator(subject=subject, granted_by=granted_by, note=note)
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            again = await PlatformOperatorService.resolve(db, subject)
            if again is None:
                raise
            stored = await db.get(PlatformOperator, again.operator_id)
            assert stored is not None
            return stored, False
        await db.refresh(row)
        return row, True

    @staticmethod
    async def revoke(db: AsyncSession, *, subject: str, revoked_by: str) -> bool:
        """Revoke the active grant. Returns whether one existed. Commits."""
        result = await db.execute(
            update(PlatformOperator)
            .where(
                PlatformOperator.subject == subject.strip(),
                PlatformOperator.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC), revoked_by=revoked_by)
            .returning(PlatformOperator.id)
        )
        revoked = result.first() is not None
        await db.commit()
        return revoked

    @staticmethod
    async def list_all(db: AsyncSession) -> list[PlatformOperator]:
        return list(
            (
                await db.execute(select(PlatformOperator).order_by(PlatformOperator.id))
            ).scalars()
        )
