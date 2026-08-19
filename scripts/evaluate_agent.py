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
    },
    {
        "name": "withdrawal overdue",
        "ticket_id": 15,
        "expected_action": "escalate",
        "expected_retrieval": True,
        "expected_tool": (
            "zendesk.update_ticket"
        ),
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
                f"latency="
                f"{result['latency_ms']}ms"
            )

    passed = sum(
        1
        for result in results
        if result["overall_pass"]
    )

    total = len(
        results
    )

    report = {
        "total": total,
        "passed": passed,
        "failed": (
            total - passed
        ),
        "pass_rate": (
            passed / total
            if total
            else 0
        ),
        "results": results,
    }

    output = Path(
        "evals/latest_agent_report.json"
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
        f"Report: {output}"
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )