import asyncio
import json
from pathlib import Path

from app.core.database import (
    AsyncSessionLocal,
)
from app.services.agent_evaluation_service import (
    AgentEvaluationService,
)


CASES = [
    {
        "name": "resolved customer issue",
        "ticket_id": 13,
        "expected_action": "no_action",
        "expected_retrieval": False,
        "expected_tool": "none",
        "expected_auto_execute": False,
    },
    {
        "name": "withdrawal overdue",
        "ticket_id": 15,
        "expected_action": "escalate",
        "expected_retrieval": True,
        "expected_tool": (
            "zendesk.update_ticket"
        ),
        "expected_auto_execute": False,
    },
]


async def main() -> None:

    results = []

    async with AsyncSessionLocal() as db:

        for case in CASES:

            print(
                f"\nEvaluating: "
                f"{case['name']}"
            )

            result = await (
                AgentEvaluationService
                .evaluate_case(
                    db=db,
                    ticket_id=(
                        case["ticket_id"]
                    ),
                    expected_action=(
                        case[
                            "expected_action"
                        ]
                    ),
                    expected_retrieval=(
                        case[
                            "expected_retrieval"
                        ]
                    ),
                    expected_tool=(
                        case[
                            "expected_tool"
                        ]
                    ),
                    expected_auto_execute=(
                        case[
                            "expected_auto_execute"
                        ]
                    ),
                )
            )

            result["name"] = (
                case["name"]
            )

            results.append(
                result
            )

            status = (
                "PASS"
                if result["overall_pass"]
                else "FAIL"
            )

            print(
                f"{status} | "
                f"action="
                f"{result['actual_action']} | "
                f"retrieval="
                f"{result['actual_retrieval']} | "
                f"auto_execute="
                f"{result['actual_auto_execute']} | "
                f"latency="
                f"{result['latency_ms']}ms"
            )

    total = len(
        results
    )

    passed = sum(
        1
        for result in results
        if result["overall_pass"]
    )

    failed = (
        total - passed
    )

    action_passed = sum(
        1
        for result in results
        if result["action_pass"]
    )

    retrieval_passed = sum(
        1
        for result in results
        if result["retrieval_pass"]
    )

    tool_passed = sum(
        1
        for result in results
        if result["tool_pass"]
    )

    auto_execute_passed = sum(
        1
        for result in results
        if result["auto_execute_pass"]
    )

    average_latency_ms = (
        sum(
            result["latency_ms"]
            for result in results
        )
        / total
        if total
        else 0
    )

    report = {
        "total": total,
        "passed": passed,
        "failed": failed,

        "pass_rate": (
            passed / total
            if total
            else 0
        ),

        "action_accuracy": (
            action_passed / total
            if total
            else 0
        ),

        "retrieval_accuracy": (
            retrieval_passed / total
            if total
            else 0
        ),

        "tool_accuracy": (
            tool_passed / total
            if total
            else 0
        ),

        "auto_execute_safety_accuracy": (
            auto_execute_passed / total
            if total
            else 0
        ),

        "average_latency_ms": round(
            average_latency_ms,
            2,
        ),

        "results": results,
    }

    output = Path(
        "evals/latest_agent_report.json"
    )

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

    print(
        "\n-----------------------------"
    )

    print(
        f"Agent evaluation: "
        f"{passed}/{total} passed"
    )

    print(
        f"Pass rate: "
        f"{report['pass_rate'] * 100:.1f}%"
    )

    print(
        f"Action accuracy: "
        f"{report['action_accuracy'] * 100:.1f}%"
    )

    print(
        f"RAG routing accuracy: "
        f"{report['retrieval_accuracy'] * 100:.1f}%"
    )

    print(
        f"Tool selection accuracy: "
        f"{report['tool_accuracy'] * 100:.1f}%"
    )

    print(
        f"Auto-execution safety: "
        f"{report['auto_execute_safety_accuracy'] * 100:.1f}%"
    )

    print(
        f"Average latency: "
        f"{report['average_latency_ms']}ms"
    )

    print(
        f"Report: {output}"
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )