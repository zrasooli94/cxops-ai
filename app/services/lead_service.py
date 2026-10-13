"""Early-access / demo-request leads (Phase 1Q).

A lead records interest. It never creates an organization, a membership or a
widget by itself: an operator reviews it, and only an *approved* lead can be
provisioned - into an ``onboarding`` organization plus a single-use owner
invitation. Lifecycle::

    new -> reviewing -> contacted -> approved
      \\          \\           \\-> declined
       \\-> contacted, declined   (declined -> reviewing re-opens)

Public submission is behind ``settings.public_lead_capture_enabled`` (default
off). Abuse controls, cheapest first:

* strict field bounds and an allowlisted industry value (schema);
* a honeypot field - a filled-in honeypot is answered exactly like a real
  submission and stored nowhere;
* a platform-wide hourly ceiling (robust: it does not depend on a client IP
  the API cannot verify) and a per-email daily ceiling;
* at most one open lead per email (partial unique index): a repeat submission
  only bumps ``submission_count`` - it never overwrites what the first
  submission said, so nobody can rewrite someone else's request.

The public response is identical for a new lead, a duplicate and a honeypot
hit, so the endpoint cannot be used to learn whether an address is known.
Logs carry lead ids and outcomes, never names, emails or inquiry text.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rbac import OrganizationRole
from app.models.commercial import (
    LEAD_OPEN_STATUSES,
    LEAD_STATUS_APPROVED,
    LEAD_STATUS_CONTACTED,
    LEAD_STATUS_DECLINED,
    LEAD_STATUS_NEW,
    LEAD_STATUS_REVIEWING,
    LEAD_STATUSES,
    SalesLead,
)
from app.models.organization import LIFECYCLE_ONBOARDING, Organization
from app.services.commercial_common import normalize_email, sha256_hex
from app.services.public_chat_rate_limiter import public_chat_rate_limiter_db
from app.services.team_service import TeamService

log = get_logger(__name__)

CONSENT_VERSION = "2026-10-early-access-v1"

LEAD_TRANSITIONS: dict[str, frozenset[str]] = {
    LEAD_STATUS_NEW: frozenset({LEAD_STATUS_REVIEWING, LEAD_STATUS_CONTACTED, LEAD_STATUS_DECLINED}),
    LEAD_STATUS_REVIEWING: frozenset(
        {LEAD_STATUS_CONTACTED, LEAD_STATUS_APPROVED, LEAD_STATUS_DECLINED}
    ),
    LEAD_STATUS_CONTACTED: frozenset(
        {LEAD_STATUS_REVIEWING, LEAD_STATUS_APPROVED, LEAD_STATUS_DECLINED}
    ),
    LEAD_STATUS_APPROVED: frozenset(),
    LEAD_STATUS_DECLINED: frozenset({LEAD_STATUS_REVIEWING}),
}


class LeadError(Exception):
    status_code = 400


class LeadCaptureDisabledError(LeadError):
    status_code = 503

    def __init__(self) -> None:
        super().__init__("Early-access requests are not open yet.")


class LeadRateLimitedError(LeadError):
    status_code = 429

    def __init__(self) -> None:
        super().__init__("We are receiving a lot of requests. Please try again later.")


class LeadNotFoundError(LeadError):
    status_code = 404


class LeadConflictError(LeadError):
    status_code = 409


def _lead_payload(lead: SalesLead) -> dict[str, Any]:
    return {
        "id": lead.id,
        "company_name": lead.company_name,
        "contact_name": lead.contact_name,
        "email": lead.email,
        "website": lead.website,
        "industry": lead.industry,
        "inquiry": lead.inquiry,
        "status": lead.status,
        "allowed_transitions": sorted(LEAD_TRANSITIONS.get(lead.status, frozenset())),
        "submission_count": lead.submission_count,
        "consent_version": lead.consent_version,
        "consented_at": lead.consented_at.isoformat(),
        "operator_notes": lead.operator_notes,
        "status_changed_at": lead.status_changed_at.isoformat() if lead.status_changed_at else None,
        "provisioned_organization_id": lead.provisioned_organization_id,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "last_submitted_at": lead.last_submitted_at.isoformat() if lead.last_submitted_at else None,
    }


RECEIVED = {
    "status": "received",
    "message": (
        "Thanks - your request has been received. Our team reviews every request "
        "and will contact you by email."
    ),
}


class LeadService:
    @staticmethod
    def availability() -> dict[str, Any]:
        return {"accepting": get_settings().public_lead_capture_enabled}

    @classmethod
    async def submit(
        cls,
        db: AsyncSession,
        *,
        company_name: str,
        contact_name: str,
        email: str,
        website: str | None,
        industry: str,
        inquiry: str | None,
        honeypot: str | None,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        if not settings.public_lead_capture_enabled:
            raise LeadCaptureDisabledError()
        if honeypot:
            log.info("lead_submission_discarded", reason="honeypot")
            return dict(RECEIVED)

        normalized = normalize_email(email)
        moment = now_epoch if now_epoch is not None else time.time()
        if not await public_chat_rate_limiter_db.allow(
            db,
            scope="lead_platform",
            key="all",
            limit=settings.lead_submissions_per_hour_platform,
            window_seconds=3600.0,
            now_epoch=moment,
        ) or not await public_chat_rate_limiter_db.allow(
            db,
            scope="lead_email",
            key=sha256_hex(normalized),
            limit=settings.lead_submissions_per_day_per_email,
            window_seconds=86400.0,
            now_epoch=moment,
        ):
            await db.commit()
            log.info("lead_submission_rejected", reason="rate_limited")
            raise LeadRateLimitedError()

        lead_id, outcome = await cls._insert_or_fold(
            db,
            company_name=company_name.strip(),
            contact_name=contact_name.strip(),
            email=email.strip(),
            normalized=normalized,
            website=website,
            industry=industry,
            inquiry=(inquiry or "").strip() or None,
        )
        log.info("lead_submission_recorded", lead_id=lead_id, outcome=outcome)
        return dict(RECEIVED)

    @staticmethod
    async def _insert_or_fold(
        db: AsyncSession,
        *,
        company_name: str,
        contact_name: str,
        email: str,
        normalized: str,
        website: str | None,
        industry: str,
        inquiry: str | None,
    ) -> tuple[int, str]:
        async def fold() -> tuple[int, str] | None:
            open_lead = (
                await db.execute(
                    select(SalesLead)
                    .where(
                        SalesLead.email_normalized == normalized,
                        SalesLead.status.in_(LEAD_OPEN_STATUSES),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if open_lead is None:
                return None
            open_lead.submission_count = open_lead.submission_count + 1
            open_lead.last_submitted_at = datetime.now(UTC)
            await db.commit()
            return open_lead.id, "duplicate_folded"

        folded = await fold()
        if folded is not None:
            return folded
        now = datetime.now(UTC)
        lead = SalesLead(
            company_name=company_name,
            contact_name=contact_name,
            email=email,
            email_normalized=normalized,
            website=website,
            industry=industry,
            inquiry=inquiry,
            consent_version=CONSENT_VERSION,
            consented_at=now,
            status=LEAD_STATUS_NEW,
            last_submitted_at=now,
        )
        db.add(lead)
        try:
            await db.commit()
        except IntegrityError:
            # A concurrent submission for the same address created the open
            # lead first; fold into it instead.
            await db.rollback()
            folded = await fold()
            if folded is None:
                raise
            return folded
        return lead.id, "created"

    # ------------------------------------------------------------------
    # Operator side
    # ------------------------------------------------------------------

    @staticmethod
    async def list_leads(
        db: AsyncSession, *, status: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        query = select(SalesLead).order_by(SalesLead.created_at.desc(), SalesLead.id.desc())
        if status:
            if status not in LEAD_STATUSES:
                raise LeadError(f"unknown status {status!r}")
            query = query.where(SalesLead.status == status)
        rows = (await db.execute(query.limit(max(1, min(limit, 200))))).scalars()
        counts = dict(
            (
                await db.execute(
                    select(SalesLead.status, func.count()).group_by(SalesLead.status)
                )
            ).all()
        )
        return {
            "leads": [_lead_payload(row) for row in rows],
            "counts": {state: int(counts.get(state, 0)) for state in LEAD_STATUSES},
        }

    @staticmethod
    async def get_lead(db: AsyncSession, lead_id: int) -> dict[str, Any]:
        lead = await db.get(SalesLead, lead_id)
        if lead is None:
            raise LeadNotFoundError("Lead not found.")
        return _lead_payload(lead)

    @staticmethod
    async def update_lead(
        db: AsyncSession,
        *,
        lead_id: int,
        operator_subject: str,
        status: str | None,
        operator_notes: str | None,
        notes_provided: bool,
    ) -> dict[str, Any]:
        lead = (
            await db.execute(select(SalesLead).where(SalesLead.id == lead_id).with_for_update())
        ).scalar_one_or_none()
        if lead is None:
            await db.rollback()
            raise LeadNotFoundError("Lead not found.")
        previous = lead.status
        if status is not None and status != lead.status:
            if status not in LEAD_TRANSITIONS.get(previous, frozenset()):
                await db.rollback()
                raise LeadConflictError(f"A {previous} lead cannot move to {status}.")
            lead.status = status
            lead.status_changed_at = datetime.now(UTC)
            lead.status_changed_by = operator_subject
        if notes_provided:
            lead.operator_notes = (operator_notes or "").strip() or None
        try:
            await db.commit()
        except IntegrityError as exc:
            # Re-opening a declined lead while a newer open lead exists for the
            # same address would violate the one-open-lead index.
            await db.rollback()
            raise LeadConflictError(
                "Another open request exists for this email address."
            ) from exc
        await db.refresh(lead)
        log.info(
            "lead_updated",
            lead_id=lead_id,
            operator_subject=operator_subject,
            from_status=previous,
            to_status=lead.status,
            notes_changed=notes_provided,
        )
        return _lead_payload(lead)

    @staticmethod
    async def provision(
        db: AsyncSession,
        *,
        lead_id: int,
        operator_subject: str,
        organization_name: str | None,
    ) -> dict[str, Any]:
        """Create the onboarding organization and a one-time owner invitation.

        Runs under a row lock on the lead, so two operators provisioning the
        same lead at once produce one organization; the loser gets 409.
        """
        lead = (
            await db.execute(select(SalesLead).where(SalesLead.id == lead_id).with_for_update())
        ).scalar_one_or_none()
        if lead is None:
            await db.rollback()
            raise LeadNotFoundError("Lead not found.")
        if lead.status != LEAD_STATUS_APPROVED:
            await db.rollback()
            raise LeadConflictError("Only an approved lead can be provisioned.")
        if lead.provisioned_organization_id is not None:
            await db.rollback()
            raise LeadConflictError("This lead has already been provisioned.")

        name = (organization_name or lead.company_name).strip()
        if not name or len(name) > 255:
            await db.rollback()
            raise LeadError("organization name must be 1-255 characters")
        organization = Organization(
            name=name,
            industry=lead.industry,
            website_url=lead.website,
            lifecycle_status=LIFECYCLE_ONBOARDING,
            lifecycle_changed_at=datetime.now(UTC),
            lifecycle_changed_by=operator_subject,
        )
        db.add(organization)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise LeadConflictError(
                "An organization with this name already exists. Provide a different name."
            ) from exc
        issued = await TeamService.issue_invitation(
            db,
            organization_id=organization.id,
            email=lead.email,
            role=OrganizationRole.OWNER,
            invited_by=operator_subject,
            commit=False,
        )
        lead.provisioned_organization_id = organization.id
        await db.commit()
        log.info(
            "lead_provisioned",
            lead_id=lead_id,
            organization_id=organization.id,
            operator_subject=operator_subject,
            invitation_id=issued.invitation.id,
        )
        return {
            "lead_id": lead_id,
            "organization_id": organization.id,
            "organization_name": organization.name,
            "lifecycle_status": organization.lifecycle_status,
            "owner_invitation": {
                "id": issued.invitation.id,
                "email": issued.invitation.email,
                "role": issued.invitation.role,
                "expires_at": issued.invitation.expires_at.isoformat(),
                "token": issued.token,
            },
            "delivery": (
                "Invitation email delivery is not configured. Send the accept link "
                "to the customer yourself; it is shown only once."
            ),
        }
