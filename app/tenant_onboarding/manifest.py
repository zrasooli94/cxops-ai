"""Strict, secret-free tenant manifest loading (Phase 1P.3).

The manifest is the single declarative input to onboarding. It is validated
eagerly and completely so that an operator sees every problem in one pass
instead of discovering it halfway through an apply.

Two classes of rejection are intentional and non-negotiable:

``UnknownFieldError``
    The manifest is a control-plane surface. A typo (``allowed_origin``) or a
    stray block must not be silently ignored, because "it looked like it worked"
    is exactly how a tenant ends up with an empty origin allowlist in production.

``SecretMaterialError``
    Manifests are committed to git. Anything that looks like a credential is
    refused at load time. This is a denylist over both key names and value
    shapes, and it is intentionally conservative: a false positive costs a
    rename, a false negative leaks a live credential into version control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

# A tenant slug is the value stored in ``organizations.external_id`` and is used
# in URLs, log lines, and deployment commands. Keep it boring.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SLUG_LENGTH = 100
MAX_NAME_LENGTH = 255
MAX_DISPLAY_NAME_LENGTH = 100
MAX_WELCOME_MESSAGE_LENGTH = 500
MAX_THEME_TOKEN_LENGTH = 100
MAX_ORIGINS = 20
MAX_KNOWLEDGE_DOCUMENTS = 50
MAX_KNOWLEDGE_CONTENT_CHARS = 20_000
MAX_BUSINESS_INTEGRATIONS = 10

# Keys that must never appear anywhere in a manifest, at any nesting depth.
# Fragments are matched against the whole normalized key, so they cannot fire on
# a substring of an unrelated word.
_FORBIDDEN_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "authorization",
    "auth_header",
    "connection_string",
    "database_url",
    "encryption_key",
    "jwt_secret",
    "webhook_secret",
    "client_secret",
    "signing_key",
)

# Short or ambiguous words are matched as whole keys only, to avoid firing on
# innocent names (a key called ``bearer_mode`` is not a bearer credential).
_FORBIDDEN_EXACT_KEYS = frozenset(
    {
        "secret",
        "token",
        "tokens",
        "dsn",
        "bearer",
        "auth",
        "passphrase",
        "session_token",
        "public_widget_key",
        "widget_key",
        "public_chat_key",
        "access_token",
        "refresh_token",
        "id_token",
        "api_token",
    }
)

# Keys that legitimately contain a forbidden fragment but are not credentials.
# ``theme_token`` is the only one in the manifest schema: an opaque branding
# label rendered from a closed set of known mappings, which is exactly the
# opposite of a secret.
_SAFE_KEY_OVERRIDES = frozenset({"theme_token"})

# Value shapes that are credentials regardless of the key they sit under.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^pk_live_[0-9a-f]{16,}$"),
    re.compile(r"^sk-[A-Za-z0-9_\-]{16,}$"),
    re.compile(r"^gitleaks:"),
    re.compile(r"^-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"^eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$"),
    re.compile(r"^(postgres|postgresql|mysql|redis|mongodb)(\+[a-z0-9]+)?://", re.IGNORECASE),
    re.compile(r"^(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^xox[baprs]-[A-Za-z0-9\-]{10,}$"),
    re.compile(r"^xapp-[0-9]-[A-Za-z0-9\-]{10,}$"),
    # A pasted Authorization header, in any of the shapes people actually use.
    re.compile(r"^bearer\s+\S{8,}$", re.IGNORECASE),
    re.compile(r"^basic\s+[A-Za-z0-9+/=]{8,}$", re.IGNORECASE),
    re.compile(r"^authorization\s*[:=]", re.IGNORECASE),
)

# Loopback hosts are the only place a non-HTTPS embedding origin is tolerated.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PROVIDER_MODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SOURCE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Pilot posture markers. Their meaning to a reviewer, from inert to live:
#
#   disabled    no widget, no providers - a template or an off tenant
#   staging     internal only, not customer-reachable
#   local_demo  customer-reachable, but every business action is simulated
#   pilot       customer-reachable live pilot whose business actions are still
#               simulated. Distinct from local_demo in intent only: local_demo
#               reads as "this is a demo", pilot reads as "this is a real
#               customer trial". Neither permits a real provider.
#   production  a real provider is wired and performs real actions
#
# "pilot" exists because a live trial with simulated business actions is neither
# a demo nor production, and calling it either misleads an operator. The
# distinction matters for review, not for runtime: _NON_PRODUCTION_STATES below
# is what the enforcement actually keys off, so both labels get the same
# protection.
_PILOT_STATES = frozenset({"disabled", "local_demo", "staging", "pilot", "production"})

# States that may NOT carry a non-local_demo provider_mode.
_NON_PRODUCTION_STATES = frozenset({"local_demo", "pilot", "staging", "disabled"})

_KNOWN_PROVIDER_MODES = frozenset({"local_demo"})

# "production" asserts that a real provider performs real actions. The reverse
# pairing -- claiming production while every business action is still simulated
# -- is the exact failure this build must make impossible, so it is rejected
# rather than left to reviewer vigilance. Today no live provider mode exists, so
# "production" is not declarable at all: that is the honest state of the system,
# and the loader says so instead of accepting a label it cannot honour.
_PRODUCTION_STATE = "production"


class ManifestError(ValueError):
    """A manifest was rejected.

    ``problems`` carries every issue found so an operator can fix the file in a
    single edit rather than one error per run.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class UnknownFieldError(ManifestError):
    pass


