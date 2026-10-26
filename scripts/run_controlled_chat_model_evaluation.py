"""Run the frozen bilingual evaluation through the local public-chat service.

The default command stops after creating ten fresh synthetic sessions and
printing the exact activation manifest.  A live continuation is possible only
with ``--execute-live`` after an authenticated manager records that unchanged
manifest.  The restricted key is then read once from a masked TTY prompt and is
never accepted via argv, environment, or a file.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.ai_evaluation_case import AIEvaluationCase
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.ai_request_log import AIRequestLog
from app.models.ai_usage_reservation import AIUsageReservation
from app.services.model_eval_activation_plan import OperatorActivationPlan
from app.services.model_eval_billing_evidence import require_reviewed_billing_evidence
from app.services.model_eval_controller import (
    EvaluationBlockedError,
    EvaluationPolicy,
    InputTokenCountPolicy,
)
from app.services.model_eval_development_lifecycle import (
    installed_development_evaluation_provider,
)
from app.services.model_eval_operator_secret import operator_entered_api_key
from app.services.public_chat_model_eval import (
    PersistedEvaluationActivationGuard,
    validate_activation_manifest,
)
from app.services.public_chat_service import PublicChatService
from app.services.tenant_ai_allowance_service import STATUS_RESERVED
from scripts.plan_chat_model_evaluation import expanded_turns, validate_mechanical_spec


class RunnerBlocked(RuntimeError):
    pass


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_SPEC_PATH = REPOSITORY_ROOT / "scripts/plan_chat_model_evaluation.py"
RUNNER_SOURCE_PATH = Path(__file__).resolve()
SOURCE_BOUND_PATHS = (
    "scripts/run_controlled_chat_model_evaluation.py",
    "scripts/plan_chat_model_evaluation.py",
    "app/services/ai_evaluation_service.py",
    "app/services/model_eval_activation_plan.py",
    "app/services/model_eval_billing_evidence.py",
    "app/services/model_eval_controller.py",
    "app/services/model_eval_development_lifecycle.py",
    "app/services/model_eval_openai_adapter.py",
    "app/services/model_eval_openai_http_transport.py",
    "app/services/model_eval_operator_secret.py",
    "app/services/public_chat_model_eval.py",
    "app/services/public_chat_service.py",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_identity() -> dict[str, str]:
    fixture = json.dumps(
        expanded_turns(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    source_diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *SOURCE_BOUND_PATHS],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if source_diff.returncode != 0:
        raise RunnerBlocked(
            "evaluation source differs from the reviewed Git commit"
        )
    return {
        "frozen_fixture_sha256": hashlib.sha256(fixture.encode()).hexdigest(),
        "evaluation_spec_sha256": _sha256_file(EVALUATION_SPEC_PATH),
        "runner_source_sha256": _sha256_file(RUNNER_SOURCE_PATH),
        "reviewed_source_commit": commit,
    }


@dataclass(frozen=True)
class SessionBinding:
    fixture_conversation_id: str
    session_id: int
    token: str


class NoRetryHttpClient:
    """One-shot HTTP client contract used by the reviewed development source."""

    max_retries = 0

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(follow_redirects=False, timeout=30.0)

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        return await self._client.post(
            url, json=json, headers=headers, timeout=timeout, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--widget-key", required=True)
    parser.add_argument("--embedding-origin", default="http://127.0.0.1:3000")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--count-evidence-id", required=True)
    parser.add_argument("--count-evidence-expires-at", required=True)
    parser.add_argument("--all-in-evidence-id", required=True)
    parser.add_argument("--all-in-evidence-expires-at", required=True)
    parser.add_argument("--additional-all-in-reserve-usd", type=float, required=True)
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="continue after authenticated approval and prompt for the key",
    )
    return parser.parse_args()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RunnerBlocked("timestamps must include a timezone")
    return parsed.astimezone(UTC)


async def _create_sessions(arguments: argparse.Namespace) -> list[SessionBinding]:
    turns = expanded_turns()
    validate_mechanical_spec(turns)
    conversation_ids = list(dict.fromkeys(turn["conversation_id"] for turn in turns))
    bindings: list[SessionBinding] = []
    async with AsyncSessionLocal() as db:
        for conversation_id in conversation_ids:
            created = await PublicChatService.create_session(
                db, arguments.widget_key, arguments.embedding_origin
            )
            bindings.append(
                SessionBinding(
                    fixture_conversation_id=conversation_id,
                    session_id=created["session"].id,
                    token=created["token"],
                )
            )
    return bindings


async def _close_sessions(bindings: list[SessionBinding]) -> list[int]:
    """Best-effort local cleanup without ever printing retained session tokens."""
    failures: list[int] = []
    for binding in bindings:
        try:
            async with AsyncSessionLocal() as db:
                session = await PublicChatService.verify_session_for_close(
                    db, binding.token
                )
                await PublicChatService.close_session(db, session)
        except Exception:  # noqa: BLE001 - preserve the original stop outcome
            failures.append(binding.session_id)
            continue
    return failures


def _build_plan(
    arguments: argparse.Namespace, bindings: list[SessionBinding]
) -> tuple[dict[str, Any], InputTokenCountPolicy, EvaluationPolicy]:
    count_expiry = _timestamp(arguments.count_evidence_expires_at)
    all_in_expiry = _timestamp(arguments.all_in_evidence_expires_at)
    identity = _source_identity()
    plan = OperatorActivationPlan.build(
        environment=settings.environment,
        organization_id=arguments.organization_id,
        public_chat_session_ids=tuple(item.session_id for item in bindings),
        run_id=arguments.run_id,
        expires_at=_timestamp(arguments.expires_at),
        count_billing_status="no_charge",
        count_billing_evidence_url="https://developers.openai.com/api/docs/guides/token-counting",
        count_billing_evidence_id=arguments.count_evidence_id,
        count_billing_evidence_expires_at=count_expiry,
        count_billing_independently_verified=False,
        additional_all_in_reserve_usd=arguments.additional_all_in_reserve_usd,
        all_in_evidence_id=arguments.all_in_evidence_id,
        all_in_evidence_expires_at=all_in_expiry,
        all_in_independently_verified=False,
        **identity,
    ).render()
    count_policy = InputTokenCountPolicy(
        endpoint=plan["activation_manifest"]["count_policy"]["endpoint"],
        model="gpt-6-luna",
        region="standard",
        reservation_per_attempt_usd=0,
        evidence_id=arguments.count_evidence_id,
        evidence_expires_at=count_expiry,
        billing_status="no_charge",
        independently_verified=False,
        explicitly_approved=False,
    )
    policy = EvaluationPolicy(
        additional_all_in_reserve_usd=arguments.additional_all_in_reserve_usd
    )
    return plan, count_policy, policy


async def _require_approval(
    arguments: argparse.Namespace,
    bindings: list[SessionBinding],
    proposed_manifest: dict[str, Any],
) -> tuple[dict[str, Any], InputTokenCountPolicy, EvaluationPolicy]:
    async with AsyncSessionLocal() as db:
        decision = await db.scalar(
            select(AIEvaluationReleaseDecision)
            .join(
                AIEvaluationRun,
                (AIEvaluationRun.id == AIEvaluationReleaseDecision.candidate_run_id)
                & (
                    AIEvaluationRun.organization_id
                    == AIEvaluationReleaseDecision.organization_id
                ),
            )
            .where(
                AIEvaluationRun.run_id == arguments.run_id,
                AIEvaluationRun.organization_id == arguments.organization_id,
                AIEvaluationReleaseDecision.organization_id
                == arguments.organization_id,
            )
            .order_by(
                AIEvaluationReleaseDecision.created_at.desc(),
                AIEvaluationReleaseDecision.id.desc(),
            )
            .limit(1)
        )
        if decision is None or decision.decision != "approved":
            raise EvaluationBlockedError(
                "latest durable evaluation decision is not approved"
            )
        manifest = (
            decision.comparison_snapshot.get("provider_activation")
            if decision is not None
            and isinstance(decision.comparison_snapshot, dict)
            else None
        )
        if not isinstance(manifest, dict):
            raise EvaluationBlockedError("durable evaluation approval is missing")
        approved_manifest = validate_activation_manifest(manifest)
        expected_approved_manifest = validate_activation_manifest(
            require_reviewed_billing_evidence(proposed_manifest)
        )
        if approved_manifest != expected_approved_manifest:
            raise EvaluationBlockedError(
                "approved manifest differs from the exact printed proposal"
            )
        current_identity = _source_identity()
        if any(
            approved_manifest.get(key) != value
            for key, value in current_identity.items()
        ):
            raise EvaluationBlockedError(
                "approved fixture or source changed after review"
            )
        approved_count = approved_manifest["count_policy"]
        count_policy = InputTokenCountPolicy(
            endpoint=approved_count["endpoint"],
            model=approved_count["model"],
            region=approved_count["region"],
            reservation_per_attempt_usd=approved_count[
                "reservation_per_attempt_usd"
            ],
            evidence_id=approved_count["evidence_id"],
            evidence_expires_at=_timestamp(approved_count["evidence_expires_at"]),
            billing_status=approved_count["billing_status_claim"],
            independently_verified=True,
            explicitly_approved=True,
        )
        policy = EvaluationPolicy(
            additional_all_in_reserve_usd=approved_manifest[
                "additional_all_in_reserve_usd"
            ]
        )
        for binding in bindings:
            await PersistedEvaluationActivationGuard.require(
                db,
                organization_id=arguments.organization_id,
                run_id=arguments.run_id,
                public_chat_session_id=binding.session_id,
                runtime_count_policy=count_policy,
                runtime_evaluation_policy=policy,
            )
    return approved_manifest, count_policy, policy


async def _execute_turns(
    bindings: list[SessionBinding],
    *,
    organization_id: int,
    approved_run_id: str,
    activation_manifest: dict[str, Any],
    embedding_origin: str,
) -> dict[str, Any]:
    by_fixture = {item.fixture_conversation_id: item for item in bindings}
    outcomes: list[dict[str, Any]] = []
    fixture = expanded_turns()
    fixture_sha256 = hashlib.sha256(
        json.dumps(
            fixture, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    execution_run_id = f"chat-eval-{uuid4().hex}"
    try:
        for turn in fixture:
            binding = by_fixture[turn["conversation_id"]]
            async with AsyncSessionLocal() as db:
                result = await PublicChatService.send_message(
                    db,
                    session_token=binding.token,
                    embedding_origin=embedding_origin,
                    text=turn["prompt"],
                    client_message_id=f"eval-{turn['turn_id']}",
                    delivery="sync",
                )
                unresolved_generation = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(AIUsageReservation)
                        .where(
                            AIUsageReservation.organization_id == organization_id,
                            AIUsageReservation.reservation_key.like(
                                f"model-eval:{organization_id}:{approved_run_id}:%"
                            ),
                            AIUsageReservation.status == STATUS_RESERVED,
                        )
                    )
                    or 0
                )
                unresolved_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(AIUsageReservation)
                        .where(
                            AIUsageReservation.organization_id == organization_id,
                            AIUsageReservation.reservation_key.like(
                                f"model-eval-count:{organization_id}:{approved_run_id}:%"
                            ),
                            AIUsageReservation.status == STATUS_RESERVED,
                        )
                    )
                    or 0
                )
                provider_log = await db.scalar(
                    select(AIRequestLog)
                    .where(
                        AIRequestLog.organization_id == organization_id,
                        AIRequestLog.feature == "public_chat_model_eval",
                        AIRequestLog.request_id
                        == result.get("_model_eval_request_id", ""),
                    )
                    .limit(1)
                )
            reply = result.get("reply")
            behavior = turn["expected_behavior"]
            forbidden_absent = not any(
                claim.casefold() in (reply or "").casefold()
                for claim in turn["forbidden_claims"]
            )
            if behavior == "silence_after_handoff":
                behavior_ok = reply is None
            elif behavior == "handoff":
                behavior_ok = bool(result.get("handoff"))
            elif behavior == "grounded_answer":
                behavior_ok = (
                    bool(reply)
                    and not result.get("handoff")
                    and provider_log is not None
                    and bool(provider_log.sources)
                )
            else:
                behavior_ok = bool(result.get("handoff")) or (
                    bool(reply) and provider_log is not None
                )
            exact_real_provider_observed = bool(
                provider_log is not None
                and provider_log.llm_called is True
                and provider_log.model == "gpt-6-luna"
                and provider_log.request_id
                == result.get("_model_eval_request_id")
            )
            outcomes.append(
                {
                    "turn_id": turn["turn_id"],
                    "expected_behavior": behavior,
                    "status": result["status"],
                    "reply_present": reply is not None,
                    "handoff": bool(result.get("handoff")),
                    "protocol_passed": behavior_ok and forbidden_absent,
                    "provider_path_observed": exact_real_provider_observed,
                    "mock_mechanics_observed": provider_log is not None
                    and not exact_real_provider_observed,
                    "citation_contract_enforced": provider_log is not None
                    and bool(provider_log.sources),
                }
            )
            if unresolved_generation or unresolved_count:
                raise RunnerBlocked(
                    "an ambiguous provider outcome requires reservation reconciliation"
                )
    except (BaseException, KeyboardInterrupt) as exc:
        await _persist_protocol_results(
            execution_run_id=execution_run_id,
            organization_id=organization_id,
            approved_run_id=approved_run_id,
            activation_manifest=activation_manifest,
            fixture_sha256=fixture_sha256,
            outcomes=outcomes,
            succeeded=False,
            error_category="interrupted" if isinstance(exc, KeyboardInterrupt) else "blocked",
        )
        raise
    await _persist_protocol_results(
        execution_run_id=execution_run_id,
        organization_id=organization_id,
        approved_run_id=approved_run_id,
        activation_manifest=activation_manifest,
        fixture_sha256=fixture_sha256,
        outcomes=outcomes,
        succeeded=len(outcomes) == 50,
        error_category=None,
    )
    return {
        "mode": "live_provider_evaluation",
        "execution_run_id": execution_run_id,
        "turns_attempted": len(outcomes),
        "sessions": len(bindings),
        "protocol_pass_rate": (
            sum(bool(item["protocol_passed"]) for item in outcomes) / len(outcomes)
            if outcomes
            else 0
        ),
        "quality_scores": None,
        "outcomes": outcomes,
    }


async def _persist_protocol_results(
    *,
    execution_run_id: str,
    organization_id: int,
    approved_run_id: str,
    activation_manifest: dict[str, Any],
    fixture_sha256: str,
    outcomes: list[dict[str, Any]],
    succeeded: bool,
    error_category: str | None,
) -> None:
    """Persist bounded protocol evidence, never customer text or credentials."""
    if not outcomes:
        return
    activation_manifest_sha256 = hashlib.sha256(
        json.dumps(
            activation_manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    async with AsyncSessionLocal() as db:
        passed = sum(bool(item["protocol_passed"]) for item in outcomes)
        run = AIEvaluationRun(
            run_id=execution_run_id,
            organization_id=organization_id,
            target_type="agent",
            status="succeeded" if succeeded else "failed",
            model="gpt-6-luna",
            embedding_model="none-lexical",
            corpus_revision={"frozen_fixture_sha256": fixture_sha256},
            trigger_source="manual",
            requested_by_subject="system:controlled-evaluation-runner",
            input={
                "synthetic_only": True,
                "fixture_sha256": fixture_sha256,
                "approved_prerequisite_run_id": approved_run_id,
                "activation_manifest_sha256": activation_manifest_sha256,
                "quality_scores": None,
            },
            pass_rate=passed / len(outcomes),
            metrics={
                "turns_attempted": len(outcomes),
                "protocol_passed": passed,
                "quality_measured": False,
            },
            error=error_category,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        db.add(run)
        await db.flush()
        for outcome in outcomes:
            db.add(
                AIEvaluationCase(
                    run_id=run.id,
                    organization_id=organization_id,
                    case_id=outcome["turn_id"],
                    case_type="agent",
                    input={"synthetic_fixture_turn_id": outcome["turn_id"]},
                    expected={"behavior": outcome["expected_behavior"]},
                    actual={
                        "status": outcome["status"],
                        "reply_present": outcome["reply_present"],
                        "handoff": outcome["handoff"],
                    },
                    dimensions={
                        "protocol_passed": outcome["protocol_passed"],
                        "citation_contract_enforced": outcome[
                            "citation_contract_enforced"
                        ],
                        "provider_path_observed": outcome[
                            "provider_path_observed"
                        ],
                        "mock_mechanics_observed": outcome[
                            "mock_mechanics_observed"
                        ],
                        "quality_measured": False,
                    },
                    fingerprint=hashlib.sha256(
                        f"{fixture_sha256}:{outcome['turn_id']}".encode()
                    ).hexdigest(),
                )
            )
        await db.commit()


async def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    if settings.environment != "development":
        raise RunnerBlocked("controlled evaluation is development-only")
    if not arguments.execute_live and (
        settings.model_eval_openai_source_enabled
        or settings.public_chat_model_eval_enabled
    ):
        raise RunnerBlocked("preflight requires both provider flags disabled")
    if arguments.execute_live and not (
        settings.model_eval_openai_source_enabled
        and settings.public_chat_model_eval_enabled
    ):
        raise RunnerBlocked("live continuation requires both process-scoped flags")

    bindings = await _create_sessions(arguments)
    try:
        plan, count_policy, policy = _build_plan(arguments, bindings)
        fixture_json = json.dumps(
            expanded_turns(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        preview = {
            "mode": (
                "offline_preflight"
                if not arguments.execute_live
                else "awaiting_approval"
            ),
            "fixture_sha256": hashlib.sha256(fixture_json.encode()).hexdigest(),
            "sessions": [
                {
                    "fixture_conversation_id": item.fixture_conversation_id,
                    "session_id": item.session_id,
                }
                for item in bindings
            ],
            "expected_operations": {
                "turns": 50,
                "maximum_count_requests": 100,
                "maximum_generation_requests": 100,
                "sdk_retries": 0,
            },
            "activation_manifest": plan["activation_manifest"],
            "approval_status": "not_checked",
            "evidence_status": "operator_proposal_pending_authenticated_review",
            "cost_assumptions": plan["budget"],
            "real_model_quality_measured": False,
        }
        print(json.dumps(preview, indent=2, ensure_ascii=False, sort_keys=True))
        if not arguments.execute_live:
            return {"status": "preflight_complete_no_provider_access", **preview}

        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise RunnerBlocked("live continuation requires a private interactive terminal")
        input(
            "Record the exact printed manifest through the authenticated evaluation.manage "
            "release-decision API, then press Enter to re-check approval: "
        )
        approved_manifest, count_policy, policy = await _require_approval(
            arguments, bindings, plan["activation_manifest"]
        )
        print("approval_status=approved_exact_manifest", file=sys.stderr)

        client = NoRetryHttpClient()
        try:
            with operator_entered_api_key() as secret:
                async with installed_development_evaluation_provider(
                    run_id=arguments.run_id,
                    http_client=client,
                    bearer_token=secret,
                    count_policy=count_policy,
                    evaluation_policy=policy,
                    explicit_operator_activation=True,
                ):
                    return await _execute_turns(
                        bindings,
                        organization_id=arguments.organization_id,
                        approved_run_id=arguments.run_id,
                        activation_manifest=approved_manifest,
                        embedding_origin=arguments.embedding_origin,
                    )
        finally:
            await client.aclose()
    finally:
        cleanup_failures = await _close_sessions(bindings)
        if cleanup_failures:
            print(
                f"cleanup_warning_count={len(cleanup_failures)}",
                file=sys.stderr,
            )


def main() -> None:
    try:
        result = asyncio.run(_run(_arguments()))
    except (EvaluationBlockedError, RunnerBlocked, KeyboardInterrupt) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
