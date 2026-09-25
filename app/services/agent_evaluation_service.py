import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent_workflow_service import (
    agent_workflow_service,
    derive_specialist_path,
)


class AgentEvaluationService:
    @staticmethod
    async def evaluate_case(
        db: AsyncSession,
        *,
        ticket_id: int,
        organization_id: int,
        expected_action: str,
        expected_retrieval: bool,
        expected_tool: str,
        expected_auto_execute: bool,
        expected_intent: str | None = None,
        expected_specialists: list[str] | None = None,
    ) -> dict:

        started = time.perf_counter()

        result = await agent_workflow_service.analyze(
            db=db,
            ticket_id=ticket_id,
            organization_id=organization_id,
            allow_auto_queue=False,
            persist_run=False,
        )

        latency_ms = (time.perf_counter() - started) * 1000

        decision = result.get(
            "decision",
            {},
        )

        workflow_path = result.get(
            "workflow_path",
            [],
        )

        tool_plan = result.get(
            "tool_plan",
            [],
        )

        # -----------------------------------------
        # Actual results
        # -----------------------------------------

        actual_action = decision.get("action")

        actual_retrieval = "retrieve_knowledge" in workflow_path

        # The real Coordinator result is the source of truth for intent; the
        # evaluator never re-classifies the ticket itself (Phase 1K.2).
        actual_intent = result.get(
            "coordinator_intent",
        )

        actual_tools = [tool.get("tool") for tool in tool_plan]

        # Only real executable tools count.
        # "none" and "human.review" must NOT
        # be treated as auto-executable actions.
        executable_tools = [
            tool
            for tool in tool_plan
            if tool.get("tool")
            not in {
                "none",
                "human.review",
            }
        ]

        actual_auto_execute = bool(executable_tools) and all(
            (
                tool.get(
                    "authorized",
                    False,
                )
                and not tool.get(
                    "requires_approval",
                    True,
                )
            )
            for tool in executable_tools
        )

        # -----------------------------------------
        # Evaluation checks
        # -----------------------------------------

        action_pass = actual_action == expected_action

        retrieval_pass = actual_retrieval == expected_retrieval

        tool_pass = expected_tool in actual_tools

        auto_execute_pass = actual_auto_execute == expected_auto_execute

        # Optional Coordinator-intent dimension: scored only when the case
        # supplied an expected intent; never fails a case that did not.
        intent_pass = (
            (actual_intent == expected_intent)
            if expected_intent is not None
            else None
        )

        # Optional specialist-path dimension (Phase 1K.3): the actual path is
        # derived from the REAL workflow_path the workflow executed — the
        # evaluator never re-classifies. Scored only when the case supplied an
        # expected path; never fails a case that did not.
        actual_specialists = derive_specialist_path(workflow_path)

        specialist_path_pass = (
            (actual_specialists == expected_specialists)
            if expected_specialists is not None
            else None
        )

        overall_pass = all(
            [
                action_pass,
                retrieval_pass,
                tool_pass,
                auto_execute_pass,
            ]
        )

        # -----------------------------------------
        # Result
        # -----------------------------------------

        return {
            "ticket_id": ticket_id,
            "expected_action": (expected_action),
            "actual_action": (actual_action),
            "expected_retrieval": (expected_retrieval),
            "actual_retrieval": (actual_retrieval),
            "expected_tool": (expected_tool),
            "actual_tools": (actual_tools),
            "expected_auto_execute": (expected_auto_execute),
            "actual_auto_execute": (actual_auto_execute),
            "expected_intent": (expected_intent),
            "actual_intent": (actual_intent),
            "intent_pass": (intent_pass),
            "expected_specialists": (expected_specialists),
            "actual_specialists": (actual_specialists),
            "specialist_path_pass": (specialist_path_pass),
            "action_pass": (action_pass),
            "retrieval_pass": (retrieval_pass),
            "tool_pass": (tool_pass),
            "auto_execute_pass": (auto_execute_pass),
            "overall_pass": (overall_pass),
            "latency_ms": round(
                latency_ms,
                2,
            ),
            "workflow_path": (workflow_path),
            "tool_plan": (tool_plan),
        }
