"""Run the zero-provider RAG prerequisite in a local development database.

This command never constructs an OpenAI client.  It records actual lexical
retrieval/citation contract results for synthetic tenant knowledge.  An
authenticated ``evaluation.manage`` reviewer must still record the immutable
activation decision through the existing API; this command cannot approve it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.ai_evaluation_service import AIEvaluationService
from app.services.model_eval_rag_prerequisite import run_offline_rag_contract


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--baseline-id", type=int)
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> dict:
    if settings.environment != "development":
        raise SystemExit("offline RAG prerequisite is development-only")
    cases = json.loads(arguments.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise SystemExit("cases must be a JSON list")
    async with AsyncSessionLocal() as db:
        run = await run_offline_rag_contract(
            db,
            organization_id=arguments.organization_id,
            cases=cases,
            requested_by_subject=arguments.subject,
            run_id=f"offline-rag-{uuid4().hex}",
        )
        result = {
            "mode": arguments.mode,
            "organization_id": arguments.organization_id,
            "run_id": run.run_id,
            "status": run.status,
            "pass_rate": run.pass_rate,
            "fixture_sha256": run.corpus_revision[
                "offline_contract_fixture_sha256"
            ],
            "provider_requests": 0,
            "provider_cost_usd": 0,
            "quality_claim": "tenant-scoped lexical retrieval contract only; no model quality measured",
        }
        if arguments.mode == "baseline":
            baseline = await AIEvaluationService.create_baseline_from_run(
                db,
                organization_id=arguments.organization_id,
                run_id=run.run_id,
                created_by_subject=arguments.subject,
            )
            result["baseline_id"] = baseline.id
        else:
            if arguments.baseline_id is None:
                raise SystemExit("candidate mode requires --baseline-id")
            gate = await AIEvaluationService.evaluate_release_gate(
                db,
                organization_id=arguments.organization_id,
                run_id=run.run_id,
                baseline_id=arguments.baseline_id,
            )
            result["baseline_id"] = arguments.baseline_id
            result["release_gate_blocked"] = gate.blocked
        return result


def main() -> None:
    print(json.dumps(asyncio.run(_run(_arguments())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
