"""Persist a provider-free RAG contract prerequisite for model evaluation.

This is deliberately a retrieval/citation contract check, not evidence of model
quality.  It executes the same tenant-scoped lexical retrieval used to assemble
the future provider request and persists ordinary evaluation run/case rows so an
authenticated ``evaluation.manage`` reviewer can compare and decide it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.services.offline_grounded_chat import offline_grounded_chat

OFFLINE_RAG_CONTRACT_MODEL = "offline-grounded-contract-v1"
OFFLINE_RAG_CONTRACT_EMBEDDING = "none-lexical"
_MAX_CONTRACT_CASES = 20
_SHA256 = re.compile(r"[0-9a-f]{64}")


def contract_fixture_sha256(cases: list[dict]) -> str:
    canonical = [
        {
            "id": str(case["id"]),
            "question": str(case["question"]),
            "expected_source_id": str(case["expected_source_id"]),
            "previous_customer_question": (
                str(case["previous_customer_question"])
                if case.get("previous_customer_question") is not None
                else None
            ),
        }
        for case in cases
    ]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _current_corpus_sha256(
    db: AsyncSession, *, organization_id: int, source_ids: list[str]
) -> str | None:
    source_ids = sorted(set(source_ids))
    try:
        chunk_ids = [int(source_id.removeprefix("offline:")) for source_id in source_ids]
    except ValueError:
        return None
    expected_rows = (
        await db.execute(
            select(KnowledgeChunk.id)
            .join(
                KnowledgeDocument,
                (KnowledgeDocument.id == KnowledgeChunk.document_id)
                & (KnowledgeDocument.organization_id == KnowledgeChunk.organization_id),
            )
            .where(
                KnowledgeChunk.organization_id == organization_id,
                KnowledgeChunk.id.in_(chunk_ids),
                KnowledgeDocument.organization_id == organization_id,
            )
        )
    ).all()
    if len(expected_rows) != len(chunk_ids):
        return None
    rows = (
        await db.execute(
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.content,
                KnowledgeDocument.checksum,
                KnowledgeDocument.updated_at,
            )
            .join(
                KnowledgeDocument,
                (KnowledgeDocument.id == KnowledgeChunk.document_id)
                & (KnowledgeDocument.organization_id == KnowledgeChunk.organization_id),
            )
            .where(
                KnowledgeChunk.organization_id == organization_id,
                KnowledgeDocument.organization_id == organization_id,
            )
            .order_by(KnowledgeChunk.id)
            .limit(5000)
        )
    ).all()
    return hashlib.sha256(
        json.dumps(
            [
                {
                    "source_id": f"offline:{row.id}",
                    "content": row.content,
                    "document_checksum": row.checksum,
                    "document_updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def run_offline_rag_contract(
    db: AsyncSession,
    *,
    organization_id: int,
    cases: list[dict],
    requested_by_subject: str,
    run_id: str,
):
    """Execute and persist one real, zero-provider retrieval contract run."""
    if (
        not cases
        or len(cases) > _MAX_CONTRACT_CASES
        or len({str(case.get("id")) for case in cases}) != len(cases)
        or not requested_by_subject
    ):
        raise ValueError("offline RAG contract requires cases and an operator subject")
    fixture_sha = contract_fixture_sha256(cases)
    source_ids = sorted({str(case["expected_source_id"]) for case in cases})
    corpus_sha = await _current_corpus_sha256(
        db, organization_id=organization_id, source_ids=source_ids
    )
    if corpus_sha is None:
        raise ValueError("offline RAG contract sources are not owned by the tenant")
    await AIEvaluationRepository.create_run(
        db,
        run_id=run_id,
        organization_id=organization_id,
        target_type="rag",
        model=OFFLINE_RAG_CONTRACT_MODEL,
        embedding_model=OFFLINE_RAG_CONTRACT_EMBEDDING,
        corpus_revision={
            "offline_contract_fixture_sha256": fixture_sha,
            "offline_contract_corpus_sha256": corpus_sha,
            "offline_contract_case_count": len(cases),
        },
        trigger_source="manual",
        requested_by_subject=requested_by_subject,
        status="running",
        started_at=datetime.now(UTC),
        input_data={"synthetic_case_ids": [str(case["id"]) for case in cases]},
    )
    results: list[bool] = []
    for case in cases:
        answer = await offline_grounded_chat.answer(
            db,
            organization_id=organization_id,
            question=str(case["question"]),
            previous_customer_question=case.get("previous_customer_question"),
        )
        expected_source_id = str(case["expected_source_id"])
        source_current = bool(
            answer
            and await offline_grounded_chat.source_is_current(
                db,
                organization_id=organization_id,
                source_id=answer.source_id,
            )
        )
        passed = bool(
            answer and answer.source_id == expected_source_id and source_current
        )
        results.append(passed)
        await AIEvaluationRepository.add_case(
            db,
            run_id=run_id,
            organization_id=organization_id,
            case_id=str(case["id"]),
            case_type="rag",
            expected={"source_id": expected_source_id},
            actual={"source_id": answer.source_id if answer else None},
            dimensions={
                "passed": passed,
                "retrieval_hit": passed,
                "grounding_correct": passed,
                "citation_valid": passed,
            },
            total_tokens=0,
            estimated_cost_usd=0,
            fingerprint=fixture_sha,
        )
    rate = sum(results) / len(results)
    await AIEvaluationRepository.update_run(
        db,
        run_id=run_id,
        organization_id=organization_id,
        values={
            "status": "succeeded",
            "pass_rate": rate,
            "metrics": {
                "total_cases": len(results),
                "passed_cases": sum(results),
                "retrieval_accuracy": rate,
                "grounding_accuracy": rate,
                "citation_validity": rate,
                "refusal_accuracy": 1.0,
                "avg_latency_ms": 0.0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
            },
            "completed_at": datetime.now(UTC),
        },
    )
    return await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run_id, organization_id=organization_id
    )


def is_offline_rag_contract_run(run) -> bool:
    revision = run.corpus_revision if isinstance(run.corpus_revision, dict) else {}
    fixture_sha = revision.get("offline_contract_fixture_sha256")
    corpus_sha = revision.get("offline_contract_corpus_sha256")
    case_count = revision.get("offline_contract_case_count")
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    return (
        run.target_type == "rag"
        and run.model == OFFLINE_RAG_CONTRACT_MODEL
        and run.embedding_model == OFFLINE_RAG_CONTRACT_EMBEDDING
        and getattr(run, "trigger_source", "manual") == "manual"
        and isinstance(fixture_sha, str)
        and _SHA256.fullmatch(fixture_sha) is not None
        and isinstance(corpus_sha, str)
        and _SHA256.fullmatch(corpus_sha) is not None
        and isinstance(case_count, int)
        and not isinstance(case_count, bool)
        and 1 <= case_count <= _MAX_CONTRACT_CASES
        and run.pass_rate == 1.0
        and metrics.get("total_cases") == case_count
        and metrics.get("passed_cases") == case_count
        and metrics.get("retrieval_accuracy") == 1.0
        and metrics.get("grounding_accuracy") == 1.0
        and metrics.get("citation_validity") == 1.0
        and metrics.get("total_tokens") == 0
        and metrics.get("estimated_cost_usd") == 0.0
    )


async def validate_persisted_offline_rag_contract(
    db: AsyncSession, *, organization_id: int, run
) -> bool:
    if not is_offline_rag_contract_run(run):
        return False
    cases = await AIEvaluationRepository.list_cases_for_run_for_tenant(
        db, run_id=run.run_id, organization_id=organization_id
    )
    fixture_sha = run.corpus_revision["offline_contract_fixture_sha256"]
    corpus_sha = run.corpus_revision["offline_contract_corpus_sha256"]
    expected_count = run.corpus_revision["offline_contract_case_count"]
    source_ids = sorted(str(case.expected.get("source_id", "")) for case in cases)
    current_corpus_sha = await _current_corpus_sha256(
        db, organization_id=organization_id, source_ids=source_ids
    )
    return (
        len(cases) == expected_count
        and current_corpus_sha == corpus_sha
        and all(case.fingerprint == fixture_sha for case in cases)
        and all(case.actual.get("source_id") == case.expected.get("source_id") for case in cases)
        and all(
            case.dimensions.get("passed") is True
            and case.dimensions.get("retrieval_hit") is True
            and case.dimensions.get("grounding_correct") is True
            and case.dimensions.get("citation_valid") is True
            and case.total_tokens == 0
            and case.estimated_cost_usd == 0
            for case in cases
        )
    )
