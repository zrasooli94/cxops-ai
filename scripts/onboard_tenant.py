"""Onboard a tenant from a declarative manifest (Phase 1P.3).

Usage
-----
Preview without writing anything::

    .venv/bin/python scripts/onboard_tenant.py \
        --manifest config/tenants/a1-cash-for-cars.yaml --dry-run

Apply (idempotent)::

    .venv/bin/python scripts/onboard_tenant.py \
        --manifest config/tenants/a1-cash-for-cars.yaml

Rotate the public widget key (the previous key stops working immediately)::

    .venv/bin/python scripts/onboard_tenant.py \
        --manifest config/tenants/a1-cash-for-cars.yaml --rotate-widget-key

Behaviour worth knowing before you run this
------------------------------------------
* The manifest is validated completely before any database contact. Credential
  material and unknown fields are refused, not warned about.
* ``--dry-run`` performs zero writes, including zero key generation, so a
  preview never prints a key that could not work anyway.
* Without ``--rotate-widget-key`` an existing widget key is preserved, so a
  re-apply does not break a live website embed.
* Only the raw public widget key is ever printed. That key is public by design
  (it ships in customer-facing HTML) and is printed at most once, on creation or
  rotation. Session tokens, integration secrets, and connection strings are
  never printed or logged.
* Knowledge documents are ingested after the tenant transaction commits;
  ingestion is checksum-deduplicated, so a re-run converges.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.tenant_onboarding import (
    ManifestError,
    apply_plan,
    build_embed_snippet,
    build_plan,
    load_manifest_file,
)
from app.tenant_onboarding.planner import list_tenant_widget_keys_status

log = get_logger(__name__)

WIDGET_KEY_PLACEHOLDER = "<PUBLIC_WIDGET_KEY>"


def _print_header(manifest_path: str, slug: str, dry_run: bool) -> None:
    mode = "DRY RUN (no writes)" if dry_run else "APPLY"
    print(f"tenant onboarding - {mode}")
    print(f"  manifest: {manifest_path}")
    print(f"  tenant:   {slug}")


def _print_plan(plan) -> None:
    counts = plan.summary_counts()
    rendered = ", ".join(
        f"{name.lower()}={counts[name]}" for name in sorted(counts) if counts[name]
    )
    print("plan:")
    for line in plan.describe():
        print(line)
    print(f"  summary: {rendered or 'nothing to do'}")


def _print_widget_state(status: dict) -> None:
    if status.get("ambiguous"):
        print(
            f"  widget:   AMBIGUOUS ({status.get('widget_rows')} rows exist) - "
            "resolve by hand before re-running"
        )
        return
    if not status.get("widget_rows"):
        print("  widget:   none (a key is minted on apply)")
        return
    state = "enabled" if status.get("enabled") else "disabled"
    print(
        f"  widget:   {state}, key_present={status.get('has_key')}, "
        f"origins={status.get('origin_count', 0)}"
    )


def _print_embed(frontend_base_url: str, display_name: str, key: str | None) -> None:
    print("embed snippet:")
    print(
        build_embed_snippet(
            frontend_base_url,
            key or WIDGET_KEY_PLACEHOLDER,
            display_name,
        )
    )
    if key is None:
        print(
            "  (the key is stored only as a SHA-256 digest; paste the key you "
            "received at creation, or re-run with --rotate-widget-key for a new one)"
        )


def _fail(message: str, *, traceback_enabled: bool = False) -> int:
    """Report a failure without echoing potentially sensitive exception text.

    Database and driver exceptions routinely embed the connection URL, and this
    output is routinely captured by CI. The exception *type* is actionable; the
    full traceback is available to the operator on their own terminal via
    ``--traceback``.
    """
    print(f"error: {message}", file=sys.stderr)
    log.error("tenant_onboarding_failed", reason=message)
    return 2


async def _run(args: argparse.Namespace) -> int:
    frontend_base_url = (
        args.frontend_base_url.rstrip("/")
        if args.frontend_base_url
        else settings.frontend_base_url
    )

    try:
        manifest = load_manifest_file(args.manifest)
    except ManifestError as exc:
        print(f"error: manifest {args.manifest} was rejected:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        log.error(
            "tenant_manifest_rejected",
            manifest_path=str(args.manifest),
            problem_count=len(exc.problems),
        )
        return 2

    _print_header(str(args.manifest), manifest.slug, args.dry_run)
    if manifest.pilot is not None:
        print(f"  pilot:    {manifest.pilot.state}")

    async with AsyncSessionLocal() as db:
        plan = await build_plan(db, manifest, rotate_widget_key=args.rotate_widget_key)
        _print_plan(plan)

        if plan.has_errors:
            error_count = sum(1 for action in plan.actions if action.is_error)
            log.error(
                "tenant_onboarding_plan_has_errors",
                slug=manifest.slug,
                error_count=error_count,
            )
            return 1

        if args.dry_run:
            if plan.is_noop:
                print("\nresult: UNCHANGED - the tenant already matches this manifest.")
            else:
                print(
                    "\nresult: DRY RUN - re-run without --dry-run to apply. "
                    "No keys, secrets, or rows were created."
                )
            if plan.organization_id is not None:
                _print_widget_state(
                    await list_tenant_widget_keys_status(db, plan.organization_id)
                )
            if manifest.public_chat is not None:
                _print_embed(
                    frontend_base_url,
                    manifest.public_chat.display_name,
                    None,
                )
            return 0

        if plan.is_noop and not args.rotate_widget_key:
            print("\nresult: UNCHANGED - the tenant already matches this manifest.")
            return 0

        result = await apply_plan(
            db,
            manifest,
            plan,
            rotate_widget_key=args.rotate_widget_key,
        )

    print(f"\nresult: APPLIED (organization_id={result.organization_id})")
    if result.knowledge_ingested or result.knowledge_duplicates:
        print(
            f"  knowledge: {result.knowledge_ingested} ingested, "
            f"{result.knowledge_duplicates} already present"
        )

    if result.widget_key_created:
        print("\n*** public widget key (printed once - store it now) ***")
        print(result.public_widget_key)
    elif result.widget_key_rotated:
        print("\n*** public widget key rotated (printed once - store it now) ***")
        print("the previous key no longer resolves to this tenant")
        print(result.public_widget_key)

    if manifest.public_chat is not None:
        _print_embed(
            frontend_base_url,
            manifest.public_chat.display_name,
            result.public_widget_key,
        )

    log.info(
        "tenant_onboarding_applied",
        slug=manifest.slug,
        organization_id=result.organization_id,
        knowledge_ingested=result.knowledge_ingested,
        knowledge_duplicates=result.knowledge_duplicates,
        widget_key_created=result.widget_key_created,
        widget_key_rotated=result.widget_key_rotated,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create or update a tenant from a declarative manifest. Idempotent: "
            "re-running changes nothing and preserves the public widget key."
        )
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to the tenant manifest YAML file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the plan without writing anything",
    )
    parser.add_argument(
        "--rotate-widget-key",
        action="store_true",
        help=(
            "Explicitly mint a new public widget key. The previous key stops "
            "working immediately and embedded widgets must be re-published."
        ),
    )
    parser.add_argument(
        "--frontend-base-url",
        default=None,
        help=(
            "Override FRONTEND_BASE_URL when building the embed snippet, "
            "e.g. https://app.example.com"
        ),
    )
    parser.add_argument(
        "--traceback",
        action="store_true",
        help=(
            "Print the full traceback on failure. Output is written to your "
            "terminal only and is never logged."
        ),
    )
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(_run(args))
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as exc:  # noqa: BLE001 - operator-facing CLI boundary
        if args.traceback:
            import traceback

            traceback.print_exc()
        # Deliberately reports the type only: the message of a database or
        # driver error can contain the connection string.
        exit_code = _fail(
            f"{type(exc).__name__} (re-run with --traceback for details)"
        )

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
