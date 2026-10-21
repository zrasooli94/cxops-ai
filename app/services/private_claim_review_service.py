"""C10 synthetic-only reviewer assignment, private claim digest & audit service.

NOT exposed by an API, staff UI, worker or production feature flag. Every
mutation requires a caller-authenticated AuthorizationContext and an
independent lookup of its *current* org membership/role. All reads/writes bind
tenant ID; the database has composite foreign keys and append-only triggers.

Only versioned SHA-256 digests and closed review labels are stored. The
fictional C6 data used to validate exact source quotes remains on disk. No
customer contents, raw source paragraphs, free-text rationale, API keys or
real reviewer signatures are persisted.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import (
    AuthorizationContext,
    Capability,
    OrganizationRole,
)
from app.models.claim_review import (
    ClaimReviewAssignment,
    ClaimReviewBatch,
    ClaimReviewEvent,
    ClaimReviewItem,
)
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from scripts.chatbot_claim_review import (
    _cited_sources,
    _digest,
    _worksheet_rows,
    load_dataset,
)

_DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "evals"
    / "chatbot_claim_review_synthetic.json"
)
_VALID_LABELS = frozenset({
    "supported", "contradicted", "unsupported", "unclear",
})


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PrivateClaimReviewService:
    @staticmethod
    def _synthetic_source() -> tuple[dict, dict[str, tuple[dict, dict, dict]]]:
        fixture = load_dataset(_DATASET_PATH)
        compiled: dict[str, tuple[dict, dict, dict]] = {}
        row_map = {row["review_key"]: row for row in _worksheet_rows(fixture)}
        for case in fixture["cases"]:
            for claim in case["claims"]:
                key = case["case_id"] + ":" + claim["claim_id"]
                compiled[key] = (case, claim, row_map[key])
        return fixture, compiled

    @staticmethod
    async def _authorized(
        db: AsyncSession,
        actor: AuthorizationContext,
        *,
        organization_id: int,
        manage: bool,
    ) -> None:
        if (
            not isinstance(organization_id, int)
            or isinstance(organization_id, bool)
            or organization_id <= 0
            or actor is None
            or actor.organization_id != organization_id
            or not isinstance(actor.subject, str)
            or not actor.subject.strip()
            or len(actor.subject) > 255
        ):
            raise PermissionError("Claim review actor/tenant authorization failed")

        # Lock membership and lifecycle through the current transaction. A
        # concurrent revocation or suspension must serialize with this write.
        membership = (
            await db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.subject == actor.subject,
                ).with_for_update(read=True)
            )
        ).scalar_one_or_none()
        org = (
            await db.execute(
                select(Organization).where(
                    Organization.id == organization_id,
                ).with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if membership is None or org is None:
            raise PermissionError("Claim review actor/tenant authorization failed")

        try:
            role = OrganizationRole(membership.role)
        except ValueError as exc:
            raise PermissionError("Unknown current membership role") from exc
        context = AuthorizationContext(
            organization_id=organization_id,
            subject=actor.subject,
            role=role,
            organization_status=org.lifecycle_status,
        )
        needed = Capability.EVALUATION_MANAGE if manage else Capability.EVALUATION_READ
        if (
            context.role != actor.role
            or needed not in context.capabilities
            or needed not in actor.capabilities
        ):
            raise PermissionError("Current claim-review capability is not granted")

    @staticmethod
    async def _current_reviewer(
        db: AsyncSession,
        *,
        organization_id: int,
        subject: str,
    ) -> None:
        membership = (
            await db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.subject == subject,
                ).with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if membership is None:
            raise ValueError("Reviewer must be a current tenant member")
        try:
            role = OrganizationRole(membership.role)
        except ValueError as exc:
            raise ValueError("Reviewer role is invalid") from exc
        if role not in {
            OrganizationRole.OWNER,
            OrganizationRole.ADMIN,
            OrganizationRole.SUPERVISOR,
        }:
            raise ValueError("Reviewer needs evaluation-management capability")

    @staticmethod
    async def _batch(
        db: AsyncSession,
        *,
        organization_id: int,
        batch_id: int,
        lock: bool = False,
    ) -> ClaimReviewBatch:
        statement = select(ClaimReviewBatch).where(
            ClaimReviewBatch.id == batch_id,
            ClaimReviewBatch.organization_id == organization_id,
        )
        if lock:
            statement = statement.with_for_update()
        batch = (await db.execute(statement)).scalar_one_or_none()
        if batch is None:
            raise ValueError("Claim review batch not found for tenant")
        return batch

    @classmethod
    async def create_synthetic_batch(
        cls,
        db: AsyncSession,
        *,
        actor: AuthorizationContext,
    ) -> ClaimReviewBatch:
        await cls._authorized(
            db, actor, organization_id=actor.organization_id, manage=True,
        )
        fixture, compiled = cls._synthetic_source()
        batch = ClaimReviewBatch(
            organization_id=actor.organization_id,
            dataset_id=fixture["dataset_id"],
            dataset_digest=_digest(fixture),
            created_by_subject=actor.subject,
        )
        db.add(batch)
        await db.flush()
        for key, (case, claim, row) in compiled.items():
            sources = _cited_sources(case, claim)
            item = ClaimReviewItem(
                organization_id=actor.organization_id,
                batch_id=batch.id,
                review_key=key,
                fingerprint=row["fingerprint"],
                source_digest=_hash(row["cited_source_passages"]),
                source_revisions=[
                    {"source_id": src["source_id"], "revision": src["revision"]}
                    for src in sources
                ],
            )
            db.add(item)
        await db.flush()
        return batch

    @classmethod
    async def assign_reviewers(
        cls,
        db: AsyncSession,
        *,
        actor: AuthorizationContext,
        batch_id: int,
        reviewer_one_subject: str,
        reviewer_two_subject: str,
    ) -> list[ClaimReviewAssignment]:
        await cls._authorized(
            db, actor, organization_id=actor.organization_id, manage=True,
        )
        await cls._batch(
            db, organization_id=actor.organization_id, batch_id=batch_id,
            lock=True,
        )
        subjects = (reviewer_one_subject, reviewer_two_subject)
        if (
            len(set(subjects)) != 2
            or any(
                not isinstance(subject, str)
                or not subject.strip()
                or len(subject) > 255
                for subject in subjects
            )
        ):
            raise ValueError("Two distinct bounded reviewer subjects are required")
        for subject in subjects:
            await cls._current_reviewer(
                db, organization_id=actor.organization_id, subject=subject,
            )
        existing = (
            await db.execute(
                select(ClaimReviewAssignment.id).where(
                    ClaimReviewAssignment.organization_id == actor.organization_id,
                    ClaimReviewAssignment.batch_id == batch_id,
                )
            )
        ).first()
        if existing is not None:
            raise ValueError("This immutable batch has assigned reviewers already")

        assignments = [
            ClaimReviewAssignment(
                organization_id=actor.organization_id,
                batch_id=batch_id,
                slot=i,
                reviewer_subject=subject,
                assigned_by_subject=actor.subject,
            )
            for i, subject in enumerate(subjects, start=1)
        ]
        db.add_all(assignments)
        await db.flush()
        return assignments

    @classmethod
    async def assigned_queue(
        cls,
        db: AsyncSession,
        *,
        actor: AuthorizationContext,
        batch_id: int,
    ) -> list[dict[str, str]]:
        await cls._authorized(
            db, actor, organization_id=actor.organization_id, manage=True,
        )
        await cls._batch(
            db, organization_id=actor.organization_id, batch_id=batch_id,
        )
        assignment = (
            await db.execute(
                select(ClaimReviewAssignment).where(
                    ClaimReviewAssignment.organization_id == actor.organization_id,
                    ClaimReviewAssignment.batch_id == batch_id,
                    ClaimReviewAssignment.reviewer_subject == actor.subject,
                )
            )
        ).scalar_one_or_none()
        if assignment is None:
            return []
        items = (
            await db.execute(
                select(ClaimReviewItem).where(
                    ClaimReviewItem.organization_id == actor.organization_id,
                    ClaimReviewItem.batch_id == batch_id,
                ).order_by(ClaimReviewItem.id)
            )
        ).scalars().all()
        # The endpoint is future work; this output has no other reviewer's
        # identity, labels, source text or notes. The immutable snapshots
        # correspond to exact synthetic fixture rows.
        return [
            {"review_key": item.review_key, "fingerprint": item.fingerprint}
            for item in items
        ]

    @staticmethod
    def _review_digests(
        *,
        case: dict,
        claim: dict,
        label: str,
        evidence_excerpt: str | None,
        rationale: str | None,
    ) -> tuple[str | None, str | None]:
        if label not in _VALID_LABELS:
            raise ValueError("Unknown claim evidence label")
        if label in {"supported", "contradicted"}:
            if (
                not isinstance(evidence_excerpt, str)
                or not 8 <= len(evidence_excerpt) <= 1_000
                or not any(
                    evidence_excerpt in source["text"]
                    for source in _cited_sources(case, claim)
                )
            ):
                raise ValueError("Review must quote exact cited evidence")
            return _hash(evidence_excerpt), None
        if (
            not isinstance(rationale, str)
            or not 10 <= len(rationale.strip()) <= 500
        ):
            raise ValueError("Unsupported/unclear review needs a bounded rationale")
        return None, _hash(rationale.strip())

    @classmethod
    async def _item(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        batch: ClaimReviewBatch,
        review_key: str,
    ) -> tuple[ClaimReviewItem, dict, dict]:
        item = (
            await db.execute(
                select(ClaimReviewItem).where(
                    ClaimReviewItem.organization_id == organization_id,
                    ClaimReviewItem.batch_id == batch.id,
                    ClaimReviewItem.review_key == review_key,
                ).with_for_update()
            )
        ).scalar_one_or_none()
        fixture, compiled = cls._synthetic_source()
        if item is None or review_key not in compiled:
            raise ValueError("Claim not found in this tenant's synthetic batch")
        if _digest(fixture) != batch.dataset_digest:
            raise ValueError("Frozen dataset revision no longer matches")
        case, claim, row = compiled[review_key]
        if (
            item.fingerprint != row["fingerprint"]
            or item.source_digest != _hash(row["cited_source_passages"])
        ):
            raise ValueError("Frozen claim/source snapshot changed")
        return item, case, claim

    @classmethod
    async def submit_review(
        cls,
        db: AsyncSession,
        *,
        actor: AuthorizationContext,
        batch_id: int,
        review_key: str,
        label: str,
        evidence_excerpt: str | None = None,
        rationale: str | None = None,
    ) -> ClaimReviewEvent:
        await cls._authorized(
            db, actor, organization_id=actor.organization_id, manage=True,
        )
        batch = await cls._batch(
            db, organization_id=actor.organization_id, batch_id=batch_id,
        )
        item, case, claim = await cls._item(
            db, organization_id=actor.organization_id,
            batch=batch, review_key=review_key,
        )
        assigned = (
            await db.execute(
                select(ClaimReviewAssignment).where(
                    ClaimReviewAssignment.organization_id == actor.organization_id,
                    ClaimReviewAssignment.batch_id == batch_id,
                )
            )
        ).scalars().all()
        if len(assigned) != 2:
            raise ValueError("Two independent reviewer assignments are required")
        assignment = next(
            (a for a in assigned if a.reviewer_subject == actor.subject),
            None,
        )
        if assignment is None:
            raise PermissionError("Reviewer is not assigned to this batch")
        evidence_digest, rationale_digest = cls._review_digests(
            case=case, claim=claim, label=label,
            evidence_excerpt=evidence_excerpt, rationale=rationale,
        )
        existing = (
            await db.execute(
                select(ClaimReviewEvent.id).where(
                    ClaimReviewEvent.organization_id == actor.organization_id,
                    ClaimReviewEvent.batch_id == batch_id,
                    ClaimReviewEvent.item_id == item.id,
                    ClaimReviewEvent.assignment_id == assignment.id,
                    ClaimReviewEvent.event_kind == "review",
                )
            )
        ).first()
        if existing is not None:
            raise ValueError("This append-only reviewer judgment already exists")
        event = ClaimReviewEvent(
            organization_id=actor.organization_id,
            batch_id=batch_id,
            item_id=item.id,
            assignment_id=assignment.id,
            event_kind="review",
            actor_subject=actor.subject,
            label=label,
            evidence_digest=evidence_digest,
            rationale_digest=rationale_digest,
        )
        db.add(event)
        await db.flush()
        return event

    @classmethod
    async def adjudicate(
        cls,
        db: AsyncSession,
        *,
        actor: AuthorizationContext,
        batch_id: int,
        review_key: str,
        label: str,
        evidence_excerpt: str | None = None,
        rationale: str | None = None,
    ) -> ClaimReviewEvent:
        await cls._authorized(
            db, actor, organization_id=actor.organization_id, manage=True,
        )
        batch = await cls._batch(
            db, organization_id=actor.organization_id, batch_id=batch_id,
        )
        item, case, claim = await cls._item(
            db, organization_id=actor.organization_id,
            batch=batch, review_key=review_key,
        )
        assignments = (
            await db.execute(
                select(ClaimReviewAssignment).where(
                    ClaimReviewAssignment.organization_id == actor.organization_id,
                    ClaimReviewAssignment.batch_id == batch_id,
                )
            )
        ).scalars().all()
        if len(assignments) != 2 or actor.subject in {
            assignment.reviewer_subject for assignment in assignments
        }:
            raise PermissionError("Disputes require a third, distinct adjudicator")
        prior = (
            await db.execute(
                select(ClaimReviewEvent).where(
                    ClaimReviewEvent.organization_id == actor.organization_id,
                    ClaimReviewEvent.batch_id == batch_id,
                    ClaimReviewEvent.item_id == item.id,
                    ClaimReviewEvent.event_kind == "review",
                )
            )
        ).scalars().all()
        if len(prior) != 2 or len({event.label for event in prior}) != 2:
            raise ValueError("Only two completed, disputed judgments may be adjudicated")
        evidence_digest, rationale_digest = cls._review_digests(
            case=case, claim=claim, label=label,
            evidence_excerpt=evidence_excerpt, rationale=rationale,
        )
        event = ClaimReviewEvent(
            organization_id=actor.organization_id,
            batch_id=batch_id,
            item_id=item.id,
            assignment_id=None,
            event_kind="adjudication",
            actor_subject=actor.subject,
            label=label,
            evidence_digest=evidence_digest,
            rationale_digest=rationale_digest,
        )
        db.add(event)
        await db.flush()
        return event
