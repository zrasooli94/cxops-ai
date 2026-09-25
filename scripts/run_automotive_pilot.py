"""Phase 1M — optional replay of the real agent workflow on pilot tickets.

Runs the existing ``AgentWorkflowService.analyze`` over selected local (demo)
pilot tickets so the pipeline's real Coordinator / Knowledge / Action
specialists can be demonstrated. This is strictly opt-in:

- Only tickets belonging to the pilot tenant (by exact name) are analyzed.
- External execution is never attempted here; ``allow_auto_queue`` defaults
  to False and is only enabled with an explicit ``--allow-auto-queue`` flag.
  Approval and authorization flows are otherwise unchanged.

No synthetic result is created: this script only exercises the real workflow
path and persists whatever the pipeline actually decides.
"""

import argparse
import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization
from app.models.ticket import Ticket
from app.services.agent_workflow_service import agent_workflow_service
from scripts.automotive_pilot_data import (
    OPTIONAL_REPLAY_REFS,
    PILOT_ORGANIZATION_NAME,
    TICKET_BY_REF,
    validate_catalog,
)


async def _pilot_org_id(db) -> int | None:
    result = await db.execute(
        select(Organization.id).where(Organization.name == PILOT_ORGANIZATION_NAME)
    )
    return result.scalar_one_or_none()


def _normalize_ref(ref: str) -> str:
    return ref.split("a1-", 1)[-1]


async def main(
    *,
    ticket_refs: list[str],
    allow_auto_queue: bool,
    organization_id: int | None,
    dry_run: bool,
) -> list[dict]:
    validate_catalog()
    async with AsyncSessionLocal() as db:
        org_id = organization_id
        if org_id is None:
            resolved = await _pilot_org_id(db)
            assert resolved is not None, (
                "could not resolve a pilot organization; pass --organization-id"
            )
            org_id = resolved
        print("pilot organization_id:", org_id)

        results: list[dict] = []
        for ref in ticket_refs:
            external_id = f"a1-{_normalize_ref(ref)}"
            ticket_result = await db.execute(
                select(Ticket).where(
                    Ticket.organization_id == org_id,
                    Ticket.external_id == external_id,
                )
            )
            ticket = ticket_result.scalar_one_or_none()
            if ticket is None:
                print(f"skip {external_id}: ticket not found in pilot tenant")
                continue
            if ticket.status == "solved":
                print(f"skip {external_id}: ticket resolved")
                continue
            print(f"analyzing {external_id} (ticket {ticket.id}) ...")
            if dry_run:
                results.append(
                    {"ticket_id": ticket.id, "external_id": external_id,
                     "dry_run": True}
                )
                continue
            outcome = await agent_workflow_service.analyze(
                db,
                ticket_id=ticket.id,
                organization_id=org_id,
                allow_auto_queue=allow_auto_queue,
                persist_run=True,
                authz=None,
                force=False,
            )
            results.append(
                {
                    "ticket_id": ticket.id,
                    "external_id": external_id,
                    "run_id": outcome.get("run_id"),
                    "decision": outcome.get("decision", {}).get("action"),
                    "coordinator_intent": outcome.get("coordinator_intent"),
                    "specialist_path": outcome.get("specialist_path"),
                    "tools": [
                        tool.get("tool") for tool in outcome.get("tool_plan", [])
                    ],
                }
            )
            print(
                "  decision:",
                results[-1]["decision"],
                "| specialist_path:",
                results[-1]["specialist_path"],
            )
        return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Replay the real agent workflow on local pilot tickets "
        "(opt-in, no external execution by default)."
    )
    parser.add_argument(
        "--ticket-ref",
        action="append",
        default=None,
        help="Pilot ticket ref (e.g. valuations-001). Repeatable. Defaults to "
        "the catalog's safe replay set.",
    )
    parser.add_argument(
        "--organization-id",
        type=int,
        default=None,
        help="Pilot organization id (defaults to the pilot tenant).",
    )
    parser.add_argument(
        "--allow-auto-queue",
        action="store_true",
        help="Allow automatic queueing (run approvals and authorization still "
        "apply).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve ticket ids only; never run the workflow.",
    )
    args = parser.parse_args()

    refs = [
        _normalize_ref(r) for r in (args.ticket_ref or OPTIONAL_REPLAY_REFS)
    ]
    assert all(r in TICKET_BY_REF for r in refs), (
        "unknown pilot ticket ref"
    )
    asyncio.run(
        main(
            ticket_refs=refs,
            allow_auto_queue=args.allow_auto_queue,
            organization_id=args.organization_id,
            dry_run=args.dry_run,
        )
    )