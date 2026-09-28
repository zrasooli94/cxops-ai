"""one public chat config per tenant

Revision ID: 1p4a0001
Revises: 1p2a0001
Create Date: 2026-09-27 14:00:00.000000

Phase 1P.4 production invariant: ``public_chat_configurations.organization_id``
becomes UNIQUE, so a tenant can hold exactly one widget configuration.

Until this revision the only uniqueness on this table was
``public_widget_key_hash``, so nothing stopped a second row for the same tenant.
Nothing surfaced it: the onboarding planner already refused to repair a
multi-row tenant (it raises ``OnboardingConflictError`` rather than picking a
winner), and ``PublicChatConfiguration`` rows are only ever written by
onboarding. The invariant was therefore enforced *only* in application code, on
one code path. A second writer - a migration, a manual fix, a script run against
production, a future admin tool - would have created a second widget row that no
service-level guard covered, and the tenant's widget key lookup would then have
had two valid answers.

The database is the correct place for this invariant, because it has to hold for
every writer, including the ones nobody has written yet.

Fail-safe design
----------------
Duplicates are never silently deleted, merged, or resolved. The upgrade runs a
deterministic precheck first and, if it finds any, raises with the exact
offending ``organization_id`` values and row counts and changes nothing. The
operator gets a decision to make, not a surprise.

The precheck exists because the bare ``CREATE UNIQUE INDEX`` would *also* fail
safely, but only with a constraint-violation error that names the key and not
the tenants. On a production database an operator needs to know *which tenants*
to go and look at.

Ordering
--------
The constraint is created before the now-redundant plain index on the same
column is dropped. If the precheck or the constraint fails, the migration stops
with the original index still in place, so a failed attempt never leaves the
table less indexed than it found it.

Redundant index
---------------
``ix_public_chat_configurations_organization_id`` is dropped because a unique
constraint on the same single column creates its own index with identical
lookups. Keeping both would make every insert and update of this table maintain
two identical structures. This is part of the same change, not a separate one.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1p4a0001"
down_revision: str | Sequence[str] | None = "1p2a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINT_NAME = "ux_public_chat_configurations_organization_id"
REDUNDANT_INDEX_NAME = "ix_public_chat_configurations_organization_id"


def _find_duplicate_organizations(conn: sa.Connection) -> list[tuple[int, int]]:
    """Return ``(organization_id, row_count)`` for every tenant with >1 config.

    The same query operators run by hand in production; see
    ``docs/runbooks/a1-live-pilot.md``.
    """
    rows = conn.execute(
        sa.text(
            """
            SELECT organization_id, COUNT(*) AS config_count
            FROM public_chat_configurations
            GROUP BY organization_id
            HAVING COUNT(*) > 1
            ORDER BY organization_id
            """
        )
    ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


def upgrade() -> None:
    """Enforce one public chat configuration per tenant.

    Raises before mutating anything if the table already contains duplicates.
    """
    bind = op.get_bind()
    duplicates = _find_duplicate_organizations(bind)

    if duplicates:
        detail = ", ".join(
            f"organization_id={org_id} ({count} rows)" for org_id, count in duplicates
        )
        raise RuntimeError(
            "cannot enforce one public chat configuration per tenant: "
            f"public_chat_configurations contains duplicates for {detail}. "
            "No rows were changed. Resolve each tenant deliberately - pick the "
            "row to keep, confirm its public_widget_key_hash is the key the "
            "customer's live embed snippet actually uses, re-point any sessions "
            "that reference the rows you remove, then delete the others - and "
            "re-run this migration. See docs/runbooks/a1-live-pilot.md "
            "(MIGRATE)."
        )

    op.create_unique_constraint(
        CONSTRAINT_NAME,
        "public_chat_configurations",
        ["organization_id"],
    )

    # Only reached once the constraint exists, so the table is never left with
    # less indexing than it started with.
    op.drop_index(REDUNDANT_INDEX_NAME, table_name="public_chat_configurations")


def downgrade() -> None:
    """Relax back to "one or more configurations per tenant".

    The index is restored *before* the constraint is dropped, so the table is
    never momentarily unindexed on a column it still needs to look up.
    """
    op.create_index(
        REDUNDANT_INDEX_NAME,
        "public_chat_configurations",
        ["organization_id"],
    )
    op.drop_constraint(
        CONSTRAINT_NAME,
        "public_chat_configurations",
        type_="unique",
    )
