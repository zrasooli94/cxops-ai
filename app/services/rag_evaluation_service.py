from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_request_log import AIRequestLog
from app.services.citation_service import CitationService
from app.services.rag_service import rag_service


class RAGEvaluationService:
    @staticmethod
    async def _get_request_log(
        db: AsyncSession,
        request_id: str,
    ) -> AIRequestLog | None:

        result = await db.execute(
            select(AIRequestLog).where(AIRequestLog.request_id == request_id)
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def evaluate_case(
        db: AsyncSession,
        case: dict,
    ) -> dict:

        response = await rag_service.answer(
            db=db,
            question=case["question"],
        )

        sources = response["sources"]

        source_titles = {source["title"] for source in sources}

        expected_sources = set(
            case.get(
                "expected_sources",
                [],
            )
        )

        should_refuse = bool(
            case.get(
                "should_refuse",
                False,
            )
        )

        answer = response["answer"]
        answer_lower = answer.lower()

        # -----------------------------
        # Retrieval accuracy
        # -----------------------------

        if should_refuse:
            retrieval_hit = len(sources) == 0

        else:
            retrieval_hit = bool(source_titles & expected_sources)

        # -----------------------------
        # Answer correctness
        # -----------------------------

        expected_terms = case.get(
            "expected_terms",
            [],
        )

        if should_refuse:
            answer_correct = response["grounded"] is False

        else:
            answer_correct = all(
                term.lower() in answer_lower for term in expected_terms
            )

        # -----------------------------
        # Grounding correctness
        # -----------------------------

        expected_grounded = not should_refuse

        grounding_correct = response["grounded"] == expected_grounded

        # -----------------------------
        # Refusal correctness
        # -----------------------------

        refusal_correct = None

        if should_refuse:
            refusal_correct = response["grounded"] is False and len(sources) == 0

        # -----------------------------
        # Citation validation
        # -----------------------------

        citation_valid = True

        if response["grounded"]:
            valid_source_ids = {source["source_id"] for source in sources}

            (
                citation_valid,
                _,
            ) = CitationService.validate(
                answer=answer,
                valid_source_ids=(valid_source_ids),
            )

        # -----------------------------
        # Observability data
        # -----------------------------

        log = await RAGEvaluationService._get_request_log(
            db,
            response["request_id"],
        )

        return {
            "id": case["id"],
            "question": case["question"],
            "passed": all(
                [
                    retrieval_hit,
                    answer_correct,
                    grounding_correct,
                    citation_valid,
                    (refusal_correct if refusal_correct is not None else True),
                ]
            ),
            "retrieval_hit": (retrieval_hit),
            "answer_correct": (answer_correct),
            "grounding_correct": (grounding_correct),
            "refusal_correct": (refusal_correct),
            "citation_valid": (citation_valid),
            "grounded": response["grounded"],
            "sources": list(source_titles),
            "answer": answer,
            "best_similarity": response["best_similarity"],
            "latency_ms": (log.latency_ms if log else 0.0),
            "total_tokens": (log.total_tokens if log else 0),
            "estimated_cost_usd": (log.estimated_cost_usd if log else 0.0),
        }

    @staticmethod
    async def evaluate(
        db: AsyncSession,
        cases: list[dict],
    ) -> dict:

        results: list[dict] = []

        for case in cases:
            result = await RAGEvaluationService.evaluate_case(
                db=db,
                case=case,
            )

            results.append(result)

        total = len(results)

        answerable_results = [
            result
            for result, case in zip(
                results,
                cases,
                strict=True,
            )
            if not case.get(
                "should_refuse",
                False,
            )
        ]

        refusal_results = [
            result
            for result, case in zip(
                results,
                cases,
                strict=True,
            )
            if case.get(
                "should_refuse",
                False,
            )
        ]

        def rate(
            values: list[bool],
        ) -> float:

            if not values:
                return 0.0

            return sum(values) / len(values)

        total_tokens = sum(result["total_tokens"] for result in results)

        total_cost = sum(result["estimated_cost_usd"] for result in results)

        avg_latency = (
            sum(result["latency_ms"] for result in results) / total if total else 0.0
        )

        return {
            "summary": {
                "total_cases": total,
                "passed_cases": sum(result["passed"] for result in results),
                "pass_rate": rate([result["passed"] for result in results]),
                "retrieval_accuracy": (
                    rate([result["retrieval_hit"] for result in answerable_results])
                ),
                "answer_correctness": (
                    rate([result["answer_correct"] for result in answerable_results])
                ),
                "grounding_accuracy": (
                    rate([result["grounding_correct"] for result in results])
                ),
                "citation_validity": (
                    rate([result["citation_valid"] for result in answerable_results])
                ),
                "refusal_accuracy": (
                    rate(
                        [bool(result["refusal_correct"]) for result in refusal_results]
                    )
                ),
                "avg_latency_ms": (avg_latency),
                "total_tokens": (total_tokens),
                "estimated_cost_usd": (total_cost),
            },
            "cases": results,
        }
