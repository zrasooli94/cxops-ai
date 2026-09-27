import hashlib
import json as _json
from copy import deepcopy
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun


class ToolAuthorizationError(Exception):
    pass


def _validate_tool_arguments(
    arguments: dict,
    arg_schema: dict,
) -> None:
    """Validate tool arguments against a JSON-schema-like specification.

    Required fields must be present; forbidden keys cause an error.
    """
    if not isinstance(arguments, dict):
        raise ToolAuthorizationError("arguments must be a dict")

    schema_keys = set(arg_schema.keys())
    arg_keys = set(arguments.keys())

    # Check for unexpected keys (keys in arguments but not in schema)
    unexpected = arg_keys - schema_keys
    if unexpected:
        raise ToolAuthorizationError(
            f"Unexpected argument(s): {', '.join(sorted(unexpected))}"
        )

    # Check required fields
    for key, schema in arg_schema.items():
        if schema.get("required") and key not in arguments:
            raise ToolAuthorizationError(
                f"Missing required argument: {key}"
            )

    # Check non-empty constraints
    for key in arg_keys & schema_keys:
        schema = arg_schema[key]
        if schema.get("non_empty") is not None and not arguments[key]:
            raise ToolAuthorizationError(
                f"Argument '{key}' must be non-empty"
            )


