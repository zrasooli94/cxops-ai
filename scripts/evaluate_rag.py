import asyncio
import json
from pathlib import Path

from app.core.database import (
    AsyncSessionLocal,
)
from app.services.rag_evaluation_service import (
    RAGEvaluationService,
)


EVAL_FILE = Path(
    "evals/rag_cases.json"
)


async def main():

    cases = json.loads(
        EVAL_FILE.read_text(
            encoding="utf-8"
        )
    )

    async with AsyncSessionLocal() as db:

        report = await (
            RAGEvaluationService.evaluate(
                db=db,
                cases=cases,
            )
        )

    summary = report["summary"]

    print()
    print("CXOps AI — RAG Evaluation")
    print("=" * 45)

    print(
        f"Cases: "
        f"{summary['passed_cases']}"
        f"/{summary['total_cases']}"
    )

    print(
        "Pass rate: "
        f"{summary['pass_rate']:.1%}"
    )

    print(
        "Retrieval accuracy: "
        f"{summary['retrieval_accuracy']:.1%}"
    )

    print(
        "Answer correctness: "
        f"{summary['answer_correctness']:.1%}"
    )

    print(
        "Grounding accuracy: "
        f"{summary['grounding_accuracy']:.1%}"
    )

    print(
        "Citation validity: "
        f"{summary['citation_validity']:.1%}"
    )

    print(
        "Refusal accuracy: "
        f"{summary['refusal_accuracy']:.1%}"
    )

    print(
        "Average latency: "
        f"{summary['avg_latency_ms']:.0f} ms"
    )

    print(
        "Total tokens: "
        f"{summary['total_tokens']}"
    )

    print(
        "Estimated cost: $"
        f"{summary['estimated_cost_usd']:.6f}"
    )

    print()
    print("Individual cases")
    print("=" * 45)

    for result in report["cases"]:

        status = (
            "PASS"
            if result["passed"]
            else "FAIL"
        )

        print(
            f"{status} — {result['id']}"
        )

        if not result["passed"]:

            print(
                "  Retrieval:",
                result["retrieval_hit"],
            )

            print(
                "  Answer:",
                result["answer_correct"],
            )

            print(
                "  Grounding:",
                result[
                    "grounding_correct"
                ],
            )

            print(
                "  Citation:",
                result[
                    "citation_valid"
                ],
            )

            print(
                "  Sources:",
                result["sources"],
            )

    output_path = Path(
        "evals/latest_report.json"
    )

    output_path.write_text(
        json.dumps(
            report,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "Report saved to:",
        output_path,
    )


if __name__ == "__main__":
    asyncio.run(main())