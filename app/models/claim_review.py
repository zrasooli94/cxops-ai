"""C10: source-only, synthetic-limited private claim-review storage.

All four tables are tenant owned. Composite foreign keys prevent attaching an
item, assignment or event to a different organization/batch. Snapshots contain
hashes and *synthetic-only* source revision IDs, NOT customer/source text,
emails, API keys, auth tokens or reviewers' free-text notes.

The C10 migration installs PostgreSQL triggers that reject UPDATE/DELETE on
these audit records and validate actor/assignment separation on event INSERT.
A DB superuser can bypass/drop triggers; this is not WORM storage.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ClaimReviewBatch(Base):
    __tablename__ = "claim_review_batches"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_cr_batches_id_org"),
        Index("ix_cr_batches_org_created", "organization_id", "created_at"),
        CheckConstraint(
            "dataset_id = 'c6-fictional-faq-20261021'",
            name="ck_cr_batches_synthetic_only",
        ),
        CheckConstraint(
            "dataset_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cr_batches_digest",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False,
    )
    dataset_id: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class ClaimReviewItem(Base):
    __tablename__ = "claim_review_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["claim_review_batches.id", "claim_review_batches.organization_id"],
            name="fk_cr_items_batch_org",
        ),
        UniqueConstraint(
            "id", "organization_id", "batch_id", name="uq_cr_items_id_org_batch",
        ),
        UniqueConstraint("batch_id", "review_key", name="uq_cr_items_batch_key"),
        Index("ix_cr_items_org_batch", "organization_id", "batch_id"),
        CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_cr_items_fingerprint",
        ),
        CheckConstraint(
            "source_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cr_items_source_digest",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False,
    )
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    review_key: Mapped[str] = mapped_column(String(160), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revisions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class ClaimReviewAssignment(Base):
    __tablename__ = "claim_review_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["claim_review_batches.id", "claim_review_batches.organization_id"],
            name="fk_cr_assignments_batch_org",
        ),
        UniqueConstraint(
            "id", "organization_id", "batch_id",
            name="uq_cr_assignments_id_org_batch",
        ),
        UniqueConstraint("batch_id", "slot", name="uq_cr_assignments_batch_slot"),
        UniqueConstraint(
            "batch_id", "reviewer_subject", name="uq_cr_assignments_batch_subject",
        ),
        CheckConstraint("slot IN (1, 2)", name="ck_cr_assignments_slot"),
        Index("ix_cr_assignments_org_subject", "organization_id", "reviewer_subject"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False,
    )
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_by_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class ClaimReviewEvent(Base):
    __tablename__ = "claim_review_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["claim_review_batches.id", "claim_review_batches.organization_id"],
            name="fk_cr_events_batch_org",
        ),
        ForeignKeyConstraint(
            ["item_id", "organization_id", "batch_id"],
            [
                "claim_review_items.id", "claim_review_items.organization_id",
                "claim_review_items.batch_id",
            ],
            name="fk_cr_events_item_org_batch",
        ),
        ForeignKeyConstraint(
            ["assignment_id", "organization_id", "batch_id"],
            [
                "claim_review_assignments.id",
                "claim_review_assignments.organization_id",
                "claim_review_assignments.batch_id",
            ],
            name="fk_cr_events_assignment_org_batch",
        ),
        CheckConstraint(
            "event_kind IN ('review', 'adjudication')",
            name="ck_cr_events_kind",
        ),
        CheckConstraint(
            "label IN ('supported', 'contradicted', 'unsupported', 'unclear')",
            name="ck_cr_events_label",
        ),
        CheckConstraint(
            "(event_kind = 'review' AND assignment_id IS NOT NULL) "
            "OR (event_kind = 'adjudication' AND assignment_id IS NULL)",
            name="ck_cr_events_assignment_type",
        ),
        CheckConstraint(
            "((label IN ('supported', 'contradicted') AND "
            "evidence_digest IS NOT NULL) OR "
            "(label IN ('unsupported', 'unclear') AND rationale_digest IS NOT NULL))",
            name="ck_cr_events_evidence_requirement",
        ),
        CheckConstraint(
            "evidence_digest IS NULL OR evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cr_events_evidence_digest",
        ),
        CheckConstraint(
            "rationale_digest IS NULL OR rationale_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cr_events_rationale_digest",
        ),
        Index(
            "uq_cr_events_review_item_assignment",
            "item_id", "assignment_id", unique=True,
            postgresql_where=text("event_kind = 'review'"),
        ),
        Index(
            "uq_cr_events_adjudication_item",
            "item_id", unique=True,
            postgresql_where=text("event_kind = 'adjudication'"),
        ),
        Index(
            "ix_cr_events_org_batch", "organization_id", "batch_id", "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False,
    )
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rationale_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
