"""C10 synthetic claim-review assignment and append-only audit foundation.

Expansion-only migration following 1q0a0001. No changes to existing tables or
live execution paths. Four new tenant-owned tables, composite same-tenant FKs,
closed checks and database immutability/event-actor triggers. No customer text,
source passages, or freeform review notes stored (SHA256 digests only).

This migration MUST ONLY BE APPLIED in disposable C10 CI by this milestone.
No production/staging migration or activation is authorized.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "1r0a0001"
down_revision: str | Sequence[str] | None = "1q0a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _stamp() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True),
        nullable=False, server_default=sa.func.now(),
    )


def _org() -> sa.Column:
    return sa.Column(
        "organization_id", sa.Integer(),
        sa.ForeignKey("organizations.id"), nullable=False,
    )


def _digest_constraint(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} ~ '^[0-9a-f]{{64}}$'", name=name,
    )


def upgrade() -> None:
    op.create_table(
        "claim_review_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        _org(),
        sa.Column("dataset_id", sa.String(80), nullable=False),
        sa.Column("dataset_digest", sa.String(64), nullable=False),
        sa.Column("created_by_subject", sa.String(255), nullable=False),
        _stamp(),
        sa.UniqueConstraint("id", "organization_id", name="uq_cr_batches_id_org"),
        sa.CheckConstraint(
            "dataset_id = 'c6-fictional-faq-20261021'",
            name="ck_cr_batches_synthetic_only",
        ),
        _digest_constraint("dataset_digest", "ck_cr_batches_digest"),
    )
    op.create_index(
        "ix_cr_batches_org_created", "claim_review_batches",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "claim_review_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        _org(),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("review_key", sa.String(160), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column(
            "source_revisions", JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        _stamp(),
        sa.ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["claim_review_batches.id", "claim_review_batches.organization_id"],
            name="fk_cr_items_batch_org",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", "batch_id", name="uq_cr_items_id_org_batch",
        ),
        sa.UniqueConstraint("batch_id", "review_key", name="uq_cr_items_batch_key"),
        _digest_constraint("fingerprint", "ck_cr_items_fingerprint"),
        _digest_constraint("source_digest", "ck_cr_items_source_digest"),
    )
    op.create_index(
        "ix_cr_items_org_batch", "claim_review_items",
        ["organization_id", "batch_id"],
    )

    op.create_table(
        "claim_review_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        _org(),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("reviewer_subject", sa.String(255), nullable=False),
        sa.Column("assigned_by_subject", sa.String(255), nullable=False),
        _stamp(),
        sa.ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["claim_review_batches.id", "claim_review_batches.organization_id"],
            name="fk_cr_assignments_batch_org",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", "batch_id",
            name="uq_cr_assignments_id_org_batch",
        ),
        sa.UniqueConstraint(
            "batch_id", "slot", name="uq_cr_assignments_batch_slot",
        ),
        sa.UniqueConstraint(
            "batch_id", "reviewer_subject", name="uq_cr_assignments_batch_subject",
        ),
        sa.CheckConstraint("slot IN (1, 2)", name="ck_cr_assignments_slot"),
    )
    op.create_index(
        "ix_cr_assignments_org_subject", "claim_review_assignments",
        ["organization_id", "reviewer_subject"],
    )

    op.create_table(
        "claim_review_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        _org(),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=True),
        sa.Column("event_kind", sa.String(20), nullable=False),
        sa.Column("actor_subject", sa.String(255), nullable=False),
        sa.Column("label", sa.String(20), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=True),
        sa.Column("rationale_digest", sa.String(64), nullable=True),
        _stamp(),
        sa.ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["claim_review_batches.id", "claim_review_batches.organization_id"],
            name="fk_cr_events_batch_org",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "organization_id", "batch_id"],
            [
                "claim_review_items.id", "claim_review_items.organization_id",
                "claim_review_items.batch_id",
            ],
            name="fk_cr_events_item_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id", "organization_id", "batch_id"],
            [
                "claim_review_assignments.id",
                "claim_review_assignments.organization_id",
                "claim_review_assignments.batch_id",
            ],
            name="fk_cr_events_assignment_org_batch",
        ),
        sa.CheckConstraint(
            "event_kind IN ('review', 'adjudication')",
            name="ck_cr_events_kind",
        ),
        sa.CheckConstraint(
            "label IN ('supported', 'contradicted', 'unsupported', 'unclear')",
            name="ck_cr_events_label",
        ),
        sa.CheckConstraint(
            "(event_kind = 'review' AND assignment_id IS NOT NULL) "
            "OR (event_kind = 'adjudication' AND assignment_id IS NULL)",
            name="ck_cr_events_assignment_type",
        ),
        sa.CheckConstraint(
            "((label IN ('supported', 'contradicted') AND "
            "evidence_digest IS NOT NULL) OR "
            "(label IN ('unsupported', 'unclear') AND rationale_digest IS NOT NULL))",
            name="ck_cr_events_evidence_requirement",
        ),
        sa.CheckConstraint(
            "evidence_digest IS NULL OR evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cr_events_evidence_digest",
        ),
        sa.CheckConstraint(
            "rationale_digest IS NULL OR rationale_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cr_events_rationale_digest",
        ),
    )
    op.create_index(
        "uq_cr_events_review_item_assignment", "claim_review_events",
        ["item_id", "assignment_id"], unique=True,
        postgresql_where=sa.text("event_kind = 'review'"),
    )
    op.create_index(
        "uq_cr_events_adjudication_item", "claim_review_events",
        ["item_id"], unique=True,
        postgresql_where=sa.text("event_kind = 'adjudication'"),
    )
    op.create_index(
        "ix_cr_events_org_batch", "claim_review_events",
        ["organization_id", "batch_id", "created_at"],
    )

    # Database-side immutability and separation are defense in depth for
    # application logic; ordinary roles cannot rewrite audit records.
    op.execute("""
        CREATE FUNCTION c10_claim_review_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'C10 audit records are append-only'
                USING ERRCODE = 'integrity_constraint_violation';
        END
        $$;
    """)
    for table in (
        "claim_review_batches", "claim_review_items",
        "claim_review_assignments", "claim_review_events",
    ):
        op.execute(f"""
            CREATE TRIGGER tr_c10_immutable BEFORE UPDATE OR DELETE
            ON {table} FOR EACH ROW
            EXECUTE FUNCTION c10_claim_review_immutable();
        """)

    op.execute("""
        CREATE FUNCTION c10_claim_review_event_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            assigned_subject TEXT;
            assignment_count INTEGER;
            previous_count INTEGER;
            different_labels INTEGER;
        BEGIN
            IF NEW.event_kind = 'review' THEN
                SELECT reviewer_subject INTO assigned_subject
                FROM claim_review_assignments
                WHERE id = NEW.assignment_id
                  AND organization_id = NEW.organization_id
                  AND batch_id = NEW.batch_id;
                IF assigned_subject IS NULL
                   OR assigned_subject <> NEW.actor_subject THEN
                    RAISE EXCEPTION 'C10 reviewer assignment actor mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            ELSIF NEW.event_kind = 'adjudication' THEN
                SELECT COUNT(*) INTO assignment_count
                FROM claim_review_assignments
                WHERE organization_id = NEW.organization_id
                  AND batch_id = NEW.batch_id;
                IF assignment_count <> 2 OR EXISTS (
                    SELECT 1 FROM claim_review_assignments
                    WHERE organization_id = NEW.organization_id
                      AND batch_id = NEW.batch_id
                      AND reviewer_subject = NEW.actor_subject
                ) THEN
                    RAISE EXCEPTION 'C10 adjudicator must be third reviewer'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT COUNT(*), COUNT(DISTINCT label)
                  INTO previous_count, different_labels
                FROM claim_review_events
                WHERE organization_id = NEW.organization_id
                  AND batch_id = NEW.batch_id
                  AND item_id = NEW.item_id
                  AND event_kind = 'review';
                IF previous_count <> 2 OR different_labels <> 2 THEN
                    RAISE EXCEPTION 'C10 adjudication requires two disputed reviews'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$;
    """)
    op.execute("""
        CREATE TRIGGER tr_c10_event_identity BEFORE INSERT
        ON claim_review_events FOR EACH ROW
        EXECUTE FUNCTION c10_claim_review_event_identity();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER tr_c10_event_identity ON claim_review_events")
    op.execute("DROP FUNCTION c10_claim_review_event_identity()")
    for table in (
        "claim_review_events", "claim_review_assignments",
        "claim_review_items", "claim_review_batches",
    ):
        op.execute(f"DROP TRIGGER tr_c10_immutable ON {table}")
    op.execute("DROP FUNCTION c10_claim_review_immutable()")
    op.drop_table("claim_review_events")
    op.drop_table("claim_review_assignments")
    op.drop_table("claim_review_items")
    op.drop_table("claim_review_batches")
