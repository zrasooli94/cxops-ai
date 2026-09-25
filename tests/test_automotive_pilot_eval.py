"""Phase 1M — automotive pilot evaluation dataset tests.

The 18 agent cases and 10 RAG cases must validate against the existing
Phase 1J/1K evaluation schemas (extra fields forbidden, bounded specialist
labels, boolean retrievals), reference real seeded pilot tickets, and satisfy
the scenario-coverage requirements of the Phase 1M gate: all four Coordinator
intents, both specialist routes, and explicit coverage of the two risk-flagged
scenarios.
"""

import os
import warnings
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-a1eval-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-a1eval-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

warnings.filterwarnings("ignore")

from app.core.config import reset_settings_cache
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
)
from app.models.service_escalation import ServiceEscalation
from app.models.service_queue import ServiceQueue
from app.models.sla_policy import SLAPolicy
from app.models.ticket import Ticket
from app.schemas.evaluation import (
    EvalAgentCaseInput,
    EvalAgentCaseInputs,
    EvalRAGCaseInput,
    EvalRAGCaseInputs,
)
from scripts.automotive_pilot_data import (
    KNOWLEDGE_DOCUMENTS,
    PILOT_ORGANIZATION_NAME,
    SCENARIOS,
    load_agent_cases,
    load_rag_cases,
)
from scripts.seed_automotive_pilot import seed

TEST_REFERENCE = datetime(2026, 9, 1, tzinfo=UTC)

ACTION_TOOL = {
    "respond": {
        "zendesk.send_reply",
        "zendesk.update_ticket",
    },
    "route": {"zendesk.update_ticket"},
    "escalate": {"zendesk.update_ticket"},
    "internal_note": {"zendesk.add_internal_note"},
    "human_review": {"human.review"},
    "no_action": {"none"},
}


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "hs256")
    monkeypatch.setenv("AUTH_JWT_SECRET", "z" * 32)
    monkeypatch.setenv("AUTH_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("AUTH_JWT_ISSUER", "test-a1eval-issuer")
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "test-a1eval-audience")
    monkeypatch.setenv("AUTH_DEV_MODE", "False")
    monkeypatch.setenv("ENVIRONMENT", "development")
    reset_settings_cache()


@pytest_asyncio.fixture
async def ticket_ids_by_ref(db):
    await seed(
        db,
        reference=TEST_REFERENCE,
        with_knowledge=False,
        print_fn=lambda *_: None,
    )
    org_id = (
        await db.execute(
            select(Organization.id).where(
                Organization.name == PILOT_ORGANIZATION_NAME
            )
        )
    ).scalar_one()
    tickets = (
        await db.execute(select(Ticket).where(Ticket.organization_id == org_id))
    ).scalars().all()
    by_ref = {t.external_id.split("a1-", 1)[1]: t.id for t in tickets}
    try:
        yield by_ref
    finally:
        await _purge_pilot_org(db)


async def _purge_pilot_org(db) -> None:
    result = await db.execute(
        select(Organization.id).where(Organization.name == PILOT_ORGANIZATION_NAME)
    )
    leaked_ids = [row[0] for row in result.all()]
    if not leaked_ids:
        return
    for tbl, col in [
        (ConversationMessage, "organization_id"),
        (Conversation, "organization_id"),
        (ServiceEscalation, "organization_id"),
        (Ticket, "organization_id"),
        (Customer, "organization_id"),
        (ServiceQueue, "organization_id"),
        (SLAPolicy, "organization_id"),
    ]:
        await db.execute(delete(tbl).where(getattr(tbl, col).in_(leaked_ids)))
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.organization_id.in_(leaked_ids)
        )
    )
    await db.execute(delete(Organization).where(Organization.id.in_(leaked_ids)))
    await db.commit()


def _agent_inputs(cases: list[dict], ticket_ids: dict[str, int]) -> list[dict]:
    return [
        {
            "ticket_id": ticket_ids[case["ticket_ref"]],
            "expected_action": case["expected_action"],
            "expected_retrieval": case["expected_retrieval"],
            "expected_tool": case["expected_tool"],
            "expected_auto_execute": case["expected_auto_execute"],
            "expected_intent": case["expected_intent"],
            "expected_specialists": case["expected_specialists"],
        }
        for case in cases
    ]


def test_agent_dataset_imports_and_has_valid_shape():
    cases = load_agent_cases()
    assert 14 <= len(cases) <= 18
    refs = [c["ticket_ref"] for c in cases]
    assert len(set(refs)) == len(refs), "duplicate ticket_ref in agent cases"
    for case in cases:
        assert set(case.keys()) == {
            "name", "ticket_ref", "subject", "description",
            "expected_action", "expected_retrieval", "expected_tool",
            "expected_auto_execute", "expected_intent", "expected_specialists",
        }, case["name"]


def test_rag_dataset_imports_and_has_valid_shape():
    cases = load_rag_cases()
    assert 8 <= len(cases) <= 30
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids), "duplicate id in rag cases"
    for case in cases:
        assert set(case.keys()) == {
            "id", "question", "expected_sources",
            "expected_terms", "should_refuse",
        }, case["id"]