class ToolAuthorizationService:
    TOOL_POLICY_VERSION: ClassVar[int] = 1
    POLICIES: ClassVar[dict[str, dict[str, Any]]] = {
        "none": {
            "risk_level": "low",
            "requires_approval": False,
            "auto_authorize": True,
            "required_capability": None,
            "argument_schema": {},
        },
        "human.review": {
            "risk_level": "low",
            "requires_approval": False,
            "auto_authorize": True,
            "required_capability": None,
            "argument_schema": {},
        },
        "zendesk.add_internal_note": {
            "risk_level": "low",
            "requires_approval": False,
            "auto_authorize": True,
            "required_capability": "ticket.write",
            "argument_schema": {
                "reason": {
                    "required": True,
                    "max_length": 2000,
                    "non_empty": True,
                },
            },
        },
        "zendesk.update_ticket": {
            "risk_level": "medium",
            "requires_approval": True,
            "auto_authorize": False,
            "required_capability": "ticket.write",
            "argument_schema": {
                "team": {
                    "optional": True,
                    "max_length": 100,
                },
                "priority": {
                    "optional": True,
                    "enum": ["low", "normal", "high", "urgent"],
                },
            },
        },
        "zendesk.send_reply": {
            "risk_level": "high",
            "requires_approval": True,
            "auto_authorize": False,
            "required_capability": "ticket.write",
            "argument_schema": {
                "body": {
                    "required": True,
                    "max_length": 10000,
                    "non_empty": True,
                },
            },
        },
    }

    _REGISTRATION_KEYS = frozenset(
        {
            "risk_level",
            "requires_approval",
            "auto_authorize",
            "required_capability",
            "argument_schema",
            "business_tool",
            "provider",
            "description",
        }
    )

    @classmethod
    def register_policy(
        cls,
        tool_name: str,
        policy: dict[str, Any],
    ) -> None:
        """Register (or extend) a tool policy entry (Phase 1P.2).

        Business providers register their tools' policies here at import time so
        the authorization pipeline (authorize_plan / compute_run_digest /
        assert_executable / preflight) governs them identically to the static
        Zendesk tools. The extra keys ``business_tool`` and ``provider`` are
        policy-controlled metadata for target derivation and tenant scoping;
        they are not part of the digest payload (only risk/approval/capability
        and arguments are). Registration is idempotent per tool name but
        refuses to silently change an existing entry's policy-controlled keys.
        """
        existing = cls.POLICIES.get(tool_name)
        if existing is not None:
            for key in ("risk_level", "requires_approval", "required_capability"):
                if existing.get(key) != policy.get(key):
                    raise ValueError(
                        f"Refusing to re-register tool '{tool_name}' with a "
                        f"different {key}."
                    )
            merged = dict(existing)
            merged.update(policy)
            cls.POLICIES[tool_name] = merged
            return
        unexpected = set(policy.keys()) - cls._REGISTRATION_KEYS
        if unexpected:
            raise ValueError(
                f"Policy for '{tool_name}' has unknown keys: {sorted(unexpected)}"
            )
        cls.POLICIES[tool_name] = dict(policy)

    @staticmethod
    def plan_requires_zendesk(tool_plan: list[dict[str, Any]]) -> bool:
        """Whether a tool plan needs an external Zendesk execution target.

        Business tools are executed against local persistence (their results
        are mirrored into the local conversation) and must NOT be blocked by
        the Zendesk-target gate; any other executable tool does require the
        Zendesk ticket. Unknown tools are treated as Zendesk-requiring so the
        existing preflight keeps failing closed for them.
        """
        for raw_tool in tool_plan:
            tool_name = raw_tool.get("tool", "")
            if tool_name in {"none", "human.review"}:
                continue
            policy = ToolAuthorizationService.POLICIES.get(tool_name)
            if policy is None:
                continue  # rejected later by preflight
            if not bool(policy.get("business_tool", False)):
                return True
        return False

    @classmethod
    def business_tool_names(cls) -> set[str]:
        return {
            name
            for name, policy in cls.POLICIES.items()
            if bool(policy.get("business_tool", False))
        }

    @classmethod
    def authorize_plan(
        cls,
        tool_plan: list[dict],
    ) -> list[dict]:

        authorized_plan: list[dict] = []

        for raw_tool in tool_plan:
            tool_call = deepcopy(raw_tool)

            tool_name = tool_call.get("tool", "")

            policy = cls.POLICIES.get(tool_name)

            if policy is None:
                raise ToolAuthorizationError(
                    f"Tool '{tool_name}' is not authorized by policy."
                )

            tool_call["risk_level"] = policy["risk_level"]

            tool_call["requires_approval"] = policy["requires_approval"]

            tool_call["authorized"] = policy["auto_authorize"]

            tool_call["required_capability"] = policy["required_capability"]

            authorized_plan.append(tool_call)

        return authorized_plan

    @staticmethod
    def requires_human_approval(
        tool_plan: list[dict],
    ) -> bool:

        return any(
            (
                tool.get(
                    "requires_approval",
                    True,
                )
                and not tool.get(
                    "authorized",
                    False,
                )
            )
            for tool in tool_plan
        )

    @staticmethod
    def assert_executable(
        tool_plan: list[dict],
    ) -> None:

        unauthorized = [
            tool.get("tool")
            for tool in tool_plan
            if (
                tool.get(
                    "requires_approval",
                    True,
                )
                and not tool.get(
                    "authorized",
                    False,
                )
            )
        ]

        if unauthorized:
            names = ", ".join(str(name) for name in unauthorized)

            raise ToolAuthorizationError(
                f"Tool plan contains unauthorized tools: {names}"
            )

    @staticmethod
    def can_auto_execute(
        tool_plan: list[dict],
    ) -> bool:

        executable_tools = [
            tool
            for tool in tool_plan
            if tool.get("tool")
            not in {
                "none",
                "human.review",
            }
        ]

        if not executable_tools:
            return False

        return all(
            (
                tool.get(
                    "authorized",
                    False,
                )
                and not tool.get(
                    "requires_approval",
                    True,
                )
            )
            for tool in executable_tools
        )

    @staticmethod
    def compute_run_digest(
        *,
        run_id: str,
        organization_id: int,
        ticket_id: int,
        policy_version: int,
        tool_plan: list[dict[str, Any]],
    ) -> str:
        """Compute canonical authorization digest for a run.

        Validates each tool against current POLICIES but preserves
        legitimately persisted ``authorized`` state (e.g. from human
        approval).  Only normalizes fields that policy controls.
        ``policy_version`` MUST be the run's persisted ``tool_policy_version``.
        """
        validated: list[dict[str, Any]] = []
        for raw_tool in tool_plan:
            tool_call = {k: v for k, v in raw_tool.items()}

            tool_name = tool_call.get("tool", "")
            policy = ToolAuthorizationService.POLICIES.get(tool_name)
            if policy is None:
                raise ToolAuthorizationError(
                    f"Tool '{tool_name}' is not authorized by policy."
                )

            # Policy-controlled fields always overwrite untrusted values,
            # but preserve legitimately persisted ``authorized`` state.
            tool_call["risk_level"] = policy["risk_level"]
            tool_call["requires_approval"] = policy["requires_approval"]
            tool_call["required_capability"] = policy["required_capability"]

            # Validate arguments against strict schema
            arg_schema = policy.get("argument_schema", {})
            if arg_schema:
                _validate_tool_arguments(tool_call.get("arguments", {}), arg_schema)

            # Preserve the persisted ``authorized`` value; only enforce
            # policy consistency for low-risk auto-authorized tools.
            if tool_call["risk_level"] == "low":
                tool_call["authorized"] = policy["auto_authorize"]
            # For medium/high-risk tools, keep the persisted ``authorized``
            # as-is (e.g. true from a prior human approval).

            validated.append(tool_call)

        payload = {
            "run_id": run_id,
            "organization_id": organization_id,
            "ticket_id": ticket_id,
            "policy_version": policy_version,
            "tool_plan": validated,
        }
        return hashlib.sha256(
            _json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def validate_run_digest(
        run: "AgentRun",
    ) -> bool:
        """Validate that a run's authorization_digest matches the canonical digest.

        Fails closed when the run has no persisted ``tool_policy_version`` or
        organization: a missing or stale policy version is never trusted.
        """

        if run.tool_policy_version is None:
            return False
        if run.organization_id is None:
            return False

        expected = ToolAuthorizationService.compute_run_digest(
            run_id=run.run_id,
            organization_id=run.organization_id,
            ticket_id=run.ticket_id,
            policy_version=run.tool_policy_version,
            tool_plan=run.tool_plan or [],
        )
        return run.authorization_digest == expected


tool_authorization_service = ToolAuthorizationService()
