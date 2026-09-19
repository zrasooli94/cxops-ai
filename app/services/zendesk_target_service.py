"""Canonical definition of a valid Zendesk external-execution target.

This module contains no network or database calls and exposes no credentials.
It is the single source of truth for whether a ticket can be addressed through
Zendesk by the durable agent execution worker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.ticket import Ticket


def resolve_zendesk_execution_target(ticket: Ticket | None) -> int | None:
    """Return the numeric Zendesk ticket id if the ticket is a valid target.

    A ticket is eligible for Zendesk execution only when:

    - ``source`` is exactly ``"zendesk"``
    - ``external_id`` is present and non-empty
    - ``external_id`` parses as a positive integer

    Any other source (including ``"api"``, ``"control-center-test"``, or future
    integrations) is rejected. The helper fails closed by returning ``None``.
    """
    if ticket is None:
        return None

    if ticket.source != "zendesk":
        return None

    external_id = ticket.external_id
    if not external_id:
        return None

    try:
        zendesk_ticket_id = int(external_id)
    except (ValueError, TypeError):
        return None

    if zendesk_ticket_id <= 0:
        return None

    return zendesk_ticket_id
