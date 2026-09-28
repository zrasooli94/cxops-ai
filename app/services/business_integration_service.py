"""Tenant/provider scoping for business tools (Phase 1P.2).

A tool's provider must be enabled for the tenant before the agent workflow
may propose it or the worker may execute it. Provider-less core tools
(``customer.send_reply``) are always enabled — they are the generic
conversation surface of the product, not a provider integration.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_integration import BusinessIntegrationConfiguration
from app.tools.registry import business_tool_registry


class BusinessIntegrationError(Exception):
    pass


class BusinessIntegrationService:
    @staticmethod
    async def enabled_tool_names(
        db: AsyncSession,
        organization_id: int,
    ) -> set[str]:
        """Names of business tools enabled for an organization.

        Fails closed: tools whose provider is not in the enabled set (or whose
        provider is not yet supported at all) are never offered or executed.
        """
        defs = business_tool_registry.definitions()
        core = {d.name for d in defs if d.provider is None}

        result = await db.execute(
            select(BusinessIntegrationConfiguration.provider).where(
                BusinessIntegrationConfiguration.organization_id == organization_id,
                BusinessIntegrationConfiguration.enabled.is_(True),
            )
        )
        enabled_providers = {row for (row,) in result.all()}

        provider_tools = {
            d.name for d in defs if d.provider is not None and d.provider in enabled_providers
        }
        return core | provider_tools

    @staticmethod
    async def list_configs(
        db: AsyncSession,
        organization_id: int,
    ) -> list[BusinessIntegrationConfiguration]:
        """Every provider configuration row for an organization.

        Callers that need to know how a provider is wired (rather than merely
        whether its tools are offered) read ``config_json`` from here instead of
        querying this table themselves, so provider scoping stays in one place.
        """
        result = await db.execute(
            select(BusinessIntegrationConfiguration).where(
                BusinessIntegrationConfiguration.organization_id == organization_id,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_config(
        db: AsyncSession,
        organization_id: int,
        provider: str,
    ) -> BusinessIntegrationConfiguration | None:
        result = await db.execute(
            select(BusinessIntegrationConfiguration).where(
                BusinessIntegrationConfiguration.organization_id == organization_id,
                BusinessIntegrationConfiguration.provider == provider,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def set_provider_enabled(
        db: AsyncSession,
        organization_id: int,
        provider: str,
        enabled: bool,
        config_json: dict | None = None,
    ) -> BusinessIntegrationConfiguration:
        """Idempotently create/update the enablement row for a provider."""
        config = await BusinessIntegrationService.get_config(
            db,
            organization_id,
            provider,
        )
        if config is None:
            config = BusinessIntegrationConfiguration(
                organization_id=organization_id,
                provider=provider,
                enabled=enabled,
                config_json=config_json or {},
            )
            db.add(config)
        else:
            config.enabled = enabled
            if config_json is not None:
                config.config_json = config_json
        await db.flush()
        return config