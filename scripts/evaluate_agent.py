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
    {
        "name": "verification documents update",
        "ticket_id": 29,
        "expected_action": "internal_note",
        "expected_retrieval": False,
        "expected_tool": (
            "zendesk.add_internal_note"
        ),
        "expected_auto_execute": True,
    },
    {
        "name": "account access problem",
        "ticket_id": 30,
        "expected_action": "respond",
        "expected_retrieval": True,
        "expected_tool": (
            "zendesk.send_reply"
        ),
        "expected_auto_execute": False,
    },
    {
        "name": "withdrawal processing question",
        "ticket_id": 31,
        "expected_action": "respond",
        "expected_retrieval": True,
        "expected_tool": (
            "zendesk.send_reply"
        ),
        "expected_auto_execute": False,
    },
    {
        "name": "identity verification requirements",
        "ticket_id": 32,
        "expected_action": "respond",
        "expected_retrieval": True,
        "expected_tool": (
            "zendesk.send_reply"
        ),
        "expected_auto_execute": False,
    },
    {
        "name": "deposit still missing",
        "ticket_id": 33,
        "expected_action": "respond",
        "expected_retrieval": True,
        "expected_tool": (
            "zendesk.send_reply"
        ),
        "expected_auto_execute": False,
    },
    {
        "name": "suspicious account activity",
        "ticket_id": 34,
        "expected_action": "escalate",
        "expected_retrieval": True,
        "expected_tool": (
            "zendesk.update_ticket"
        ),
        "expected_auto_execute": False,
    },
    {
        "name": "special loyalty compensation",
        "ticket_id": 35,
        "expected_action": "human_review",
        "expected_retrieval": True,
        "expected_tool": (
            "human.review"
        ),
        "expected_auto_execute": False,
    },
    {
        "name": "simple greeting",
        "ticket_id": 36,
        "expected_action": "no_action",
        "expected_retrieval": False,
        "expected_tool": "none",
        "expected_auto_execute": False,
    },
    {
        "name": "record communication preference",
        "ticket_id": 37,
        "expected_action": "internal_note",
        "expected_retrieval": False,
        "expected_tool": (
            "zendesk.add_internal_note"
        ),
        "expected_auto_execute": True,
    },
    {
        "name": "unknown priority withdrawal feature",
        "ticket_id": 38,
        "expected_action": "human_review",
        "expected_retrieval": True,
        "expected_tool": (
            "human.review"
        ),
        "expected_auto_execute": False,
    },
]


def percentage(
    value: int,
    total: int,
) -> float:

    if total == 0:
        return 0.0

    return (
        value / total
    ) * 100


async def main() -> None:

    results = []

    async with AsyncSessionLocal() as db:

        for index, case in enumerate(
            CASES,
            start=1,
        ):

            print(
                "\n"
                "================================"
            )

            print(
                f"[{index}/{len(CASES)}] "
                f"Evaluating: "
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
                f"{status}"
            )

            print(
                "Action: "
                f"{result['actual_action']} "
                f"(expected "
                f"{result['expected_action']})"
            )

            print(
                "Retrieval: "
                f"{result['actual_retrieval']} "
                f"(expected "
                f"{result['expected_retrieval']})"
            )

            print(
                "Tools: "
                f"{result['actual_tools']} "
                f"(expected "
                f"{result['expected_tool']})"
            )

            print(
                "Auto execute: "
                f"{result['actual_auto_execute']} "
                f"(expected "
                f"{result['expected_auto_execute']})"
            )

            print(
                "Latency: "
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

    fastest_case = (
        min(
            results,
            key=lambda result: (
                result["latency_ms"]
            ),
        )
        if results
        else None
    )

    slowest_case = (
        max(
            results,
            key=lambda result: (
                result["latency_ms"]
            ),
        )
        if results
        else None
    )

    failed_cases = [
        {
            "name": result["name"],
            "ticket_id": (
                result["ticket_id"]
            ),
            "expected_action": (
                result["expected_action"]
            ),
            "actual_action": (
                result["actual_action"]
            ),
            "expected_retrieval": (
                result[
                    "expected_retrieval"
                ]
            ),
            "actual_retrieval": (
                result[
                    "actual_retrieval"
                ]
            ),
            "expected_tool": (
                result["expected_tool"]
            ),
            "actual_tools": (
                result["actual_tools"]
            ),
            "expected_auto_execute": (
                result[
                    "expected_auto_execute"
                ]
            ),
            "actual_auto_execute": (
                result[
                    "actual_auto_execute"
                ]
            ),
        }
        for result in results
        if not result["overall_pass"]
    ]

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

        "fastest_case": (
            {
                "name": (
                    fastest_case["name"]
                ),
                "latency_ms": (
                    fastest_case[
                        "latency_ms"
                    ]
                ),
            }
            if fastest_case
            else None
        ),

        "slowest_case": (
            {
                "name": (
                    slowest_case["name"]
                ),
                "latency_ms": (
                    slowest_case[
                        "latency_ms"
                    ]
                ),
            }
            if slowest_case
            else None
        ),

        "failed_cases": (
            failed_cases
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
        "\n"
        "================================"
    )

    print(
        "AGENT BENCHMARK RESULTS"
    )

    print(
        "================================"
    )

    print(
        f"Overall: "
        f"{passed}/{total} passed"
    )

    print(
        "Overall pass rate: "
        f"{percentage(passed, total):.1f}%"
    )

    print(
        "Action accuracy: "
        f"{percentage(action_passed, total):.1f}%"
    )

    print(
        "RAG routing accuracy: "
        f"{percentage(retrieval_passed, total):.1f}%"
    )

    print(
        "Tool selection accuracy: "
        f"{percentage(tool_passed, total):.1f}%"
    )

    print(
        "Auto-execution safety: "
        f"{percentage(auto_execute_passed, total):.1f}%"
    )

    print(
        "Average latency: "
        f"{average_latency_ms:.2f}ms"
    )

    if fastest_case:

        print(
            "Fastest case: "
            f"{fastest_case['name']} "
            f"({fastest_case['latency_ms']}ms)"
        )

    if slowest_case:

        print(
            "Slowest case: "
            f"{slowest_case['name']} "
            f"({slowest_case['latency_ms']}ms)"
        )

    if failed_cases:

        print(
            "\n"
            "FAILED CASES"
        )

        print(
            "--------------------------------"
        )

        for failure in failed_cases:

            print(
                f"\n{failure['name']} "
                f"(ticket "
                f"{failure['ticket_id']})"
            )

            print(
                "Action: "
                f"{failure['actual_action']} "
                f"vs expected "
                f"{failure['expected_action']}"
            )

            print(
                "Retrieval: "
                f"{failure['actual_retrieval']} "
                f"vs expected "
                f"{failure['expected_retrieval']}"
            )

            print(
                "Tools: "
                f"{failure['actual_tools']} "
                f"vs expected "
                f"{failure['expected_tool']}"
            )

            print(
                "Auto execute: "
                f"{failure['actual_auto_execute']} "
                f"vs expected "
                f"{failure['expected_auto_execute']}"
            )

    else:

        print(
            "\nNo failed cases."
        )

    print(
        "\n"
        f"Report: {output}"
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )