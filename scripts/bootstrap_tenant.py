"""Bootstrap an organization membership for a development user.

Usage:
    python -m scripts.bootstrap_tenant \
        --subject <nhost-subject> \
        --organization-id <id> \
        --role owner|admin|supervisor|agent|viewer

Idempotent: re-running with the same (organization_id, subject) is a no-op.

The role must be explicit to avoid silent privilege escalation. Roles are
organization-scoped CXOps roles; they are not Nhost/Hasura roles.
"""

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.rbac import OrganizationRole
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership

log = get_logger(__name__)

VALID_ROLES = ", ".join(sorted(OrganizationRole.values()))


async def bootstrap(
    *,
    subject: str,
    organization_id: int,
    role: OrganizationRole,
) -> str:
    async with AsyncSessionLocal() as db:
        organization = await db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        if organization.scalar_one_or_none() is None:
            raise SystemExit(f"error: organization {organization_id} does not exist")

        result = await db.execute(
            pg_insert(OrganizationMembership)
            .values(
                subject=subject,
                organization_id=organization_id,
                role=role.value,
            )
            .on_conflict_do_nothing(
                constraint="uq_organization_memberships_organization_id_subject",
            )
        )
        await db.commit()

        if result.rowcount == 0:  # type: ignore[attr-defined]
            return (
                f"membership already exists: "
                f"subject={subject} organization_id={organization_id}"
            )
        return (
            f"membership created: subject={subject} "
            f"organization_id={organization_id} role={role.value}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an organization membership")
    parser.add_argument(
        "--subject", required=True, help="Nhost JWT sub (external user id)"
    )
    parser.add_argument(
        "--organization-id",
        required=True,
        type=int,
        help="CXOps organization id to grant access to",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=sorted(OrganizationRole.values()),
        help=f"CXOps organization role ({VALID_ROLES})",
    )
    args = parser.parse_args()

    result = asyncio.run(
        bootstrap(
            subject=args.subject,
            organization_id=args.organization_id,
            role=OrganizationRole(args.role),
        )
    )
    print(result)
    log.info("tenant_bootstrap", result=result)


if __name__ == "__main__":
    main()
