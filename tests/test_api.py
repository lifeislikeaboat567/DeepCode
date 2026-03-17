"""Integration tests for the FastAPI application."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deepcode.api.app import create_app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """Return a test client with the LLM mocked out."""
    import json

    from deepcode.llm.mock_client import MockLLMClient

    mock_answer = json.dumps({
        "thought": "done",
        "action": "final_answer",
        "action_input": {"answer": "Test response"},
    })

    def _mock_factory(*args, **kwargs):
        return MockLLMClient(responses=[mock_answer])

    monkeypatch.setattr("deepcode.api.routes.chat.create_llm_client", _mock_factory)
    monkeypatch.setattr("deepcode.api.routes.tasks.create_llm_client", _mock_factory)

    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient):
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_response_structure(self, client: TestClient):
        data = client.get("/api/v1/health").json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "version" in data
        assert "llm_provider" in data


class TestSessionEndpoints:
    def test_create_session(self, client: TestClient):
        response = client.post("/api/v1/sessions", json={"name": "Test"})
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == "Test"

    def test_list_sessions(self, client: TestClient):
        client.post("/api/v1/sessions", json={"name": "S1"})
        client.post("/api/v1/sessions", json={"name": "S2"})
        response = client.get("/api/v1/sessions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_session(self, client: TestClient):
        created = client.post("/api/v1/sessions", json={"name": "FetchMe"}).json()
        response = client.get(f"/api/v1/sessions/{created['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_get_nonexistent_session_returns_404(self, client: TestClient):
        response = client.get("/api/v1/sessions/does-not-exist")
        assert response.status_code == 404

    def test_delete_session(self, client: TestClient):
        created = client.post("/api/v1/sessions", json={}).json()
        delete_resp = client.delete(f"/api/v1/sessions/{created['id']}")
        assert delete_resp.status_code == 204

        get_resp = client.get(f"/api/v1/sessions/{created['id']}")
        assert get_resp.status_code == 404
