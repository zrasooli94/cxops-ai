"""Apply the lead data-retention policy (Phase 1Q-R).

Deletes early-access leads past their retention window. DRY-RUN by default;
``--apply`` deletes. Organizations created from approved leads are never
touched - only the lead records.

    python -m scripts.purge_expired_leads
    python -m scripts.purge_expired_leads --apply

Defaults follow the *proposed* policy in docs/legal/DRAFT-privacy-policy.md
(declined 90 days, approved 365 days, unanswered open requests 180 days).
The policy is pending owner/legal approval; running ``--apply`` against
production needs owner approval.
"""

from __future__ import annotations

import argparse
import asyncio


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--declined-days", type=int, default=90)
    parser.add_argument("--approved-days", type=int, default=365)
    parser.add_argument("--stale-open-days", type=int, default=180)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    for name in ("declined_days", "approved_days", "stale_open_days"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    return args


async def _main(args: argparse.Namespace) -> int:
    from app.core.database import AsyncSessionLocal, engine
    from app.services.lead_service import LeadService

    try:
        async with AsyncSessionLocal() as db:
            counts = await LeadService.purge_expired(
                db,
                declined_days=args.declined_days,
                approved_days=args.approved_days,
                stale_open_days=args.stale_open_days,
                apply=args.apply,
            )
    finally:
        await engine.dispose()
    verb = "deleted" if args.apply else "would delete (dry run; use --apply)"
    print(", ".join(f"{name}: {count}" for name, count in counts.items()) + f" - {verb}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
