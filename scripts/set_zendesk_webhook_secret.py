"""Provision or clear an organization's Zendesk webhook signing secret.

Usage:
    export CXOPS_ZD_WEBHOOK_SECRET='...'
    python -m scripts.set_zendesk_webhook_secret --organization-id 7 --secret-env CXOPS_ZD_WEBHOOK_SECRET

    python -m scripts.set_zendesk_webhook_secret --organization-id 7 --clear

The secret is read from the named environment variable at runtime so it never
appears in argv (and therefore not in shell history or the process list).
The secret value is never printed or logged.

Zendesk webhooks fail closed (HTTP 401) until a per-organization secret is
provisioned. This script is an administrative bootstrap for that secret. In
production this should be replaced by a managed rotation mechanism (e.g. a
secrets-manager-backed provisioning endpoint behind RBAC) rather than static
environment-injected secrets; the webhook replay window still applies
regardless of how the secret is stored.

Idempotent per organization: re-running replaces the existing secret in place.
"""

import argparse
import asyncio
import os

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.organization import Organization
from app.repositories.zendesk_oauth_token_repository import (
    ZendeskOAuthTokenRepository,
)

log = get_logger(__name__)


def _read_secret(env_var_name: str) -> str:
    secret = os.environ.get(env_var_name, "")
    secret = secret.strip() if secret else ""
    if not secret:
        raise SystemExit(
            f"error: environment variable {env_var_name} is not set or is empty"
        )
    return secret


async def provision(
    *,
    organization_id: int,
    webhook_secret: str,
) -> None:
    async with AsyncSessionLocal() as db:
        organization = await db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        if organization.scalar_one_or_none() is None:
            raise SystemExit(
                f"error: organization {organization_id} does not exist"
            )

        token = await ZendeskOAuthTokenRepository.set_webhook_secret(
            db=db,
            organization_id=organization_id,
            webhook_secret=webhook_secret,
        )
        if token is None:
            raise SystemExit(
                f"error: organization {organization_id} has no Zendesk connection"
            )


async def clear(
    *,
    organization_id: int,
) -> None:
    async with AsyncSessionLocal() as db:
        organization = await db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        if organization.scalar_one_or_none() is None:
            raise SystemExit(
                f"error: organization {organization_id} does not exist"
            )

        token = await ZendeskOAuthTokenRepository.set_webhook_secret(
            db=db,
            organization_id=organization_id,
            webhook_secret=None,
        )
        if token is None:
            raise SystemExit(
                f"error: organization {organization_id} has no Zendesk connection"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Set or clear an organization's Zendesk webhook signing secret. "
            "The secret value is supplied via an environment variable, never via argv."
        )
    )
    parser.add_argument(
        "--organization-id",
        required=True,
        type=int,
        help="CXOps organization id owning the Zendesk connection",
    )
    parser.add_argument(
        "--secret-env",
        default=None,
        help="Environment variable holding the webhook secret to provision",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear the organization's configured webhook secret instead of setting one",
    )
    args = parser.parse_args()

    if args.organization_id <= 0:
        parser.error("--organization-id must be a positive integer")

    if bool(args.secret_env) == bool(args.clear):
        parser.error("provide exactly one of --secret-env <VAR> or --clear")

    if args.clear:
        asyncio.run(clear(organization_id=args.organization_id))
        log.info(
            "zendesk_webhook_secret_cleared",
            organization_id=args.organization_id,
        )
        print(f"webhook secret cleared for organization {args.organization_id}")
    else:
        webhook_secret = _read_secret(args.secret_env)
        asyncio.run(
            provision(
                organization_id=args.organization_id,
                webhook_secret=webhook_secret,
            )
        )
        log.info(
            "zendesk_webhook_secret_provisioned",
            organization_id=args.organization_id,
            secret_provisioned=True,
        )
        print(f"webhook secret provisioned for organization {args.organization_id}")


if __name__ == "__main__":
    main()