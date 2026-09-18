"""customer <-> ticket tenant-safe relational integrity

Revision ID: 1e2a0001
Revises: 1d3a0001
Create Date: 2026-09-18 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e2a0001"
down_revision: str | Sequence[str] | None = "1d3a0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CUSTOMER_COMPOSITE_UNIQUE = "ux_customers_id_organization_id"
_COMPOSITE_FK = "fk_tickets_customer_id_organization_id_customers"
_LEGACY_FK = "tickets_customer_id_fkey"
_TICKET_CUSTOMER_INDEX = "ix_tickets_customer_id"

# Preflight audit for rows that the new composite foreign key would either
# reject or leave unenforced under MATCH SIMPLE. It only ever SELECTS counts:
# violating rows are never repaired and no customer PII is read out.
#
#   cross_org_link                      - ticket.customer_id points at a
#                                         customer owned by another org (or a
#                                         customer with a NULL org).
#   customer_link_without_ticket_org    - ticket has a customer_id but no
#                                         organization_id, so MATCH SIMPLE
#                                         would silently exempt the row.
#   customer_link_to_null_org_customer  - referenced customer itself has a
#                                         NULL organization_id.
_PREFLIGHT_SQL = sa.text(
    """
    SELECT
        COUNT(*) FILTER (
            WHERE t.customer_id IS NOT NULL
              AND t.organization_id IS NOT NULL
              AND (
                    c.organization_id IS NULL
                    OR c.organization_id <> t.organization_id
              )
        ) AS cross_org_link,
        COUNT(*) FILTER (
            WHERE t.customer_id IS NOT NULL
              AND t.organization_id IS NULL
        ) AS customer_link_without_ticket_org,
        COUNT(*) FILTER (
            WHERE t.customer_id IS NOT NULL
              AND c.organization_id IS NULL
        ) AS customer_link_to_null_org_customer
    FROM tickets AS t
    LEFT JOIN customers AS c ON c.id = t.customer_id
    """
)


def audit_violations(bind: Connection) -> dict[str, int]:
    """Return counts of rows that violate the tenant invariant (no PII)."""

    row = bind.execute(_PREFLIGHT_SQL).mappings().one()

    return {
        "cross_org_link": int(row["cross_org_link"]),
        "customer_link_without_ticket_org": int(
            row["customer_link_without_ticket_org"]
        ),
        "customer_link_to_null_org_customer": int(
            row["customer_link_to_null_org_customer"]
        ),
    }


def upgrade() -> None:
    """Enforce tenant-consistent ticket/customer ownership in the database.

    ``tickets.customer_id`` was a single-column FK, so only the service layer
    prevented an Org A ticket from referencing an Org B customer. This makes
    the invariant a database constraint:

    - ``UNIQUE (id, organization_id)`` on ``customers`` - the composite target
      for the ticket FK (``id`` alone is already the PK, so this is redundant
      for uniqueness and exists solely as the FK target, matching the tickets
      / knowledge document pattern).
    - the ticket's customer reference becomes a composite foreign key
      ``(customer_id, organization_id) -> customers(id, organization_id)``.

    MATCH SIMPLE keeps ``customer_id IS NULL`` tickets exempt, so unlinked
    tickets remain valid. The FK is created with NO ACTION (the default): there
    is no customer delete path, and default composite SET NULL would null
    ``organization_id`` and destroy the ticket's tenant ownership.
    """

    bind = op.get_bind()

    violations = audit_violations(bind)

    if any(violations.values()):
        raise RuntimeError(
            "Aborting customer/ticket tenant-integrity migration: existing "
            f"rows violate the invariant (counts={violations}). No data was "
            "modified. Resolve these rows explicitly before retrying."
        )

    op.create_unique_constraint(
        _CUSTOMER_COMPOSITE_UNIQUE,
        "customers",
        ["id", "organization_id"],
    )

    op.drop_constraint(
        _LEGACY_FK,
        "tickets",
        type_="foreignkey",
    )

    op.create_foreign_key(
        _COMPOSITE_FK,
        "tickets",
        "customers",
        ["customer_id", "organization_id"],
        ["id", "organization_id"],
    )

    op.create_index(
        _TICKET_CUSTOMER_INDEX,
        "tickets",
        ["customer_id"],
        unique=False,
    )


def downgrade() -> None:
    """Restore the single-column customer FK (dropping tenant consistency)."""

    op.drop_index(
        _TICKET_CUSTOMER_INDEX,
        table_name="tickets",
    )

    op.drop_constraint(
        _COMPOSITE_FK,
        "tickets",
        type_="foreignkey",
    )

    op.create_foreign_key(
        _LEGACY_FK,
        "tickets",
        "customers",
        ["customer_id"],
        ["id"],
    )

    op.drop_constraint(
        _CUSTOMER_COMPOSITE_UNIQUE,
        "customers",
        type_="unique",
    )
