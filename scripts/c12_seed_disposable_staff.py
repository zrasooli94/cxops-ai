"""C12 GitHub-CI-only fictional memberships for real browser/API integration.

Run AFTER C2 exact disposable guard and Alembic migration. No production
accounts, customer/source passages or provider API calls. Uses no real keys.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from scripts.chatbot_c2_disposable_guard import require_c2_disposable_environment

require_c2_disposable_environment(os.environ)
if (
    os.environ.get("CLAIM_REVIEW_SYNTHETIC_API_ENABLED") != "true"
    or os.environ.get("CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED") != "true"
    or os.environ.get("AUTH_JWT_SECRET") != "c" * 32
):
    raise SystemExit("C12 seed refused: invalid synthetic feature/auth environment")

from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership

PROFILE = [
    ("c12-owner", OrganizationRole.OWNER),
    ("c12-r-a", OrganizationRole.ADMIN),
    ("c12-r-b", OrganizationRole.SUPERVISOR),
    ("c12-third", OrganizationRole.ADMIN),
    ("c12-viewer", OrganizationRole.VIEWER),
    ("c12-both", OrganizationRole.OWNER),
]


async def seed() -> dict:
    async with AsyncSessionLocal() as db:
        a = Organization(name="C12 Synthetic Aster Bicycle Workshop")
        b = Organization(name="C12 Synthetic Birch Print Studio")
        db.add_all([a, b])
        await db.flush()
        db.add_all([
            OrganizationMembership(
                organization_id=a.id, subject=subject, role=role,
            )
            for subject, role in PROFILE
        ])
        db.add(
            OrganizationMembership(
                organization_id=b.id, subject="c12-both", role=OrganizationRole.OWNER,
            )
        )
        await db.commit()
        return {"org_a": a.id, "org_b": b.id, "synthetic": True}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("C12 seed requires exact temp output path")
    destination = Path(sys.argv[1])
    if not destination.parent.is_dir() or destination.exists():
        raise SystemExit("C12 seed refuses missing folder or existing result")
    payload = asyncio.run(seed())
    with destination.open("x", encoding="utf-8") as target:
        json.dump(payload, target)
    print("C12 synthetic-only memberships seeded in disposable database.")
