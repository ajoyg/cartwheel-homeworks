"""Homework 2 authentication tests. No Langfuse, Docker, or model key needed."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server import app as server_app


def _client() -> TestClient:
    # Not used as a context manager, so the app's startup (tracing setup) does not run.
    server_app._SESSIONS.clear()
    return TestClient(server_app.app)


def test_create_session_rejects_role_that_differs_from_database(world: dict) -> None:
    client = _client()
    response = client.post("/sessions", json={"user_id": 1, "role": "support"})
    assert response.status_code == 403
    assert server_app._SESSIONS == {}


def test_token_for_one_session_cannot_authorize_another(world: dict) -> None:
    client = _client()
    shopper = client.post("/sessions", json={"user_id": 1, "role": "shopper"}).json()
    merchant = client.post("/sessions", json={"user_id": 9002, "role": "merchant"}).json()

    response = client.post(
        f"/sessions/{merchant['session_id']}/messages",
        headers={"Authorization": f"Bearer {shopper['token']}"},
        json={"message": "Show me order 4127."},
    )
    assert response.status_code == 403
