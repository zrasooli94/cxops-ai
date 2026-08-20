import asyncio
import json
import time
from pathlib import Path

from app.core.database import AsyncSessionLocal
from app.services.agent_workflow_service import (
    agent_workflow_service,
)
from scripts.evaluate_agent import CASES


async def measure(
    awaitable,
) -> tuple[object, float]:

    started = time.perf_counter()

    result = await awaitable

    elapsed_ms = (time.perf_counter() - started) * 1000

    return result, round(
        elapsed_ms,
        2,
    )


async def profile_case(
    db,
    case: dict,
) -> dict:

    state = {
        "ticket_id": case["ticket_id"],
        "workflow_path": [],
        "sources": [],
        "tool_plan": [],
    }

    # -----------------------------------------
    # Load ticket
    # -----------------------------------------

    load_result, load_ms = await measure(
        agent_workflow_service._load_ticket(
            state,
            db=db,
        )
    )

    state.update(load_result)

    # -----------------------------------------
    # Knowledge-routing decision
    # -----------------------------------------

    assessment_result, assessment_ms = await measure(
        agent_workflow_service._assess_knowledge_need(state)
    )

    state.update(assessment_result)

    needs_knowledge = state.get(
        "needs_knowledge",
        True,
    )

    # -----------------------------------------
    # Knowledge retrieval
    # -----------------------------------------

    retrieval_ms = 0.0

    if needs_knowledge:
        retrieval_result, retrieval_ms = await measure(
            agent_workflow_service._retrieve_knowledge(
                state,
                db=db,
            )
        )

        state.update(retrieval_result)

    # -----------------------------------------
    # Final agent decision
    # -----------------------------------------

    decision_result, decision_ms = await measure(
        agent_workflow_service._decide_action(state)
    )

    state.update(decision_result)

    # -----------------------------------------
    # Build tool plan
    # -----------------------------------------

    tool_result, tool_plan_ms = await measure(
        agent_workflow_service._build_tool_plan(state)
    )

    state.update(tool_result)

    total_ms = load_ms + assessment_ms + retrieval_ms + decision_ms + tool_plan_ms

    return {
        "name": case["name"],
        "ticket_id": case["ticket_id"],
        "needs_knowledge": (needs_knowledge),
        "action": (
            state.get(
                "decision",
                {},
            ).get("action")
        ),
        "timings_ms": {
            "load_ticket": (load_ms),
            "assess_knowledge_need": (assessment_ms),
            "retrieve_knowledge": (retrieval_ms),
            "decide_action": (decision_ms),
            "build_tool_plan": (tool_plan_ms),
            "total": round(
                total_ms,
                2,
            ),
        },
    }


async def main() -> None:

    results = []

    print("\n================================")
    print("CXOps AGENT LATENCY PROFILER")
    print("================================")

    async with AsyncSessionLocal() as db:
        for index, case in enumerate(
            CASES,
            start=1,
        ):
            print(f"\n[{index}/{len(CASES)}] {case['name']}")

            result = await profile_case(
                db,
                case,
            )

            results.append(result)

            timings = result["timings_ms"]

            print(f"Load: {timings['load_ticket']}ms")

            print(f"Knowledge assessment: {timings['assess_knowledge_need']}ms")

            print(f"Retrieval: {timings['retrieve_knowledge']}ms")

            print(f"Decision: {timings['decide_action']}ms")

            print(f"Tool plan: {timings['build_tool_plan']}ms")

            print(f"Total: {timings['total']}ms")

    total_cases = len(results)

    def average(
        key: str,
    ) -> float:

        if not results:
            return 0.0

        return round(
            sum(result["timings_ms"][key] for result in results) / total_cases,
            2,
        )

    average_load = average("load_ticket")

    average_assessment = average("assess_knowledge_need")

    average_retrieval = average("retrieve_knowledge")

    average_decision = average("decide_action")

    average_tool_plan = average("build_tool_plan")

    average_total = average("total")

    slowest_case = max(
        results,
        key=lambda result: result["timings_ms"]["total"],
    )

    fastest_case = min(
        results,
        key=lambda result: result["timings_ms"]["total"],
    )

    stages = {
        "load_ticket": (average_load),
        "assess_knowledge_need": (average_assessment),
        "retrieve_knowledge": (average_retrieval),
        "decide_action": (average_decision),
        "build_tool_plan": (average_tool_plan),
    }

    slowest_stage = max(
        stages,
        key=stages.get,
    )

    report = {
        "cases": total_cases,
        "average_timings_ms": {
            "load_ticket": (average_load),
            "assess_knowledge_need": (average_assessment),
            "retrieve_knowledge": (average_retrieval),
            "decide_action": (average_decision),
            "build_tool_plan": (average_tool_plan),
            "total": (average_total),
        },
        "slowest_stage": {
            "name": (slowest_stage),
            "average_ms": (stages[slowest_stage]),
        },
        "fastest_case": (fastest_case),
        "slowest_case": (slowest_case),
        "results": (results),
    }

    output = Path("evals/latest_agent_latency_profile.json")

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            report,
            indent=2,
        )
    )

    print("\n================================")
    print("LATENCY PROFILE RESULTS")
    print("================================")

    print(f"Average total: {average_total}ms")

    print(f"Load ticket: {average_load}ms")

    print(f"Knowledge assessment: {average_assessment}ms")

    print(f"Knowledge retrieval: {average_retrieval}ms")

    print(f"Decision generation: {average_decision}ms")

    print(f"Tool planning: {average_tool_plan}ms")

    print(f"Slowest stage: {slowest_stage} ({stages[slowest_stage]}ms)")

    print(
        "Fastest case: "
        f"{fastest_case['name']} "
        f"("
        f"{fastest_case['timings_ms']['total']}"
        f"ms)"
    )

    print(
        "Slowest case: "
        f"{slowest_case['name']} "
        f"("
        f"{slowest_case['timings_ms']['total']}"
        f"ms)"
    )

    print(f"\nReport: {output}")


if __name__ == "__main__":
    asyncio.run(main())
