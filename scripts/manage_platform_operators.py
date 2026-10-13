"""Grant, revoke or list CXOps platform operators (Phase 1Q).

Platform operators review early-access leads, approve tenants and set AI
entitlements across every organization. This script is the ONLY way to grant
that authority: there is deliberately no HTTP endpoint for it. A grant is keyed
by the operator's Nhost user id (the JWT ``sub``), never by an email address.

    python -m scripts.manage_platform_operators list
    python -m scripts.manage_platform_operators grant --subject <nhost-user-uuid> \\
        --granted-by "<your name>" --note "support rota"            # dry run
    python -m scripts.manage_platform_operators grant --subject <uuid> \\
        --granted-by "<your name>" --apply
    python -m scripts.manage_platform_operators revoke --subject <uuid> \\
        --revoked-by "<your name>" --apply

``grant`` and ``revoke`` are DRY-RUN by default. Running ``--apply`` against
production is a production change and needs owner approval (see
docs/runbooks/phase1q-commercial-launch.md).
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession


def _validate_subject(value: str) -> str:
    candidate = value.strip()
    try:
        uuid.UUID(candidate)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "subject must be the operator's Nhost user id (a UUID), not an email"
        ) from exc
    return candidate


async def run(db: AsyncSession, args: argparse.Namespace) -> list[str]:
    from app.services.platform_operator_service import PlatformOperatorService

    if args.command == "list":
        rows = await PlatformOperatorService.list_all(db)
        if not rows:
            return ["no platform operators"]
        return [
            f"{row.id}\t{row.subject}\t"
            f"{'revoked ' + row.revoked_at.isoformat() if row.revoked_at else 'active'}\t"
            f"granted_by={row.granted_by}\tnote={row.note or ''}"
            for row in rows
        ]

    existing = await PlatformOperatorService.resolve(db, args.subject)
    if args.command == "grant":
        if existing is not None:
            return [f"{args.subject}: already an active platform operator; nothing to do"]
        if not args.apply:
            return [f"dry run: would grant platform operator to {args.subject} (use --apply)"]
        _row, created = await PlatformOperatorService.grant(
            db, subject=args.subject, granted_by=args.granted_by, note=args.note
        )
        return [f"{args.subject}: granted" if created else f"{args.subject}: already granted"]

    if existing is None:
        return [f"{args.subject}: not an active platform operator; nothing to do"]
    if not args.apply:
        return [f"dry run: would revoke platform operator from {args.subject} (use --apply)"]
    await PlatformOperatorService.revoke(db, subject=args.subject, revoked_by=args.revoked_by)
    return [f"{args.subject}: revoked"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    grant = sub.add_parser("grant")
    grant.add_argument("--subject", type=_validate_subject, required=True)
    grant.add_argument("--granted-by", required=True)
    grant.add_argument("--note", default=None)
    grant.add_argument("--apply", action="store_true")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--subject", type=_validate_subject, required=True)
    revoke.add_argument("--revoked-by", required=True)
    revoke.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    from app.core.database import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            lines = await run(db, args)
    finally:
        await engine.dispose()
    print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
