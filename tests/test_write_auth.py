from fastapi.testclient import TestClient

from app.main import create_app


def test_writes_fail_closed_without_token(monkeypatch):
    monkeypatch.delenv("BIFURCATE_INSECURE_DEV_MODE", raising=False)
    monkeypatch.delenv("BIFURCATE_WRITE_TOKEN", raising=False)
    client = TestClient(create_app())
    assert client.post("/api/v1/agents/register", json={}).status_code == 503


def test_writes_require_token_when_configured(monkeypatch):
    monkeypatch.delenv("BIFURCATE_INSECURE_DEV_MODE", raising=False)
    monkeypatch.setenv("BIFURCATE_WRITE_TOKEN", "test-token")
    client = TestClient(create_app())
    assert client.post("/api/v1/agents/register", json={}).status_code == 401
    assert client.post(
        "/api/v1/agents/register",
        headers={"Authorization": "Bearer test-token"},
        json={},
    ).status_code == 422
