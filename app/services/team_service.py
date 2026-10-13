"""Team membership and invitations (Phase 1Q).

Built on the existing ``organization_memberships`` table and Nhost identities:
an invitation, once accepted, is nothing more than a membership row for the
accepting principal's verified ``sub``. No second identity system exists.

Privilege rules (enforced here, not in the route layer, so every caller gets
them):

* only ``member.manage`` holders reach this service (route dependency);
* an actor may grant or change only roles strictly below their own rank - an
  owner may additionally grant ``owner`` (ownership hand-over);
* an actor may change or remove only members strictly below their own rank,
  except that an owner may manage anyone;
* nobody changes their own role (no self-escalation, no accidental lockout);
* the last owner can be neither demoted nor removed.

Invitation tokens are 256-bit random values. Only their SHA-256 digest is
stored; the raw token is returned exactly once, at creation. Acceptance is a
single conditional UPDATE (pending + unexpired), so concurrent or repeated
acceptance of one token yields exactly one membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rbac import AuthorizationContext, Capability, OrganizationRole
from app.models.commercial import (
    INVITATION_STATUS_ACCEPTED,
    INVITATION_STATUS_PENDING,
    INVITATION_STATUS_REVOKED,
    OrganizationInvitation,
)
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.services.commercial_common import (
    generate_invitation_token,
    normalize_email,
    sha256_hex,
)
from app.services.identity_verification import VerifiedIdentity

log = get_logger(__name__)

ROLE_RANK: dict[OrganizationRole, int] = {
    OrganizationRole.OWNER: 50,
    OrganizationRole.ADMIN: 40,
    OrganizationRole.SUPERVISOR: 30,
    OrganizationRole.AGENT: 20,
    OrganizationRole.VIEWER: 10,
}


class TeamError(Exception):
    """Base error; ``status_code`` is the HTTP mapping."""

    status_code = 400


class TeamPermissionError(TeamError):
    status_code = 403


class TeamNotFoundError(TeamError):
    status_code = 404


class TeamConflictError(TeamError):
    status_code = 409


class InvitationInvalidError(TeamError):
    """Unknown, expired, revoked or already-used token. One generic message so
    a caller cannot tell which, and cannot probe for valid tokens."""

    status_code = 404

    def __init__(self) -> None:
        super().__init__("This invitation is invalid or has expired.")


def can_assign_role(actor: OrganizationRole, target: OrganizationRole) -> bool:
    if actor == OrganizationRole.OWNER:
        return True
    return ROLE_RANK[target] < ROLE_RANK[actor]


def can_manage_member(actor: OrganizationRole, member: OrganizationRole) -> bool:
    if actor == OrganizationRole.OWNER:
        return True
    return ROLE_RANK[member] < ROLE_RANK[actor]


def assignable_roles(actor: OrganizationRole) -> list[str]:
    return [role.value for role in OrganizationRole if can_assign_role(actor, role)]


def _parse_role(value: str) -> OrganizationRole:
    try:
        return OrganizationRole(value)
    except ValueError as exc:
        raise TeamError(f"unknown role {value!r}") from exc


def invitation_state(invitation: OrganizationInvitation, now: datetime | None = None) -> str:
    """``pending`` | ``expired`` | ``accepted`` | ``revoked``."""
    moment = now or datetime.now(UTC)
    if invitation.status == INVITATION_STATUS_PENDING and invitation.expires_at <= moment:
        return "expired"
    return invitation.status


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: OrganizationInvitation
    token: str


class TeamService:
    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    @staticmethod
    async def list_members(
        db: AsyncSession, *, authz: AuthorizationContext
    ) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(OrganizationMembership)
                .where(OrganizationMembership.organization_id == authz.organization_id)
                .order_by(OrganizationMembership.id)
            )
        ).scalars()
        members = []
        for row in rows:
            role = OrganizationRole(row.role)
            members.append(
                {
                    "id": row.id,
                    "email": row.email,
                    "role": role.value,
                    "is_you": row.subject == authz.subject,
                    "joined_at": row.created_at.isoformat() if row.created_at else None,
                    "can_manage": row.subject != authz.subject
                    and can_manage_member(authz.role, role),
                }
            )
        return members

    @staticmethod
    async def _lock_members(db: AsyncSession, organization_id: int) -> list[OrganizationMembership]:
        """Lock every membership of the organization for this transaction so
        concurrent demotions/removals cannot both see "another owner exists"."""
        return list(
            (
                await db.execute(
                    select(OrganizationMembership)
                    .where(OrganizationMembership.organization_id == organization_id)
                    .order_by(OrganizationMembership.id)
                    .with_for_update()
                )
            ).scalars()
        )

    @classmethod
    async def change_role(
        cls,
        db: AsyncSession,
        *,
        authz: AuthorizationContext,
        membership_id: int,
        new_role: str,
    ) -> dict[str, Any]:
        target_role = _parse_role(new_role)
        if not authz.has(Capability.MEMBER_MANAGE):
            raise TeamPermissionError("Insufficient permissions")
        try:
            members = await cls._lock_members(db, authz.organization_id)
            member = next((m for m in members if m.id == membership_id), None)
            if member is None:
                raise TeamNotFoundError("Member not found.")
            if member.subject == authz.subject:
                raise TeamPermissionError("You cannot change your own role.")
            current = OrganizationRole(member.role)
            if not can_manage_member(authz.role, current):
                raise TeamPermissionError("You cannot change the role of this member.")
            if not can_assign_role(authz.role, target_role):
                raise TeamPermissionError("You cannot assign this role.")
            if current == OrganizationRole.OWNER and target_role != OrganizationRole.OWNER:
                owners = [m for m in members if m.role == OrganizationRole.OWNER.value]
                if len(owners) <= 1:
                    raise TeamConflictError("An organization must keep at least one owner.")
            member.role = target_role
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        log.info(
            "team_member_role_changed",
            organization_id=authz.organization_id,
            actor_subject=authz.subject,
            membership_id=membership_id,
            from_role=current.value,
            to_role=target_role.value,
        )
        return {"id": membership_id, "role": target_role.value}

    @classmethod
    async def remove_member(
        cls, db: AsyncSession, *, authz: AuthorizationContext, membership_id: int
    ) -> None:
        try:
            members = await cls._lock_members(db, authz.organization_id)
            member = next((m for m in members if m.id == membership_id), None)
            if member is None:
                raise TeamNotFoundError("Member not found.")
            current = OrganizationRole(member.role)
            removed_subject = member.subject
            is_self = removed_subject == authz.subject
            # Anyone may leave; removing someone else needs member.manage AND
            # a higher rank than the member (owners may remove anyone).
            if not is_self and (
                not authz.has(Capability.MEMBER_MANAGE)
                or not can_manage_member(authz.role, current)
            ):
                raise TeamPermissionError("You cannot remove this member.")
            if current == OrganizationRole.OWNER:
                owners = [m for m in members if m.role == OrganizationRole.OWNER.value]
                if len(owners) <= 1:
                    raise TeamConflictError("An organization must keep at least one owner.")
            await db.delete(member)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        log.info(
            "team_member_removed",
            organization_id=authz.organization_id,
            actor_subject=authz.subject,
            membership_id=membership_id,
            role=current.value,
            self_removal=is_self,
        )

    # ------------------------------------------------------------------
    # Invitations
    # ------------------------------------------------------------------

    @staticmethod
    def _invitation_payload(invitation: OrganizationInvitation) -> dict[str, Any]:
        return {
            "id": invitation.id,
            "email": invitation.email,
            "role": invitation.role,
            "status": invitation_state(invitation),
            "expires_at": invitation.expires_at.isoformat(),
            "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
            "accepted_at": invitation.accepted_at.isoformat() if invitation.accepted_at else None,
            "revoked_at": invitation.revoked_at.isoformat() if invitation.revoked_at else None,
        }

    @classmethod
    async def issue_invitation(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        email: str,
        role: OrganizationRole,
        invited_by: str,
        commit: bool = True,
    ) -> IssuedInvitation:
        """Create a pending invitation, replacing any live one for the address.

        No authorization here: callers (the team route, the operator lead
        provisioning flow) have already decided the actor may grant ``role``.
        """
        normalized = normalize_email(email)
        now = datetime.now(UTC)
        await db.execute(
            update(OrganizationInvitation)
            .where(
                OrganizationInvitation.organization_id == organization_id,
                OrganizationInvitation.email_normalized == normalized,
                OrganizationInvitation.status == INVITATION_STATUS_PENDING,
            )
            .values(
                status=INVITATION_STATUS_REVOKED,
                revoked_at=now,
                revoked_by_subject=invited_by,
            )
        )
        token = generate_invitation_token()
        invitation = OrganizationInvitation(
            organization_id=organization_id,
            email=email.strip(),
            email_normalized=normalized,
            role=role.value,
            token_hash=sha256_hex(token),
            status=INVITATION_STATUS_PENDING,
            invited_by_subject=invited_by,
            expires_at=now + timedelta(hours=get_settings().team_invitation_ttl_hours),
        )
        db.add(invitation)
        await db.flush()
        if commit:
            await db.commit()
            await db.refresh(invitation)
        log.info(
            "team_invitation_issued",
            organization_id=organization_id,
            actor_subject=invited_by,
            invitation_id=invitation.id,
            role=role.value,
        )
        return IssuedInvitation(invitation=invitation, token=token)

    @classmethod
    async def invite(
        cls,
        db: AsyncSession,
        *,
        authz: AuthorizationContext,
        email: str,
        role: str,
    ) -> dict[str, Any]:
        target_role = _parse_role(role)
        if not authz.has(Capability.MEMBER_MANAGE):
            raise TeamPermissionError("Insufficient permissions")
        if not can_assign_role(authz.role, target_role):
            raise TeamPermissionError("You cannot invite someone with this role.")
        normalized = normalize_email(email)
        existing_member = await db.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == authz.organization_id,
                func.lower(OrganizationMembership.email) == normalized,
            )
        )
        if existing_member:
            raise TeamConflictError("This person is already a member of the organization.")
        try:
            issued = await cls.issue_invitation(
                db,
                organization_id=authz.organization_id,
                email=email,
                role=target_role,
                invited_by=authz.subject,
            )
        except IntegrityError as exc:
            # A concurrent invite for the same address won the pending slot.
            await db.rollback()
            raise TeamConflictError(
                "An invitation for this address was just created. Refresh and try again."
            ) from exc
        payload = cls._invitation_payload(issued.invitation)
        payload["token"] = issued.token
        return payload

    @classmethod
    async def list_invitations(
        cls, db: AsyncSession, *, organization_id: int
    ) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(OrganizationInvitation)
                .where(OrganizationInvitation.organization_id == organization_id)
                .order_by(OrganizationInvitation.id.desc())
                .limit(100)
            )
        ).scalars()
        return [cls._invitation_payload(row) for row in rows]

    @classmethod
    async def revoke_invitation(
        cls, db: AsyncSession, *, authz: AuthorizationContext, invitation_id: int
    ) -> dict[str, Any]:
        invitation = (
            await db.execute(
                select(OrganizationInvitation)
                .where(
                    OrganizationInvitation.id == invitation_id,
                    OrganizationInvitation.organization_id == authz.organization_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if invitation is None:
            await db.rollback()
            raise TeamNotFoundError("Invitation not found.")
        if not can_assign_role(authz.role, OrganizationRole(invitation.role)):
            await db.rollback()
            raise TeamPermissionError("You cannot revoke this invitation.")
        if invitation.status != INVITATION_STATUS_PENDING:
            current_status = invitation.status
            await db.rollback()
            raise TeamConflictError(f"The invitation is already {current_status}.")
        invitation.status = INVITATION_STATUS_REVOKED
        invitation.revoked_at = datetime.now(UTC)
        invitation.revoked_by_subject = authz.subject
        await db.commit()
        await db.refresh(invitation)
        log.info(
            "team_invitation_revoked",
            organization_id=authz.organization_id,
            actor_subject=authz.subject,
            invitation_id=invitation_id,
        )
        return cls._invitation_payload(invitation)

    @staticmethod
    async def _pending_by_token(
        db: AsyncSession, token: str
    ) -> OrganizationInvitation | None:
        if not token or len(token) > 200:
            return None
        invitation = (
            await db.execute(
                select(OrganizationInvitation).where(
                    OrganizationInvitation.token_hash == sha256_hex(token)
                )
            )
        ).scalar_one_or_none()
        if invitation is None or invitation_state(invitation) != INVITATION_STATUS_PENDING:
            return None
        return invitation

    @classmethod
    async def preview(cls, db: AsyncSession, *, token: str) -> dict[str, Any]:
        """What accepting would do. Reveals only the organization name, role and
        expiry - nothing about other members - and only for a live token."""
        invitation = await cls._pending_by_token(db, token)
        if invitation is None:
            raise InvitationInvalidError()
        name = await db.scalar(
            select(Organization.name).where(Organization.id == invitation.organization_id)
        )
        return {
            "organization_name": name,
            "role": invitation.role,
            "email": invitation.email,
            "expires_at": invitation.expires_at.isoformat(),
        }

    @classmethod
    async def accept(
        cls,
        db: AsyncSession,
        *,
        token: str,
        identity: VerifiedIdentity,
    ) -> dict[str, Any]:
        """Redeem ``token`` for the *verified* identity.

        ``identity`` comes from :mod:`app.services.identity_verification`: the
        provider has confirmed that ``identity.subject`` owns the verified
        address ``identity.email``. The invitation is bound to its recipient,
        so a forwarded link fails here even though the token is valid.
        Missing or unverified evidence never reaches this method (the
        verifier refuses first), so there is no "no email -> skip" path.
        """
        subject = identity.subject
        invitation = await cls._pending_by_token(db, token)
        if invitation is None:
            raise InvitationInvalidError()
        if identity.email != invitation.email_normalized:
            log.warning(
                "team_invitation_recipient_mismatch",
                organization_id=invitation.organization_id,
                invitation_id=invitation.id,
                subject=subject,
            )
            raise TeamPermissionError(
                "This invitation was issued to a different email address. "
                "Sign in with the invited account."
            )
        organization_id = invitation.organization_id
        already = await db.scalar(
            select(OrganizationMembership.id).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.subject == subject,
            )
        )
        if already is not None:
            raise TeamConflictError("You are already a member of this organization.")

        now = datetime.now(UTC)
        try:
            claimed = (
                await db.execute(
                    update(OrganizationInvitation)
                    .where(
                        OrganizationInvitation.id == invitation.id,
                        OrganizationInvitation.status == INVITATION_STATUS_PENDING,
                        OrganizationInvitation.expires_at > now,
                    )
                    .values(
                        status=INVITATION_STATUS_ACCEPTED,
                        accepted_at=now,
                        accepted_by_subject=subject,
                    )
                    .returning(OrganizationInvitation.id, OrganizationInvitation.role)
                )
            ).first()
            if claimed is None:
                raise InvitationInvalidError()
            db.add(
                OrganizationMembership(
                    organization_id=organization_id,
                    subject=subject,
                    role=OrganizationRole(claimed.role),
                    email=invitation.email,
                )
            )
            await db.commit()
        except IntegrityError as exc:
            # The membership unique constraint fired: the same person accepted a
            # second invitation concurrently. The claim rolls back with it.
            await db.rollback()
            raise TeamConflictError("You are already a member of this organization.") from exc
        except BaseException:
            await db.rollback()
            raise
        name = await db.scalar(select(Organization.name).where(Organization.id == organization_id))
        log.info(
            "team_invitation_accepted",
            organization_id=organization_id,
            invitation_id=invitation.id,
            accepted_by_subject=subject,
            role=claimed.role,
        )
        return {
            "organization_id": organization_id,
            "organization_name": name,
            "role": claimed.role,
        }
