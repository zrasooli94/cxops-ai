"""Turn asynchronous public-chat AI turns on or off for ONE tenant (1P.9R).

Async turns need all three gates: the platform switch
``PUBLIC_CHAT_ASYNC_ENABLED`` (environment), this per-tenant flag, and a widget
that asks for ``delivery: "async"``. This tool changes only the per-tenant
flag, for exactly one organization, and shows the before/after state. It is
DRY-RUN by default; ``--apply`` writes. Optional allowance overrides can be set
in the same step.

    python -m scripts.set_tenant_async_turns --organization-id 7 --enable
    python -m scripts.set_tenant_async_turns --organization-id 7 --enable --apply
    python -m scripts.set_tenant_async_turns --organization-id 7 --disable --apply
    python -m scripts.set_tenant_async_turns --organization-id 7 --turn-limit 3000 \\
        --budget-usd 5 --apply

Running ``--apply`` against production is a production change and needs owner
approval (see docs/runbooks/phase1p9r-release-readiness.md, RISPU activation).
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def apply_change(
    db: AsyncSession,
    *,
    organization_id: int,
    enable: bool | None,
    turn_limit: int | None,
    budget_usd: float | None,
    apply: bool,
) -> list[str]:
    from app.models.public_chat import PublicChatConfiguration

    configs = list(
        (
            await db.execute(
                select(PublicChatConfiguration)
                .where(PublicChatConfiguration.organization_id == organization_id)
                .order_by(PublicChatConfiguration.id)
            )
        ).scalars()
    )
    if not configs:
        return [f"organization {organization_id}: no public chat configuration; nothing to do"]
    lines = []
    for config in configs:
        before = (
            config.async_turns_enabled,
            config.monthly_ai_turn_limit,
            config.monthly_ai_budget_usd,
        )
        if enable is not None:
            config.async_turns_enabled = enable
        if turn_limit is not None:
            config.monthly_ai_turn_limit = turn_limit
        if budget_usd is not None:
            config.monthly_ai_budget_usd = Decimal(str(budget_usd))  # type: ignore[assignment]
        after = (
            config.async_turns_enabled,
            config.monthly_ai_turn_limit,
            config.monthly_ai_budget_usd,
        )
        lines.append(
            f"organization {organization_id} configuration {config.id}: "
            f"async_turns_enabled/turn_limit/budget_usd {before} -> {after}"
        )
    if apply:
        await db.commit()
        lines.append("applied")
    else:
        await db.rollback()
        lines.append("dry run: nothing was written (use --apply)")
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--organization-id", type=int, required=True)
    toggle = parser.add_mutually_exclusive_group()
    toggle.add_argument("--enable", action="store_true")
    toggle.add_argument("--disable", action="store_true")
    parser.add_argument("--turn-limit", type=int, default=None)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.turn_limit is not None and args.turn_limit < 0:
        parser.error("--turn-limit must be >= 0")
    if args.budget_usd is not None and args.budget_usd < 0:
        parser.error("--budget-usd must be >= 0")
    return args


async def _main(args: argparse.Namespace) -> int:
    from app.core.database import AsyncSessionLocal, engine

    enable = True if args.enable else (False if args.disable else None)
    try:
        async with AsyncSessionLocal() as db:
            lines = await apply_change(
                db,
                organization_id=args.organization_id,
                enable=enable,
                turn_limit=args.turn_limit,
                budget_usd=args.budget_usd,
                apply=args.apply,
            )
    finally:
        await engine.dispose()
    print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
