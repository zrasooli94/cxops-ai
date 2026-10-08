"""Test RISPU evaluation case definitions."""
from app.services.rag_evaluation_rispu import rispu_evaluation_cases


def test_rispu_evaluation_cases_defined():
    cases = rispu_evaluation_cases()
    assert len(cases) >= 20
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    for c in cases:
        assert c["id"]
        assert c["question"]


def test_rispu_supported_cases_present():
    cases = rispu_evaluation_cases()
    ids = {c["id"] for c in cases}
    for key in ["rispu-supported-services-overview", "rispu-supported-mobile-apps",
                "rispu-supported-existing-product", "rispu-supported-contact",
                "rispu-supported-project-start"]:
        assert key in ids


def test_rispu_negative_boundary_present():
    cases = rispu_evaluation_cases()
    ids = {c["id"] for c in cases}
    for key in ["rispu-boundary-guarantee-rankings",
                "rispu-boundary-guarantee-ai-citations",
                "rispu-boundary-guarantee-app-store",
                "rispu-boundary-no-phone",
                "rispu-boundary-no-whatsapp",
                "rispu-boundary-no-exact-timing",
                "rispu-boundary-no-automatic-maintenance",
                "rispu-boundary-no-universal-social"]:
        assert key in ids


def test_rispu_unsupported_present():
    cases = rispu_evaluation_cases()
    ids = {c["id"] for c in cases}
    for key in ["rispu-unsupported-office",
                "rispu-unsupported-employees",
                "rispu-unsupported-certifications",
                "rispu-unsupported-response-time",
                "rispu-unsupported-price"]:
        assert key in ids


def test_rispu_transactional_marked():
    cases = rispu_evaluation_cases()
    tx = [c for c in cases if "transactional" in c["id"]]
    assert len(tx) >= 4
    for c in tx:
        assert c.get("should_refuse") is True
        assert c.get("reason") == "no-execution"


def test_rispu_unsupported_marked():
    cases = rispu_evaluation_cases()
    uns = [c for c in cases if "unsupported" in c["id"]]
    assert len(uns) >= 5
    for c in uns:
        assert c.get("should_refuse") is True
        assert c.get("reason") == "insufficient-information"


def test_rispu_no_external_execution():
    cases = rispu_evaluation_cases()
    for c in cases:
        q = c["question"].lower()
        assert "execute" not in q or "cannot" in q
