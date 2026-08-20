import pytest

from app.services.tool_authorization_service import (
    ToolAuthorizationError,
    ToolAuthorizationService,
)


def test_internal_note_is_low_risk_and_auto_authorized():
    plan = [
        {
            "tool": "zendesk.add_internal_note",
            "arguments": {
                "reason": "Record customer preference",
            },
        }
    ]

    authorized = ToolAuthorizationService.authorize_plan(
        plan
    )

    tool = authorized[0]

    assert tool["risk_level"] == "low"
    assert tool["requires_approval"] is False
    assert tool["authorized"] is True

    assert (
        ToolAuthorizationService.can_auto_execute(
            authorized
        )
        is True
    )


def test_send_reply_requires_human_approval():
    plan = [
        {
            "tool": "zendesk.send_reply",
            "arguments": {
                "body": "Customer response",
            },
        }
    ]

    authorized = ToolAuthorizationService.authorize_plan(
        plan
    )

    tool = authorized[0]

    assert tool["risk_level"] == "high"
    assert tool["requires_approval"] is True
    assert tool["authorized"] is False

    assert (
        ToolAuthorizationService.can_auto_execute(
            authorized
        )
        is False
    )


def test_update_ticket_requires_human_approval():
    plan = [
        {
            "tool": "zendesk.update_ticket",
            "arguments": {
                "priority": "urgent",
            },
        }
    ]

    authorized = ToolAuthorizationService.authorize_plan(
        plan
    )

    tool = authorized[0]

    assert tool["risk_level"] == "medium"
    assert tool["requires_approval"] is True
    assert tool["authorized"] is False


def test_human_review_is_never_auto_executed():
    plan = [
        {
            "tool": "human.review",
            "arguments": {},
        }
    ]

    authorized = ToolAuthorizationService.authorize_plan(
        plan
    )

    assert (
        ToolAuthorizationService.can_auto_execute(
            authorized
        )
        is False
    )


def test_unauthorized_high_risk_plan_cannot_execute():
    plan = [
        {
            "tool": "zendesk.send_reply",
            "arguments": {
                "body": "Customer response",
            },
        }
    ]

    authorized = ToolAuthorizationService.authorize_plan(
        plan
    )

    with pytest.raises(
        ToolAuthorizationError
    ):
        ToolAuthorizationService.assert_executable(
            authorized
        )