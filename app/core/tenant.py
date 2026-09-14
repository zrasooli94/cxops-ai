from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    """Immutable, provider-neutral trusted tenant for an authenticated principal.

    Identity (AuthenticatedPrincipal) answers "who is this user?"; TenantContext
    answers "which organization may this authenticated user operate inside?".

    It contains only the resolved organization and the subject it was resolved
    for. Authorization roles are deliberately absent (Phase 1D).
    """
    organization_id: int
    subject: str

    def __str__(self) -> str:
        return f"Tenant(org={self.organization_id})"