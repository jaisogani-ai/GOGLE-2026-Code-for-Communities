"""Shared fixtures. The API under test never loads synthetic data; tests supply their own inputs."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TATHYON_DEMO_LOGIN", "1")


@pytest.fixture
def api_client(monkeypatch):
    """A fresh in-memory workspace and a helper that returns auth headers for a demo identity.
    External Google/Gemini calls are disabled so tests are deterministic and offline."""
    for key in ("GEMINI_API_KEY", "GOOGLE_MAPS_API_KEY", "GOOGLE_MAPS_BROWSER_KEY", "GOOGLE_MAPS_SERVER_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("TATHYON_DB", "0")
    from fastapi.testclient import TestClient
    from api.main import State, app
    from api.security import limiter
    State.reset()
    limiter._hits.clear()
    client = TestClient(app)

    def login(user: str = "dmo") -> dict:
        tok = client.post("/api/auth/session", json={"user": user}).json()["token"]
        return {"Authorization": f"Bearer {tok}"}

    client.login = login  # type: ignore[attr-defined]
    yield client
    State.reset()