class SecretMaterialError(ManifestError):
    pass


class _Validator:
    """Collects problems so one load reports every defect, not just the first."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.problems.append(f"{path}: {message}")

    def error(self) -> None:
        if self.problems:
            raise ManifestError(self.problems)


@dataclass(frozen=True)
class PublicChatManifest:
    enabled: bool
    display_name: str
    welcome_message: str
    allowed_origins: tuple[str, ...]
    theme_token: str | None
    max_message_length: int
    max_messages_per_minute: int
    session_ttl_hours: int


@dataclass(frozen=True)
class BusinessIntegrationManifest:
    provider: str
    enabled: bool
    provider_mode: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeDocumentManifest:
    source_id: str
    version: int
    title: str
    content: str
    department: str | None = None
    source_uri: str | None = None

    def metadata(self) -> dict[str, Any]:
        """Deterministic document metadata.

        ``onboarding_source_id`` + ``onboarding_version`` is the stable identity
        used to reason about re-onboarding: the same source id and version with
        identical content is a no-op, and a new version is a new document. The
        checksum that ``KnowledgeIngestionService`` stores is the authoritative
        duplicate guard; this metadata is what makes a human-readable audit
        trail possible.
        """
        metadata: dict[str, Any] = {
            "onboarding_source_id": self.source_id,
            "onboarding_version": self.version,
            "onboarding_managed": True,
        }
        if self.department:
            metadata["department"] = self.department
        return metadata

    def source(self) -> str:
        return f"tenant-onboarding:{self.source_id}"


@dataclass(frozen=True)
class PilotManifest:
    state: str
    notes: str | None = None


@dataclass(frozen=True)
class TenantManifest:
    schema_version: int
    slug: str
    name: str
    industry: str | None
    public_chat: PublicChatManifest | None
    business_integrations: tuple[BusinessIntegrationManifest, ...]
    knowledge: tuple[KnowledgeDocumentManifest, ...]
    pilot: PilotManifest | None
    origin_path: str | None = None

    @property
    def is_riskpu_template(self) -> bool:
        """A generic example tenant carries no A1 surface by construction."""
        return self.slug.startswith("rispu")


def _reject_secret_material(node: Any, path: str, problems: list[str]) -> None:
    """Walk the raw document and refuse credential-shaped keys and values."""
    if isinstance(node, dict):
        for raw_key, value in node.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            normalized = key.strip().lower().replace("-", "_")
            matched = _forbidden_key_reason(normalized)
            if matched is not None:
                problems.append(
                    f"{child_path}: manifest keys must never carry credential "
                    f"material (matched {matched!r}); remove this field and "
                    f"supply it through the environment instead"
                )
            _reject_secret_material(value, child_path, problems)
        return

    if isinstance(node, list):
        for index, value in enumerate(node):
            _reject_secret_material(value, f"{path}[{index}]", problems)
        return

    if isinstance(node, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(node.strip()):
                problems.append(
                    f"{path}: value looks like credential material and must not be "
                    f"stored in a manifest; use the environment or a secret manager"
                )
                return


def _forbidden_key_reason(normalized_key: str) -> str | None:
    """Return why ``normalized_key`` is a credential field, or ``None``."""
    if normalized_key in _SAFE_KEY_OVERRIDES:
        return None
    if normalized_key in _FORBIDDEN_EXACT_KEYS:
        return normalized_key
    for fragment in _FORBIDDEN_KEY_FRAGMENTS:
        if fragment in normalized_key:
            return fragment
    return None


def _expect_mapping(value: Any, path: str, validator: _Validator) -> dict[str, Any]:
    if not isinstance(value, dict):
        validator.add(path, "must be a mapping")
        return {}
    return {str(key): item for key, item in value.items()}


def _expect_optional_mapping(
    value: Any,
    path: str,
    validator: _Validator,
) -> dict[str, Any] | None:
    """Like :func:`_expect_mapping` but treats an absent section as omitted.

    An omitted optional block means "this manifest does not manage that
    surface", which is materially different from a malformed block.
    """
    if value is None:
        return None
    return _expect_mapping(value, path, validator)


def _reject_unknown(
    mapping: dict[str, Any],
    allowed: frozenset[str],
    path: str,
    validator: _Validator,
) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    for key in unknown:
        validator.add(
            f"{path}.{key}",
            "unknown field; allowed fields are "
            + ", ".join(sorted(allowed)),
        )


def _required_str(
    mapping: dict[str, Any],
    key: str,
    path: str,
    validator: _Validator,
    *,
    max_length: int,
) -> str | None:
    raw = mapping.get(key)
    if raw is None:
        validator.add(f"{path}.{key}", "is required")
        return None
    if not isinstance(raw, str):
        validator.add(f"{path}.{key}", "must be a string")
        return None
    value = raw.strip()
    if not value:
        validator.add(f"{path}.{key}", "must not be empty")
        return None
    if len(value) > max_length:
        validator.add(
            f"{path}.{key}",
            f"must be at most {max_length} characters (got {len(value)})",
        )
        return None
    return value


def _optional_str(
    mapping: dict[str, Any],
    key: str,
    path: str,
    validator: _Validator,
    *,
    max_length: int,
) -> str | None:
    if key not in mapping or mapping[key] is None:
        return None
    return _required_str(
        mapping,
        key,
        path,
        validator,
        max_length=max_length,
    )


def _bounded_int(
    mapping: dict[str, Any],
    key: str,
    path: str,
    validator: _Validator,
    *,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    raw = mapping.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        validator.add(f"{path}.{key}", f"must be an integer between {minimum} and {maximum}")
        return default
    if not minimum <= raw <= maximum:
        validator.add(f"{path}.{key}", f"must be between {minimum} and {maximum} (got {raw})")
        return default
    return raw


def _bool(
    mapping: dict[str, Any],
    key: str,
    path: str,
    validator: _Validator,
    *,
    default: bool | None = None,
) -> bool | None:
    if key not in mapping:
        return default
    raw = mapping[key]
    if not isinstance(raw, bool):
        validator.add(f"{path}.{key}", "must be true or false")
        return default
    return raw


def _validate_origin(origin: str, path: str, validator: _Validator) -> None:
    if "*" in origin:
        validator.add(path, "wildcard origins are not supported; list every exact origin")
        return
    if "://" not in origin:
        validator.add(path, "must be an absolute origin such as https://www.example.com")
        return
    parts = urlsplit(origin)
    if parts.path not in ("", "/"):
        validator.add(path, "must not include a path")
        return
    if parts.query or parts.fragment:
        validator.add(path, "must not include a query string or fragment")
        return
    if parts.username or parts.password:
        validator.add(path, "must not include credentials")
        return
    if not parts.hostname:
        validator.add(path, "must include a host")
        return
    if origin.endswith("/"):
        validator.add(path, "must not include a trailing slash")
        return
    if parts.scheme != "https" and (parts.hostname or "").lower() not in _LOOPBACK_HOSTS:
        validator.add(
            path,
            "must use https; http is accepted only for loopback hosts during local development",
        )


def _parse_public_chat(
    raw: Any,
    path: str,
    validator: _Validator,
) -> PublicChatManifest | None:
    mapping = _expect_optional_mapping(raw, path, validator)
    if mapping is None:
        return None
    if not mapping:
        return None
    _reject_unknown(
        mapping,
        frozenset(
            {
                "enabled",
                "display_name",
                "welcome_message",
                "allowed_origins",
                "theme_token",
                "max_message_length",
                "max_messages_per_minute",
                "session_ttl_hours",
            }
        ),
        path,
        validator,
    )

    enabled = _bool(mapping, "enabled", path, validator, default=True)
    display_name = _required_str(
        mapping,
        "display_name",
        path,
        validator,
        max_length=MAX_DISPLAY_NAME_LENGTH,
    )
    welcome_message = _required_str(
        mapping,
        "welcome_message",
        path,
        validator,
        max_length=MAX_WELCOME_MESSAGE_LENGTH,
    )

    origins_raw = mapping.get("allowed_origins", [])
    allowed_origins: list[str] = []
    if not isinstance(origins_raw, list):
        validator.add(f"{path}.allowed_origins", "must be a list of exact origins")
    else:
        if len(origins_raw) > MAX_ORIGINS:
            validator.add(
                f"{path}.allowed_origins",
                f"must contain at most {MAX_ORIGINS} origins",
            )
        for index, origin in enumerate(origins_raw):
            origin_path = f"{path}.allowed_origins[{index}]"
            if not isinstance(origin, str):
                validator.add(origin_path, "must be a string")
                continue
            candidate = origin.strip()
            if not candidate:
                validator.add(origin_path, "must not be empty")
                continue
            if candidate in allowed_origins:
                validator.add(origin_path, "is a duplicate; list each origin once")
                continue
            _validate_origin(candidate, origin_path, validator)
            allowed_origins.append(candidate)

    if enabled and not allowed_origins:
        # An enabled widget with an empty allowlist can never serve a session:
        # the origin check runs before a session is created. Rejecting it here
        # means a pilot never goes live as silently non-functional.
        validator.add(
            f"{path}.allowed_origins",
            "an enabled widget must list at least one exact https origin; "
            "every embedding origin is checked server-side before a session starts",
        )

    return PublicChatManifest(
        enabled=bool(enabled),
        display_name=display_name or "",
        welcome_message=welcome_message or "",
        allowed_origins=tuple(allowed_origins),
        theme_token=_optional_str(
            mapping,
            "theme_token",
            path,
            validator,
            max_length=MAX_THEME_TOKEN_LENGTH,
        ),
        max_message_length=_bounded_int(
            mapping,
            "max_message_length",
            path,
            validator,
            minimum=1,
            maximum=10_000,
            default=2000,
        ),
        max_messages_per_minute=_bounded_int(
            mapping,
            "max_messages_per_minute",
            path,
            validator,
            minimum=1,
            maximum=300,
            default=12,
        ),
        session_ttl_hours=_bounded_int(
            mapping,
            "session_ttl_hours",
            path,
            validator,
            minimum=1,
            maximum=168,
            default=24,
        ),
    )


def _parse_business_integrations(
    raw: Any,
    path: str,
    validator: _Validator,
) -> tuple[BusinessIntegrationManifest, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        validator.add(path, "must be a list")
        return ()
    if len(raw) > MAX_BUSINESS_INTEGRATIONS:
        validator.add(path, f"must contain at most {MAX_BUSINESS_INTEGRATIONS} entries")
        return ()

    parsed: list[BusinessIntegrationManifest] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        mapping = _expect_mapping(item, item_path, validator)
        if not mapping:
            continue
        _reject_unknown(
            mapping,
            frozenset({"provider", "enabled", "provider_mode", "config"}),
            item_path,
            validator,
        )
        provider = _required_str(
            mapping,
            "provider",
            item_path,
            validator,
            max_length=64,
        )
        if provider is None:
            continue
        if not _PROVIDER_ID_RE.match(provider):
            validator.add(
                f"{item_path}.provider",
                "must be a lowercase snake_case provider id",
            )
            continue
        if provider in seen:
            validator.add(f"{item_path}.provider", f"duplicate provider {provider!r}")
            continue
        seen.add(provider)

        provider_mode = _required_str(
            mapping,
            "provider_mode",
            item_path,
            validator,
            max_length=64,
        ) or ""
        if provider_mode and provider_mode not in _KNOWN_PROVIDER_MODES:
            validator.add(
                f"{item_path}.provider_mode",
                "unknown provider_mode "
                f"{provider_mode!r}; this build ships only "
                + ", ".join(sorted(_KNOWN_PROVIDER_MODES)),
            )

        config_raw = mapping.get("config", {})
        config: dict[str, Any] = {}
        if isinstance(config_raw, dict):
            config = {str(key): value for key, value in config_raw.items()}
        else:
            validator.add(f"{item_path}.config", "must be a mapping")

        parsed.append(
            BusinessIntegrationManifest(
                provider=provider,
                enabled=bool(_bool(mapping, "enabled", item_path, validator, default=True)),
                provider_mode=provider_mode,
                config=config,
            )
        )
    return tuple(parsed)


def _parse_knowledge(
    raw: Any,
    path: str,
    validator: _Validator,
) -> tuple[KnowledgeDocumentManifest, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        validator.add(path, "must be a list")
        return ()
    if len(raw) > MAX_KNOWLEDGE_DOCUMENTS:
        validator.add(path, f"must contain at most {MAX_KNOWLEDGE_DOCUMENTS} documents")
        return ()

    parsed: list[KnowledgeDocumentManifest] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        mapping = _expect_mapping(item, item_path, validator)
        if not mapping:
            continue
        _reject_unknown(
            mapping,
            frozenset({"source_id", "version", "title", "content", "department", "source_uri"}),
            item_path,
            validator,
        )
        source_id = _required_str(
            mapping,
            "source_id",
            item_path,
            validator,
            max_length=100,
        )
        if source_id is None:
            continue
        if not _SOURCE_ID_RE.match(source_id):
            validator.add(
                f"{item_path}.source_id",
                "must be lowercase kebab-case so it is stable in logs and metadata",
            )
            continue
        if source_id in seen:
            validator.add(f"{item_path}.source_id", f"duplicate source_id {source_id!r}")
            continue
        seen.add(source_id)

        version_raw = mapping.get("version", 1)
        if isinstance(version_raw, bool) or not isinstance(version_raw, int) or version_raw < 1:
            validator.add(f"{item_path}.version", "must be a positive integer")
            version = 1
        else:
            version = version_raw

        title = _required_str(mapping, "title", item_path, validator, max_length=300)
        content = _required_str(mapping, "content", item_path, validator, max_length=MAX_KNOWLEDGE_CONTENT_CHARS)
        if title is None or content is None:
            continue

        parsed.append(
            KnowledgeDocumentManifest(
                source_id=source_id,
                version=version,
                title=title,
                content=content,
                department=_optional_str(
                    mapping,
                    "department",
                    item_path,
                    validator,
                    max_length=120,
                ),
                source_uri=_optional_str(
                    mapping,
                    "source_uri",
                    item_path,
                    validator,
                    max_length=500,
                ),
            )
        )
    return tuple(parsed)


def _parse_pilot(raw: Any, path: str, validator: _Validator) -> PilotManifest | None:
    mapping = _expect_optional_mapping(raw, path, validator)
    if mapping is None:
        return None
    if not mapping:
        return None
    _reject_unknown(mapping, frozenset({"state", "notes"}), path, validator)
    state = _required_str(mapping, "state", path, validator, max_length=32)
    if state is not None and state not in _PILOT_STATES:
        validator.add(
            f"{path}.state",
            f"must be one of {', '.join(sorted(_PILOT_STATES))} (got {state!r})",
        )
        state = None
    if state is None:
        return None
    return PilotManifest(
        state=state,
        notes=_optional_str(mapping, "notes", path, validator, max_length=1000),
    )


def parse_manifest(
    raw: Any,
    *,
    origin_path: str | None = None,
) -> TenantManifest:
    """Validate a decoded manifest document into a ``TenantManifest``.

    Raises ``SecretMaterialError`` for credential-shaped content and
    ``ManifestError`` (via ``UnknownFieldError`` for unknown keys) otherwise.
    """
    secret_problems: list[str] = []
    _reject_secret_material(raw, "manifest", secret_problems)
    if secret_problems:
        raise SecretMaterialError(secret_problems)

    validator = _Validator()
    if not isinstance(raw, dict):
        raise ManifestError(["manifest: must be a mapping at the top level"])

    document = {str(key): value for key, value in raw.items()}
    _reject_unknown(
        document,
        frozenset(
            {
                "schema_version",
                "tenant",
                "public_chat",
                "business_integrations",
                "knowledge",
                "pilot",
            }
        ),
        "manifest",
        validator,
    )

    schema_version = document.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        validator.add("manifest.schema_version", "is required and must be an integer")
        schema_version = 0
    elif schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        validator.add(
            "manifest.schema_version",
            f"unsupported schema_version {schema_version}; supported: "
            + ", ".join(str(v) for v in sorted(SUPPORTED_SCHEMA_VERSIONS)),
        )

    tenant = _expect_mapping(document.get("tenant"), "manifest.tenant", validator)
    _reject_unknown(tenant, frozenset({"slug", "name", "industry"}), "manifest.tenant", validator)
    slug = _required_str(tenant, "slug", "manifest.tenant", validator, max_length=MAX_SLUG_LENGTH)
    if slug is not None and not _SLUG_RE.match(slug):
        validator.add(
            "manifest.tenant.slug",
            "must be lowercase kebab-case (a-z, 0-9, single hyphens)",
        )
        slug = None
    name = _required_str(tenant, "name", "manifest.tenant", validator, max_length=MAX_NAME_LENGTH)
    industry = _optional_str(tenant, "industry", "manifest.tenant", validator, max_length=120)

    public_chat = _parse_public_chat(
        document.get("public_chat"),
        "manifest.public_chat",
        validator,
    )
    business_integrations = _parse_business_integrations(
        document.get("business_integrations"),
        "manifest.business_integrations",
        validator,
    )
    knowledge = _parse_knowledge(document.get("knowledge"), "manifest.knowledge", validator)
    pilot = _parse_pilot(document.get("pilot"), "manifest.pilot", validator)

    if pilot is not None and pilot.state in _NON_PRODUCTION_STATES:
        for integration in business_integrations:
            if integration.provider_mode != "local_demo":
                validator.add(
                    "manifest.pilot.state",
                    f"declares {pilot.state!r} (a non-production posture) but "
                    f"business_integrations[{integration.provider}]"
                    f".provider_mode is {integration.provider_mode!r}; "
                    "simulated business actions cannot be declared alongside a "
                    "posture that implies real ones",
                )

    if pilot is not None and pilot.state == _PRODUCTION_STATE:
        simulated = [
            integration.provider
            for integration in business_integrations
            if integration.provider_mode == "local_demo"
        ]
        if simulated:
            validator.add(
                "manifest.pilot.state",
                f"declares {_PRODUCTION_STATE!r}, which asserts a real provider "
                f"performing real actions, but "
                + ", ".join(f"business_integrations[{p}]" for p in sorted(simulated))
                + ".provider_mode is 'local_demo'; labelling simulated operations "
                "as production is the one mistake this loader must not permit. "
                "Use pilot.state: pilot until a real provider mode ships.",
            )

    validator.error()

    assert slug is not None and name is not None  # narrowed by validator.error()

    return TenantManifest(
        schema_version=schema_version,
        slug=slug,
        name=name,
        industry=industry,
        public_chat=public_chat,
        business_integrations=business_integrations,
        knowledge=knowledge,
        pilot=pilot,
        origin_path=origin_path,
    )


def load_manifest_file(path: str | Path) -> TenantManifest:
    """Read and validate a manifest from disk.

    Uses ``yaml.safe_load`` so a manifest can never construct arbitrary Python
    objects, and caps the file size so a mistakenly committed dump cannot be
    parsed into memory.
    """
    manifest_path = Path(path)
    max_bytes = 512 * 1024
    if not manifest_path.is_file():
        raise ManifestError([f"manifest: file not found: {manifest_path}"])
    size = manifest_path.stat().st_size
    if size > max_bytes:
        raise ManifestError(
            [f"manifest: {manifest_path} is {size} bytes; limit is {max_bytes}"]
        )

    text = manifest_path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError([f"manifest: {manifest_path} is not valid YAML: {exc}"]) from exc

    return parse_manifest(raw, origin_path=str(manifest_path))
