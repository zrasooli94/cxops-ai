import asyncio
import json
from collections import Counter
from pathlib import Path

from app.core.database import AsyncSessionLocal
from app.services.agent_evaluation_service import (
    AgentEvaluationService,
)
from scripts.evaluate_agent import CASES


ROUNDS = 5


def percentage(value: int, total: int) -> float:
    if total == 0:
        return 0.0

    return (value / total) * 100


async def main() -> None:
    all_results: list[dict] = []

    print("\n================================")
    print("CXOps AGENT REPEATABILITY TEST")
    print("================================")
    print(f"Cases: {len(CASES)}")
    print(f"Rounds: {ROUNDS}")
    print(f"Total evaluations: {len(CASES) * ROUNDS}")

    async with AsyncSessionLocal() as db:

        for round_number in range(
            1,
            ROUNDS + 1,
        ):

            print("\n================================")
            print(
                f"ROUND {round_number}/{ROUNDS}"
            )
            print("================================")

            for index, case in enumerate(
                CASES,
                start=1,
            ):

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

                result["round"] = (
                    round_number
                )

                all_results.append(
                    result
                )

                status = (
                    "PASS"
                    if result[
                        "overall_pass"
                    ]
                    else "FAIL"
                )

                actual_action = (
                    result[
                        "actual_action"
                    ]
                )

                latency_ms = (
                    result[
                        "latency_ms"
                    ]
                )

                print(
                    f"[{index}/{len(CASES)}] "
                    f"{status} | "
                    f"{case['name']} | "
                    f"action={actual_action} | "
                    f"latency={latency_ms}ms"
                )

    total = len(
        all_results
    )

    passed = sum(
        1
        for result in all_results
        if result[
            "overall_pass"
        ]
    )

    action_passed = sum(
        1
        for result in all_results
        if result[
            "action_pass"
        ]
    )

    retrieval_passed = sum(
        1
        for result in all_results
        if result[
            "retrieval_pass"
        ]
    )

    tool_passed = sum(
        1
        for result in all_results
        if result[
            "tool_pass"
        ]
    )

    safety_passed = sum(
        1
        for result in all_results
        if result[
            "auto_execute_pass"
        ]
    )

    average_latency_ms = (
        sum(
            result[
                "latency_ms"
            ]
            for result
            in all_results
        )
        / total
        if total
        else 0.0
    )

    # -----------------------------------------
    # Per-case consistency
    # -----------------------------------------

    case_consistency: list[dict] = []

    for case in CASES:

        name = case[
            "name"
        ]

        case_results = [
            result
            for result in all_results
            if result["name"]
            == name
        ]

        actions = [
            result[
                "actual_action"
            ]
            for result
            in case_results
        ]

        retrieval_values = [
            result[
                "actual_retrieval"
            ]
            for result
            in case_results
        ]

        tool_values = [
            tuple(
                result[
                    "actual_tools"
                ]
            )
            for result
            in case_results
        ]

        auto_values = [
            result[
                "actual_auto_execute"
            ]
            for result
            in case_results
        ]

        action_counts = Counter(
            actions
        )

        most_common_action = (
            action_counts
            .most_common(1)[0][0]
            if action_counts
            else None
        )

        action_consistent = (
            len(
                set(
                    actions
                )
            )
            == 1
        )

        retrieval_consistent = (
            len(
                set(
                    retrieval_values
                )
            )
            == 1
        )

        tool_consistent = (
            len(
                set(
                    tool_values
                )
            )
            == 1
        )

        auto_execute_consistent = (
            len(
                set(
                    auto_values
                )
            )
            == 1
        )

        fully_consistent = all(
            [
                action_consistent,
                retrieval_consistent,
                tool_consistent,
                auto_execute_consistent,
            ]
        )

        case_passes = sum(
            1
            for result
            in case_results
            if result[
                "overall_pass"
            ]
        )

        case_run_count = len(
            case_results
        )

        case_consistency.append(
            {
                "name": name,
                "ticket_id": (
                    case[
                        "ticket_id"
                    ]
                ),
                "runs": (
                    case_run_count
                ),
                "passes": (
                    case_passes
                ),
                "pass_rate": (
                    case_passes
                    / case_run_count
                    if case_run_count
                    else 0.0
                ),
                "actions": (
                    actions
                ),
                "most_common_action": (
                    most_common_action
                ),
                "action_consistent": (
                    action_consistent
                ),
                "retrieval_consistent": (
                    retrieval_consistent
                ),
                "tool_consistent": (
                    tool_consistent
                ),
                "auto_execute_consistent": (
                    auto_execute_consistent
                ),
                "fully_consistent": (
                    fully_consistent
                ),
            }
        )

    consistent_cases = sum(
        1
        for case
        in case_consistency
        if case[
            "fully_consistent"
        ]
    )

    inconsistent_cases = [
        case
        for case
        in case_consistency
        if not case[
            "fully_consistent"
        ]
    ]

    failed_results = [
        result
        for result
        in all_results
        if not result[
            "overall_pass"
        ]
    ]

    # -----------------------------------------
    # Metrics
    # -----------------------------------------

    overall_accuracy = (
        passed / total
        if total
        else 0.0
    )

    action_accuracy = (
        action_passed / total
        if total
        else 0.0
    )

    rag_routing_accuracy = (
        retrieval_passed / total
        if total
        else 0.0
    )

    tool_selection_accuracy = (
        tool_passed / total
        if total
        else 0.0
    )

    auto_execution_safety = (
        safety_passed / total
        if total
        else 0.0
    )

    consistency_rate = (
        consistent_cases
        / len(CASES)
        if CASES
        else 0.0
    )

    # -----------------------------------------
    # Report
    # -----------------------------------------

    report = {
        "rounds": (
            ROUNDS
        ),
        "cases": (
            len(CASES)
        ),
        "total_evaluations": (
            total
        ),
        "passed": (
            passed
        ),
        "failed": (
            total - passed
        ),
        "overall_accuracy": (
            overall_accuracy
        ),
        "action_accuracy": (
            action_accuracy
        ),
        "rag_routing_accuracy": (
            rag_routing_accuracy
        ),
        "tool_selection_accuracy": (
            tool_selection_accuracy
        ),
        "auto_execution_safety": (
            auto_execution_safety
        ),
        "average_latency_ms": round(
            average_latency_ms,
            2,
        ),
        "consistent_cases": (
            consistent_cases
        ),
        "consistency_rate": (
            consistency_rate
        ),
        "case_consistency": (
            case_consistency
        ),
        "failed_results": (
            failed_results
        ),
    }

    output = Path(
        "evals/"
        "latest_agent_repeatability_report.json"
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

    # -----------------------------------------
    # Safe percentage values
    # -----------------------------------------

    overall_percentage = percentage(
        passed,
        total,
    )

    action_percentage = percentage(
        action_passed,
        total,
    )

    retrieval_percentage = percentage(
        retrieval_passed,
        total,
    )

    tool_percentage = percentage(
        tool_passed,
        total,
    )

    safety_percentage = percentage(
        safety_passed,
        total,
    )

    consistency_percentage = percentage(
        consistent_cases,
        len(CASES),
    )

    # -----------------------------------------
    # Console summary
    # -----------------------------------------

    print("\n================================")
    print("REPEATABILITY RESULTS")
    print("================================")

    print(
        f"Total evaluations: "
        f"{total}"
    )

    print(
        f"Passed: "
        f"{passed}/{total}"
    )

    print(
        f"Overall accuracy: "
        f"{overall_percentage:.1f}%"
    )

    print(
        f"Action accuracy: "
        f"{action_percentage:.1f}%"
    )

    print(
        f"RAG routing accuracy: "
        f"{retrieval_percentage:.1f}%"
    )

    print(
        f"Tool selection accuracy: "
        f"{tool_percentage:.1f}%"
    )

    print(
        f"Auto-execution safety: "
        f"{safety_percentage:.1f}%"
    )

    print(
        f"Case consistency: "
        f"{consistent_cases}/"
        f"{len(CASES)} "
        f"({consistency_percentage:.1f}%)"
    )

    print(
        f"Average latency: "
        f"{average_latency_ms:.2f}ms"
    )

    # -----------------------------------------
    # Inconsistent cases
    # -----------------------------------------

    if inconsistent_cases:

        print(
            "\nINCONSISTENT CASES"
        )

        print(
            "--------------------------------"
        )

        for case in inconsistent_cases:

            name = (
                case["name"]
            )

            actions = (
                case["actions"]
            )

            action_consistent = (
                case[
                    "action_consistent"
                ]
            )

            retrieval_consistent = (
                case[
                    "retrieval_consistent"
                ]
            )

            tool_consistent = (
                case[
                    "tool_consistent"
                ]
            )

            safety_consistent = (
                case[
                    "auto_execute_consistent"
                ]
            )

            print(
                f"\n{name}"
            )

            print(
                f"Actions: "
                f"{actions}"
            )

            print(
                f"Action consistent: "
                f"{action_consistent}"
            )

            print(
                f"RAG consistent: "
                f"{retrieval_consistent}"
            )

            print(
                f"Tool consistent: "
                f"{tool_consistent}"
            )

            print(
                f"Safety consistent: "
                f"{safety_consistent}"
            )

    else:

        print(
            "\nAll cases were consistent "
            "across every round."
        )

    # -----------------------------------------
    # Failed evaluations
    # -----------------------------------------

    if failed_results:

        print(
            "\nFAILED EVALUATIONS"
        )

        print(
            "--------------------------------"
        )

        for result in failed_results:

            round_number = (
                result["round"]
            )

            name = (
                result["name"]
            )

            actual_action = (
                result[
                    "actual_action"
                ]
            )

            expected_action = (
                result[
                    "expected_action"
                ]
            )

            print(
                f"Round {round_number} | "
                f"{name} | "
                f"action={actual_action} | "
                f"expected={expected_action}"
            )

    print(
        f"\nReport: {output}"
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )