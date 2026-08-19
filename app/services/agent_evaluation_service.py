import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent_workflow_service import (
    agent_workflow_service,
)


class AgentEvaluationService:

    @staticmethod
    async def evaluate_case(
        db: AsyncSession,
        *,
        ticket_id: int,
        expected_action: str,
        expected_retrieval: bool,
        expected_tool: str,
    ) -> dict:

        started = time.perf_counter()

        result = await (
            agent_workflow_service.analyze(
                db=db,
                ticket_id=ticket_id,
                allow_auto_queue=False,
            )
        )

        latency_ms = (
            time.perf_counter()
            - started
        ) * 1000

        decision = result["decision"]

        workflow_path = result.get(
            "workflow_path",
            [],
        )

        tool_plan = result.get(
            "tool_plan",
            [],
        )

        actual_action = decision.get(
            "action"
        )

        actual_retrieval = (
            "retrieve_knowledge"
            in workflow_path
        )

        actual_tools = [
            tool.get("tool")
            for tool in tool_plan
        ]

        action_pass = (
            actual_action
            == expected_action
        )

        retrieval_pass = (
            actual_retrieval
            == expected_retrieval
        )

        tool_pass = (
            expected_tool
            in actual_tools
        )

        overall_pass = all(
            [
                action_pass,
                retrieval_pass,
                tool_pass,
            ]
        )

        return {
            "ticket_id": ticket_id,
            "expected_action": (
                expected_action
            ),
            "actual_action": (
                actual_action
            ),
            "expected_retrieval": (
                expected_retrieval
            ),
            "actual_retrieval": (
                actual_retrieval
            ),
            "expected_tool": (
                expected_tool
            ),
            "actual_tools": (
                actual_tools
            ),
            "action_pass": action_pass,
            "retrieval_pass": (
                retrieval_pass
            ),
            "tool_pass": tool_pass,
            "overall_pass": (
                overall_pass
            ),
            "latency_ms": round(
                latency_ms,
                2,
            ),
            "workflow_path": (
                workflow_path
            ),
            "tool_plan": tool_plan,
        }