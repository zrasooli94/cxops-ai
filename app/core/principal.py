from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Represents a verified authenticated user/principal.

    This is the application's internal identity abstraction.
    Business services should depend on this rather than raw JWT claims.
    """

    subject: str  # stable user identifier (sub claim)
    email: str | None = None
    issuer: str | None = None
    auth_method: str = "jwt"  # e.g., "jwt", "oauth", "api_key"
    token_claims: dict | None = None  # raw claims if needed by specialized code

    def __str__(self) -> str:
        return f"Principal(sub={self.subject}, email={self.email})"
