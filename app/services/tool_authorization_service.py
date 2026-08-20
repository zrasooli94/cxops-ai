from copy import deepcopy
from typing import ClassVar


class ToolAuthorizationError(Exception):
    pass


class ToolAuthorizationService:
    POLICIES: ClassVar[dict[str, dict[str, object]]] = {
        "none": {
            "risk_level": "low",
            "requires_approval": False,
            "auto_authorize": True,
        },
        "human.review": {
            "risk_level": "low",
            "requires_approval": False,
            "auto_authorize": True,
        },
        "zendesk.add_internal_note": {
            "risk_level": "low",
            "requires_approval": False,
            "auto_authorize": True,
        },
        "zendesk.update_ticket": {
            "risk_level": "medium",
            "requires_approval": True,
            "auto_authorize": False,
        },
        "zendesk.send_reply": {
            "risk_level": "high",
            "requires_approval": True,
            "auto_authorize": False,
        },
    }

    @classmethod
    def authorize_plan(
        cls,
        tool_plan: list[dict],
    ) -> list[dict]:

        authorized_plan: list[dict] = []

        for raw_tool in tool_plan:
            tool_call = deepcopy(raw_tool)

            tool_name = tool_call.get("tool")

            policy = cls.POLICIES.get(tool_name)

            if policy is None:
                raise ToolAuthorizationError(
                    f"Tool '{tool_name}' is not authorized by policy."
                )

            tool_call["risk_level"] = policy["risk_level"]

            tool_call["requires_approval"] = policy["requires_approval"]

            tool_call["authorized"] = policy["auto_authorize"]

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


tool_authorization_service = ToolAuthorizationService()