@pytest.mark.asyncio
async def test_agent_dataset_validates_as_phase1j(db, ticket_ids_by_ref):
    cases = _agent_inputs(load_agent_cases(), ticket_ids_by_ref)
    parsed = EvalAgentCaseInputs.model_validate({"cases": cases})
    assert len(parsed.cases) == len(cases)
    for entry in parsed.cases:
        assert entry.expected_intent is not None
        assert entry.expected_specialists is not None
        assert 1 <= len(entry.expected_specialists) <= 3


@pytest.mark.asyncio
async def test_agent_dataset_rejects_malformed_entries(db, ticket_ids_by_ref):
    cases = load_agent_cases()
    refs = [ticket_ids_by_ref[c["ticket_ref"]] for c in cases]

    base = {
        "expected_action": "respond",
        "expected_retrieval": True,
        "expected_tool": "zendesk.send_reply",
        "expected_auto_execute": False,
        "expected_intent": "information",
        "expected_specialists": ["coordinator", "knowledge"],
    }
    EvalAgentCaseInput(ticket_id=refs[0], **base)  # valid baseline

    bad_intent = dict(base, expected_intent="bogus")
    with pytest.raises(ValidationError):
        EvalAgentCaseInput(ticket_id=refs[0], **bad_intent)

    dup_specialists = dict(base, expected_specialists=["coordinator", "coordinator"])
    with pytest.raises(ValidationError):
        EvalAgentCaseInput(ticket_id=refs[0], **dup_specialists)

    unknown_specialist = dict(base, expected_specialists=["billing"])
    with pytest.raises(ValidationError):
        EvalAgentCaseInput(ticket_id=refs[0], **unknown_specialist)

    with pytest.raises(ValidationError):
        EvalAgentCaseInput(ticket_id=refs[0], **base, unknown_field="x")


@pytest.mark.asyncio
async def test_rag_dataset_validates_as_phase1j(db, ticket_ids_by_ref):
    cases = load_rag_cases()
    parsed = EvalRAGCaseInputs.model_validate(
        {"cases": [dict(c) for c in cases]}
    )
    assert len(parsed.cases) == len(cases)

    with pytest.raises(ValidationError):
        EvalRAGCaseInput(
            id="x" * 101,
            question="x",
            expected_sources=[],
            should_refuse=True,
        )
    with pytest.raises(ValidationError):
        EvalRAGCaseInput(
            id="bad-id",
            question="x",
            expected_sources=[],
            should_refuse=True,
            unknown_field="x",
        )


@pytest.mark.asyncio
async def test_coverage_all_intents_and_both_routes(db, ticket_ids_by_ref):
    cases = load_agent_cases()

    intents = {c["expected_intent"] for c in cases}
    assert intents == {"information", "action", "mixed", "none"}

    specialist_paths = {frozenset(c["expected_specialists"]) for c in cases}
    assert frozenset({"coordinator", "knowledge"}) in specialist_paths
    assert frozenset({"coordinator", "action"}) in specialist_paths
    assert frozenset({"coordinator", "knowledge", "action"}) in specialist_paths

    for case in cases:
        assert case["expected_tool"] in ACTION_TOOL[case["expected_action"]], (
            case["name"], case["expected_action"], case["expected_tool"]
        )
        if case["expected_tool"] == "none":
            assert case["expected_action"] == "no_action", case["name"]
        if set(case["expected_specialists"]) == {"knowledge"}:
            assert case["expected_retrieval"] is True, case["name"]


@pytest.mark.asyncio
async def test_every_scenario_has_an_agent_case(db, ticket_ids_by_ref):
    cases = load_agent_cases()
    covered = {c["ticket_ref"] for c in cases}
    scenario_tickets = {s["ticket"] for s in SCENARIOS}
    assert covered == scenario_tickets

    risk_refs = {
        s["ticket"] for s in SCENARIOS if s.get("risk") is True
    }
    assert len(risk_refs) >= 2
    for ref in risk_refs:
        case = next(c for c in cases if c["ticket_ref"] == ref)
        assert case["expected_specialists"]  # non-empty route for risk cases


@pytest.mark.asyncio
async def test_rag_expected_sources_are_pilot_docs(db, ticket_ids_by_ref):
    rag_cases = load_rag_cases()
    titles = {d["title"] for d in KNOWLEDGE_DOCUMENTS}
    for case in rag_cases:
        for source in case["expected_sources"]:
            assert source in titles, case["id"]
        for term in case["expected_terms"]:
            assert isinstance(term, str) and term, case["id"]


@pytest.mark.asyncio
async def test_only_documents_001_is_auto_execute(db, ticket_ids_by_ref):
    cases = load_agent_cases()
    only = {c["ticket_ref"] for c in cases if c["expected_auto_execute"]}
    assert only == {"documents-001"}
    case = next(c for c in cases if c["ticket_ref"] == "documents-001")
    assert case["expected_tool"] == "zendesk.add_internal_note"