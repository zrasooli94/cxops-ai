"""Phase 1U-E: website input documentation must match the production validator.

No database, provider API, email, lead capture or authorization is used here.
JSON Schema documents the precise semantic rule via descriptions/examples,
but does not pretend a regex captures URL parsing and normalization.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.commercial import (
    LeadSubmission,
    OperatorLeadCreate,
    WEBSITE_INPUT_DESCRIPTION,
    WEBSITE_INPUT_EXAMPLES,
    WEBSITE_SCHEMA_METADATA,
)

BASE = {
    "company_name": "Sample Workshop",
    "contact_name": "Sample Contact",
    "email": "sample@example.com",
    "industry": "automotive",
}


@pytest.mark.parametrize("model", [LeadSubmission, OperatorLeadCreate])
def test_openapi_explicitly_documents_domain_rule_and_normalization(model):
    schema = app.openapi()["components"]["schemas"][model.__name__]
    website = schema["properties"]["website"]
    assert website["description"] == WEBSITE_INPUT_DESCRIPTION
    assert website["examples"] == WEBSITE_INPUT_EXAMPLES
    for key, value in WEBSITE_SCHEMA_METADATA.items():
        assert website[key] == value
    assert any(
        option.get("type") == "string" and option.get("maxLength") == 255
        for option in website["anyOf"]
    )
    assert {"type": "null"} in website["anyOf"]
    # Refuse misleading format:uri/https-only or pattern constraints: bare
    # example.com, paths and trimming are accepted by the server.
    assert "pattern" not in website and "format" not in website


def test_public_leads_post_references_the_documented_wire_model():
    ref = app.openapi()["paths"]["/public/leads"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/LeadSubmission")


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        ("example.com", "https://example.com"),
        ("  ExAmPlE.CoM  ", "https://example.com"),
        ("https://Shop.Example.Com:8443/contact?ref=1#fragment", "https://shop.example.com:8443"),
        ("http://shop.example.com/catalog", "http://shop.example.com"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
@pytest.mark.parametrize("model", [LeadSubmission, OperatorLeadCreate])
def test_documented_values_keep_existing_normalization(model, website, expected):
    payload = {**BASE, "website": website}
    if model is LeadSubmission:
        payload["consent"] = True
    assert model.model_validate(payload).website == expected


@pytest.mark.parametrize(
    "website",
    [
        "localhost",
        "notadomain",
        "ftp://example.com",
        "https://user:password@example.com",
        "http://example.com:invalid",
    ],
)
@pytest.mark.parametrize("model", [LeadSubmission, OperatorLeadCreate])
def test_semantically_invalid_website_still_rejected(model, website):
    payload = {**BASE, "website": website}
    if model is LeadSubmission:
        payload["consent"] = True
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_live_schema_examples_are_all_accepted_with_real_parser():
    assert "dotted hostname" in WEBSITE_INPUT_DESCRIPTION
    assert "discarding path" in WEBSITE_INPUT_DESCRIPTION
    for website in WEBSITE_INPUT_EXAMPLES:
        assert LeadSubmission.model_validate(
            {**BASE, "website": website, "consent": True}
        ).website is not None
