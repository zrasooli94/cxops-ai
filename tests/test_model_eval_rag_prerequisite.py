import hashlib
import uuid

import pytest

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import Organization
from app.services.ai_evaluation_service import AIEvaluationService
from app.services.model_eval_rag_prerequisite import (
    OFFLINE_RAG_CONTRACT_MODEL,
    run_offline_rag_contract,
    validate_persisted_offline_rag_contract,
)


async def _chunk(db, organization_id: int, title: str, content: str):
    document = KnowledgeDocument(
        organization_id=organization_id,
        title=title,
        source="manual",
        checksum=hashlib.sha256(f"{organization_id}:{title}".encode()).hexdigest(),
        metadata_json={"synthetic": True},
    )
    db.add(document)
    await db.flush()
    chunk = KnowledgeChunk(
        organization_id=organization_id,
        document_id=document.id,
        chunk_index=0,
        content=content,
        token_count=len(content.split()),
        metadata_json={"synthetic": True},
        embedding=[0.0] * 1536,
    )
    db.add(chunk)
    await db.commit()
    return chunk


@pytest.mark.asyncio
async def test_offline_prerequisite_persists_real_tenant_scoped_contract(db):
    own = Organization(name=f"contract-own-{uuid.uuid4().hex[:8]}")
    other = Organization(name=f"contract-other-{uuid.uuid4().hex[:8]}")
    db.add_all([own, other])
    await db.commit()
    own_chunk = await _chunk(
        db, own.id, "Synthetic pickup", "Bluebird bicycle pickup closes at 7 PM."
    )
    await _chunk(db, other.id, "Synthetic pickup", "Bluebird pickup closes at midnight.")
    cases = [{
        "id": "pickup-en",
        "question": "When does Bluebird bicycle pickup close?",
        "expected_source_id": f"offline:{own_chunk.id}",
    }]
    run = await run_offline_rag_contract(
        db,
        organization_id=own.id,
        cases=cases,
        requested_by_subject="owner-subject",
        run_id=f"offline-{uuid.uuid4().hex}",
    )
    assert run.status == "succeeded"
    assert run.model == OFFLINE_RAG_CONTRACT_MODEL
    assert run.metrics["estimated_cost_usd"] == 0
    assert run.pass_rate == 1
    assert len(run.corpus_revision["offline_contract_fixture_sha256"]) == 64


@pytest.mark.asyncio
async def test_offline_baseline_candidate_can_be_reviewed_without_provider(db):
    org = Organization(name=f"contract-review-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.commit()
    chunk = await _chunk(
        db, org.id, "Synthetic returns", "Aurora bicycle returns are accepted for 14 days."
    )
    cases = [{
        "id": "returns-en",
        "question": "How long are Aurora bicycle returns accepted?",
        "expected_source_id": f"offline:{chunk.id}",
    }]
    baseline_run = await run_offline_rag_contract(
        db,
        organization_id=org.id,
        cases=cases,
        requested_by_subject="owner-subject",
        run_id=f"baseline-{uuid.uuid4().hex}",
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db,
        organization_id=org.id,
        run_id=baseline_run.run_id,
        created_by_subject="owner-subject",
    )
    candidate = await run_offline_rag_contract(
        db,
        organization_id=org.id,
        cases=cases,
        requested_by_subject="owner-subject",
        run_id=f"candidate-{uuid.uuid4().hex}",
    )
    gate = await AIEvaluationService.evaluate_release_gate(
        db,
        organization_id=org.id,
        run_id=candidate.run_id,
        baseline_id=baseline.id,
    )
    assert gate.blocked is False
    assert candidate.corpus_revision == baseline.corpus_revision


@pytest.mark.asyncio
async def test_knowledge_mutation_changes_contract_corpus_identity(db):
    org = Organization(name=f"contract-mutation-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.commit()
    chunk = await _chunk(
        db, org.id, "Synthetic pricing", "Comet tune-up costs 40 synthetic dollars."
    )
    cases = [{
        "id": "pricing-en",
        "question": "What does the Comet tune-up cost?",
        "expected_source_id": f"offline:{chunk.id}",
    }]
    first = await run_offline_rag_contract(
        db,
        organization_id=org.id,
        cases=cases,
        requested_by_subject="operator-supplied-subject",
        run_id=f"before-{uuid.uuid4().hex}",
    )
    chunk.content = "Comet tune-up costs 45 synthetic dollars."
    await db.commit()
    assert not await validate_persisted_offline_rag_contract(
        db, organization_id=org.id, run=first
    )
    second = await run_offline_rag_contract(
        db,
        organization_id=org.id,
        cases=cases,
        requested_by_subject="operator-supplied-subject",
        run_id=f"after-{uuid.uuid4().hex}",
    )
    assert (
        first.corpus_revision["offline_contract_corpus_sha256"]
        != second.corpus_revision["offline_contract_corpus_sha256"]
    )


@pytest.mark.asyncio
async def test_contract_rejects_duplicate_case_ids(db):
    org = Organization(name=f"contract-duplicate-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.commit()
    chunk = await _chunk(db, org.id, "Synthetic", "Orion service closes at 6 PM.")
    case = {
        "id": "duplicate",
        "question": "When does Orion service close?",
        "expected_source_id": f"offline:{chunk.id}",
    }
    with pytest.raises(ValueError, match="requires cases"):
        await run_offline_rag_contract(
            db,
            organization_id=org.id,
            cases=[case, case],
            requested_by_subject="operator-supplied-subject",
            run_id=f"duplicate-{uuid.uuid4().hex}",
        )


@pytest.mark.asyncio
async def test_repeated_expected_source_is_canonical_and_competing_chunk_invalidates(db):
    org = Organization(name=f"contract-corpus-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.commit()
    chunk = await _chunk(
        db, org.id, "Synthetic hours", "Vega workshop closes Friday at 6 PM."
    )
    cases = [
        {
            "id": "hours-one",
            "question": "When does Vega workshop close Friday?",
            "expected_source_id": f"offline:{chunk.id}",
        },
        {
            "id": "hours-two",
            "question": "What are Vega Friday workshop closing hours?",
            "expected_source_id": f"offline:{chunk.id}",
        },
    ]
    run = await run_offline_rag_contract(
        db,
        organization_id=org.id,
        cases=cases,
        requested_by_subject="operator-supplied-subject",
        run_id=f"repeated-{uuid.uuid4().hex}",
    )
    assert await validate_persisted_offline_rag_contract(
        db, organization_id=org.id, run=run
    )
    await _chunk(
        db,
        org.id,
        "Synthetic hours conflicting",
        "Vega workshop closes Friday at 8 PM.",
    )
    assert not await validate_persisted_offline_rag_contract(
        db, organization_id=org.id, run=run
    )
