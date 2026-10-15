"""Wire contracts for Phase 1Q commercial onboarding.

Every request model forbids unknown fields, so a client cannot smuggle an
``organization_id``, ``lifecycle_status``, ``role`` override or allowance into
an endpoint that does not explicitly accept it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services.commercial_common import CommercialValidationError, normalize_website

LEAD_INDUSTRIES = (
    "automotive",
    "local_services",
    "retail_ecommerce",
    "professional_services",
    "healthcare_wellness",
    "hospitality_travel",
    "education",
    "real_estate",
    "technology_saas",
    "other",
)

LeadIndustry = Literal[
    "automotive",
    "local_services",
    "retail_ecommerce",
    "professional_services",
    "healthcare_wellness",
    "hospitality_travel",
    "education",
    "real_estate",
    "technology_saas",
    "other",
]

RoleName = Literal["owner", "admin", "supervisor", "agent", "viewer"]

# The shared business-website parser accepts bare hosts, normalizes URLs and
# performs semantic checks (dotted host, supported scheme, no credentials).
# JSON Schema's plain string/maxLength constraints cannot express all of this
# without changing which legacy submissions are accepted; describe the rule
# explicitly, with valid positive examples, instead of adding a stricter,
# misleading pattern/format constraint.
WEBSITE_INPUT_DESCRIPTION = (
    "Optional business website: a bare dotted hostname (example.com) or "
    "an http:// or https:// URL (https://shop.example.com/contact). "
    "The server trims surrounding spaces and requires a dotted hostname "
    "without whitespace; embedded credentials and other URL schemes are rejected. "
    "Bare hosts use HTTPS. Accepted values are normalized to "
    "scheme://lowercase-host[:port], discarding path, query and fragment. "
    "Additional hostname, port and normalized-length checks run on the server "
    "and are not fully expressible by this JSON Schema string field."
)
WEBSITE_INPUT_EXAMPLES = ["example.com", "https://shop.example.com:8443/contact"]
WEBSITE_SCHEMA_METADATA = {
    "x-cxops-website-validator": "server-side-http-or-https-dotted-host",
    "x-cxops-normalizes-to": "scheme://lowercase-host[:port]",
}


def _strip_required(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("must not be blank")
    return candidate


class LeadSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=320)
    website: str | None = Field(
        default=None,
        max_length=255,
        description=WEBSITE_INPUT_DESCRIPTION,
        examples=WEBSITE_INPUT_EXAMPLES,
        json_schema_extra=WEBSITE_SCHEMA_METADATA,
    )
    industry: LeadIndustry
    inquiry: str | None = Field(default=None, max_length=2000)
    # Explicit consent to be contacted about this request. ``True`` only.
    consent: Literal[True]
    # Honeypot: hidden from people, irresistible to naive bots. Any value
    # discards the submission silently.
    company_fax: str | None = Field(default=None, max_length=200)

    _strip_names = field_validator("company_name", "contact_name")(_strip_required)

    @field_validator("website")
    @classmethod
    def _website(cls, value: str | None) -> str | None:
        try:
            return normalize_website(value)
        except CommercialValidationError as exc:
            raise ValueError(str(exc)) from exc


class OperatorLeadCreate(BaseModel):
    """A request recorded by an operator (no website consent checkbox)."""

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=320)
    website: str | None = Field(
        default=None,
        max_length=255,
        description=WEBSITE_INPUT_DESCRIPTION,
        examples=WEBSITE_INPUT_EXAMPLES,
        json_schema_extra=WEBSITE_SCHEMA_METADATA,
    )
    industry: LeadIndustry
    inquiry: str | None = Field(default=None, max_length=2000)

    _strip_names = field_validator("company_name", "contact_name")(_strip_required)

    @field_validator("website")
    @classmethod
    def _website(cls, value: str | None) -> str | None:
        try:
            return normalize_website(value)
        except CommercialValidationError as exc:
            raise ValueError(str(exc)) from exc


class LeadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["new", "reviewing", "contacted", "approved", "declined"] | None = None
    operator_notes: str | None = Field(default=None, max_length=4000)


class LeadProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str | None = Field(default=None, max_length=255)


class LifecycleActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "request_changes", "suspend", "reinstate"]


class EntitlementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: str | None = Field(default=None, max_length=40)
    monthly_ai_turn_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    monthly_ai_budget_usd: float | None = Field(default=None, ge=0, le=10_000)


class BusinessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=120)
    website_url: str | None = Field(default=None, max_length=255)


class WidgetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)
    welcome_message: str = Field(min_length=1, max_length=500)
    theme_token: str | None = Field(default=None, max_length=100)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)


class WidgetSetupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=100)
    welcome_message: str | None = Field(default=None, max_length=500)
    theme_token: str | None = Field(default=None, max_length=100)
    allowed_origins: list[str] | None = Field(default=None, max_length=20)


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)
    role: RoleName


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: RoleName


class InvitationTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=10, max_length=200)
