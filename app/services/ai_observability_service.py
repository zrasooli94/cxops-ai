from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_request_log import AIRequestLog


class AIObservabilityService:

    @staticmethod
    def extract_usage(
        response,
    ) -> tuple[int, int, int]:

        usage = getattr(
            response,
            "usage_metadata",
            None,
        ) or {}

        input_tokens = int(
            usage.get(
                "input_tokens",
                0,
            )
        )

        output_tokens = int(
            usage.get(
                "output_tokens",
                0,
            )
        )

        total_tokens = int(
            usage.get(
                "total_tokens",
                input_tokens + output_tokens,
            )
        )

        return (
            input_tokens,
            output_tokens,
            total_tokens,
        )

    @staticmethod
    def estimate_cost(
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> float:

        input_cost = (
            input_tokens
            / 1_000_000
            * settings.llm_input_cost_per_million
        )

        output_cost = (
            output_tokens
            / 1_000_000
            * settings.llm_output_cost_per_million
        )

        return input_cost + output_cost

    @staticmethod
    async def record(
        db: AsyncSession,
        *,
        request_id: str,
        question: str,
        answer: str | None,
        grounded: bool,
        llm_called: bool,
        retrieval_count: int,
        best_similarity: float | None,
        sources: list[dict],
        latency_ms: float,
        status: str = "success",
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        error_message: str | None = None,
    ) -> AIRequestLog:

        estimated_cost = (
            AIObservabilityService.estimate_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

        log = AIRequestLog(
            request_id=request_id,
            feature="rag_answer",
            model=settings.chat_model,
            status=status,
            question=question,
            answer=answer,
            grounded=grounded,
            llm_called=llm_called,
            retrieval_count=retrieval_count,
            best_similarity=best_similarity,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost,
            latency_ms=latency_ms,
            sources=sources,
            error_message=error_message,
        )

        db.add(log)

        await db.commit()
        await db.refresh(log)

        return log

    @staticmethod
    async def summary(
        db: AsyncSession,
    ) -> dict:

        result = await db.execute(
            select(
                func.count(
                    AIRequestLog.id
                ).label("total_requests"),

                func.sum(
                    case(
                        (
                            AIRequestLog.status
                            == "success",
                            1,
                        ),
                        else_=0,
                    )
                ).label("successful_requests"),

                func.sum(
                    case(
                        (
                            AIRequestLog.grounded
                            .is_(True),
                            1,
                        ),
                        else_=0,
                    )
                ).label("grounded_requests"),

                func.avg(
                    AIRequestLog.latency_ms
                ).label("avg_latency_ms"),

                func.sum(
                    AIRequestLog.total_tokens
                ).label("total_tokens"),

                func.sum(
                    AIRequestLog.estimated_cost_usd
                ).label("estimated_cost_usd"),
            )
        )

        row = result.one()

        total = int(
            row.total_requests or 0
        )

        successful = int(
            row.successful_requests or 0
        )

        grounded = int(
            row.grounded_requests or 0
        )

        return {
            "total_requests": total,
            "success_rate": (
                successful / total
                if total
                else 0.0
            ),
            "grounded_rate": (
                grounded / total
                if total
                else 0.0
            ),
            "avg_latency_ms": float(
                row.avg_latency_ms or 0
            ),
            "total_tokens": int(
                row.total_tokens or 0
            ),
            "estimated_cost_usd": float(
                row.estimated_cost_usd or 0
            ),
        }