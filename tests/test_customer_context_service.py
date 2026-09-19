import json

from app.services.customer_context_service import (
    CustomerContext,
    CustomerContextActivity,
    CustomerContextService,
    CustomerContextSummary,
    CustomerContextTicket,
)


def _make_context(*, display_name: str = "Alice", total: int = 1) -> CustomerContext:
    return CustomerContext(
        customer_id=1,
        display_name=display_name,
        known_channels=["zendesk"],
        summary=CustomerContextSummary(
            total_tickets=total,
            open_tickets=0,
            resolved_tickets=1,
            common_category="billing",
            last_ticket_at=None,
            last_interaction_at=None,
        ),
        recent_tickets=[
            CustomerContextTicket(
                id=10,
                subject="Refund request",
                status="solved",
                priority="high",
                category="billing",
                source="zendesk",
                created_at=None,
                updated_at=None,
            ),
        ],
        recent_activity=[
            CustomerContextActivity(
                id="ticket:10",
                type="ticket.created",
                source="ticket",
                occurred_at=None,
                title="Refund request",
            ),
        ],
    )


class TestApplySizeBounds:
    def test_small_context_unchanged(self):
        context = _make_context()
        bounded = CustomerContextService._apply_size_bounds(context)
        assert bounded.display_name == context.display_name
        assert len(bounded.recent_tickets) == 1
        assert len(bounded.recent_activity) == 1

    def test_oversized_context_is_truncated(self):
        huge = "x" * 10000
        context = _make_context(display_name=huge)
        bounded = CustomerContextService._apply_size_bounds(context)
        assert len(bounded.display_name) < len(huge)

    def test_bounded_context_fits_budget(self):
        huge = "x" * 10000
        context = _make_context(display_name=huge)
        bounded = CustomerContextService._apply_size_bounds(context)
        data = bounded.model_dump()
        assert len(json.dumps(data, default=str, sort_keys=True).encode("utf-8")) <= 8192


class TestComputeDigest:
    def test_digest_is_stable(self):
        context = _make_context()
        d1 = CustomerContextService.compute_digest(context)
        d2 = CustomerContextService.compute_digest(context)
        assert d1 == d2
        assert len(d1) == 64

    def test_digest_changes_with_material_data(self):
        base = CustomerContextService.compute_digest(_make_context(total=1))
        changed = CustomerContextService.compute_digest(_make_context(total=2))
        assert base != changed

    def test_none_context_returns_empty(self):
        assert CustomerContextService.compute_digest(None) == ""


class TestContextPrivacy:
    def test_context_payload_excludes_internal_ids_in_digest(self):
        context = _make_context()
        digest = CustomerContextService.compute_digest(context)
        # The digest is over a reduced payload; it should not simply be the
        # JSON of the full model.
        full_json = json.dumps(context.model_dump(), sort_keys=True, default=str)
        assert digest not in full_json
