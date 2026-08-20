from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert isinstance(
        response.json(),
        dict,
    )


def test_openapi_available():
    response = client.get(
        "/openapi.json"
    )

    assert response.status_code == 200

    body = response.json()

    assert "paths" in body
    assert len(body["paths"]) > 0


def test_metrics_endpoint_available():
    response = client.get(
        "/metrics"
    )

    assert response.status_code == 200

    assert (
        "cxops_agent_decisions_total"
        in response.text
    )