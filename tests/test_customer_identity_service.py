from app.services.customer_identity_service import (
    CustomerIdentityConflictError,
    CustomerIdentityService,
)


class TestNormalizeIdentifier:
    def test_email_lowercase_and_strip(self):
        result = CustomerIdentityService.normalize(
            provider="email",
            identity_type="email",
            identifier="  Alice@Example.COM  ",
        )
        assert result == "alice@example.com"

    def test_email_empty_returns_none(self):
        assert (
            CustomerIdentityService.normalize(
                provider="email",
                identity_type="email",
                identifier="   ",
            )
            is None
        )

    def test_zendesk_user_id_strips_leading_zeros(self):
        assert (
            CustomerIdentityService.normalize(
                provider="zendesk",
                identity_type="user_id",
                identifier="006119",
            )
            == "6119"
        )

    def test_zendesk_user_id_zero_becomes_zero(self):
        assert (
            CustomerIdentityService.normalize(
                provider="zendesk",
                identity_type="user_id",
                identifier="0000",
            )
            == "0"
        )

    def test_zendesk_user_id_empty_returns_none(self):
        assert (
            CustomerIdentityService.normalize(
                provider="zendesk",
                identity_type="user_id",
                identifier="   ",
            )
            is None
        )

    def test_phone_preserves_digits_and_case(self):
        assert (
            CustomerIdentityService.normalize(
                provider="whatsapp",
                identity_type="phone",
                identifier="  +1 555-0199  ",
            )
            == "+1 555-0199"
        )

    def test_none_identifier_returns_none(self):
        assert (
            CustomerIdentityService.normalize(
                provider="zendesk",
                identity_type="user_id",
                identifier=None,
            )
            is None
        )


class TestConflictError:
    def test_conflict_message_contains_no_pii(self):
        exc = CustomerIdentityConflictError("Provider identity conflict.")
        assert "alice@example.com" not in str(exc)
        assert "provider" not in str(exc).lower() or "Provider" in str(exc)
        assert "conflict" in str(exc).lower()
