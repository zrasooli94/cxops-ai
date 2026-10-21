"""C11 authenticated, tenant-selected, synthetic-only claim-review API.

Feature OFF by default and unavailable in any non-development environment.
All actor/organization IDs originate from real FastAPI JWT+membership+RBAC
dependencies (never request JSON). Service revalidates current DB role and
tenant on every operation. The only persisted fixture is the committed
fictional C6 dataset; NO customer text / LLM / external tools.

This router is *not* the evaluation release-decision API. Reviewer judgments
cannot approve a model, publish chat answers or run business tools.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenant, RequireCapability
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.schemas.claim_review import (
    AssignedClaimQueue,
    ClaimJudgmentRecorded,
    ReviewJudgmentCreate,
    ReviewerAssignmentCreate,
    ReviewerAssignmentResult,
    SyntheticBatchRead,
)
from app.services.private_claim_review_service import PrivateClaimReviewService

# Server refuses this entire route family unless an operator explicitly
# enables the preview in a DEVELOPMENT environment. Production always 404.
def require_synthetic_review_preview() -> None:
    config = get_settings()
    if (
        not config.claim_review_synthetic_api_enabled
        or config.environment != "development"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )


router = APIRouter(
    prefix="/evaluations/claim-reviews",
    tags=["Synthetic Claim Review (Disabled by Default)"],
    dependencies=[Depends(require_synthetic_review_preview)],
)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
ReviewReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.EVALUATION_READ)),
]
ReviewManageAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.EVALUATION_MANAGE)),
]
MemberManageAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.MEMBER_MANAGE)),
]

BatchId = Annotated[int, Path(ge=1, le=2_147_483_647)]
ReviewKey = Annotated[
    str,
    Path(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z][a-z0-9-]{0,79}:[a-z][a-z0-9-]{0,79}$",
    ),
]

AUTH_RESPONSES = {
    401: {"description": "Staff JWT missing or invalid"},
    403: {"description": "Tenant membership or capability denied"},
    404: {"description": "Preview off, inaccessible tenant or missing claim"},
    409: {"description": "Duplicate immutable decision or review conflict"},
}


def _safe_review_error(exc: ValueError | PermissionError | IntegrityError) -> HTTPException:
    """Fixed error vocabulary; never echo reviewer ID, source, SQL or notes."""
    if isinstance(exc, PermissionError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    if isinstance(exc, IntegrityError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Claim review write conflict",
        )
    detail = str(exc).casefold()
    if "not found" in detail:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Claim review record not found",
        )
    if any(fragment in detail for fragment in (
        "already", "duplicate", "disputed", "frozen", "changed",
        "two completed",
    )):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Claim review state conflict",
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid synthetic claim review input",
    )


@router.post(
    "/synthetic-batches",
    response_model=SyntheticBatchRead,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_RESPONSES,
)
async def create_synthetic_claim_batch(
    request: Request,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: ReviewManageAuthz,
    _read: ReviewReadAuthz,
    _staff_admin: MemberManageAuthz,
):
    """Owner/admin only. Constructs a fixed fictional C6 snapshot; no body."""
    # Deliberately no request-body fields. Reject opaque uploads rather than
    # silently accepting caller-supplied tenant IDs or sensitive source text.
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Synthetic batch creation accepts no request body",
            )
    try:
        batch = await PrivateClaimReviewService.create_synthetic_batch(
            db, actor=authz,
        )
        await db.commit()
        return SyntheticBatchRead(
            id=batch.id,
            dataset_id=batch.dataset_id,
            item_count=12,
            created_at=batch.created_at,
        )
    except (ValueError, PermissionError, IntegrityError) as exc:
        await db.rollback()
        raise _safe_review_error(exc) from None


@router.post(
    "/batches/{batch_id}/assignments",
    response_model=ReviewerAssignmentResult,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_RESPONSES,
)
async def assign_claim_reviewers(
    batch_id: BatchId,
    data: ReviewerAssignmentCreate,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: ReviewManageAuthz,
    _read: ReviewReadAuthz,
    _staff_admin: MemberManageAuthz,
):
    """Select two distinct CURRENT tenant members, never user-supplied actor."""
    try:
        assignments = await PrivateClaimReviewService.assign_reviewers(
            db,
            actor=authz,
            batch_id=batch_id,
            reviewer_one_subject=data.reviewer_one_subject,
            reviewer_two_subject=data.reviewer_two_subject,
        )
        await db.commit()
        return ReviewerAssignmentResult(
            batch_id=batch_id, assigned_slots=len(assignments),
        )
    except (ValueError, PermissionError, IntegrityError) as exc:
        await db.rollback()
        raise _safe_review_error(exc) from None


@router.get(
    "/batches/{batch_id}/my-queue",
    response_model=AssignedClaimQueue,
    responses=AUTH_RESPONSES,
)
async def my_assigned_claim_queue(
    batch_id: BatchId,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: ReviewManageAuthz,
    _read: ReviewReadAuthz,
):
    """Return only own per-claim hashes/completion. Never other votes/IDs."""
    try:
        rows = await PrivateClaimReviewService.assigned_queue(
            db, actor=authz, batch_id=batch_id,
        )
        return AssignedClaimQueue(
            batch_id=batch_id,
            items=rows,
            total=len(rows),
        )
    except (ValueError, PermissionError) as exc:
        raise _safe_review_error(exc) from None


@router.post(
    "/batches/{batch_id}/reviews/{review_key}",
    response_model=ClaimJudgmentRecorded,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_RESPONSES,
)
async def submit_my_claim_review(
    batch_id: BatchId,
    review_key: ReviewKey,
    data: ReviewJudgmentCreate,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: ReviewManageAuthz,
    _read: ReviewReadAuthz,
):
    """Immutable verdict, authenticated subject only, no body actor selector."""
    try:
        record = await PrivateClaimReviewService.submit_review(
            db,
            actor=authz,
            batch_id=batch_id,
            review_key=review_key,
            label=data.label,
            evidence_excerpt=data.evidence_excerpt,
            rationale=data.rationale,
        )
        await db.commit()
        return ClaimJudgmentRecorded(
            id=record.id,
            batch_id=batch_id,
            review_key=review_key,
            event_kind=record.event_kind,
            label=record.label,
            created_at=record.created_at,
        )
    except (ValueError, PermissionError, IntegrityError) as exc:
        await db.rollback()
        raise _safe_review_error(exc) from None


@router.post(
    "/batches/{batch_id}/adjudications/{review_key}",
    response_model=ClaimJudgmentRecorded,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_RESPONSES,
)
async def adjudicate_claim_disagreement(
    batch_id: BatchId,
    review_key: ReviewKey,
    data: ReviewJudgmentCreate,
    tenant: CurrentTenant,
    db: DatabaseSession,
    authz: ReviewManageAuthz,
    _read: ReviewReadAuthz,
):
    """Third authenticated manager only, and only for two differing labels."""
    try:
        record = await PrivateClaimReviewService.adjudicate(
            db,
            actor=authz,
            batch_id=batch_id,
            review_key=review_key,
            label=data.label,
            evidence_excerpt=data.evidence_excerpt,
            rationale=data.rationale,
        )
        await db.commit()
        return ClaimJudgmentRecorded(
            id=record.id,
            batch_id=batch_id,
            review_key=review_key,
            event_kind=record.event_kind,
            label=record.label,
            created_at=record.created_at,
        )
    except (ValueError, PermissionError, IntegrityError) as exc:
        await db.rollback()
        raise _safe_review_error(exc) from None
