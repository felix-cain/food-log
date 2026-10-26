"""Shared test setup.

Every test gets an empty SQLite database, a fake Claude and (on request) a
fake Garmin. Nothing touches the network, Turso, or real data.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Before the app is imported: main.py initialises the database on import,
# and none of the real services may be reachable from tests
os.environ["FOODLOG_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "import.db")
for _var in ("TURSO_URL", "TURSO_AUTH_TOKEN", "APP_TOKEN", "GARMIN_PERSONAL_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_var, None)

import pytest  # noqa: E402

from backend import db, garmin  # noqa: E402
from tests.fakes import FakeAnthropic, FakeGarmin  # noqa: E402

PROFILE = {"age": 30, "sex": "male", "height_cm": 180, "weight_kg": 80, "activity": "active", "goal": "maintain"}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LOCAL_DB_PATH", tmp_path / "test.db")
    db.init_db()


@pytest.fixture(autouse=True)
def claude(monkeypatch) -> FakeAnthropic:
    fake = FakeAnthropic()
    monkeypatch.setattr("anthropic.Anthropic", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture
def garmin_fake(monkeypatch) -> type[FakeGarmin]:
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "test-key")
    monkeypatch.setattr(garmin, "GarminClient", FakeGarmin)
    FakeGarmin.reset()
    return FakeGarmin


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend.main import app

    return TestClient(app)


@pytest.fixture
def with_profile(client) -> dict:
    response = client.put("/profile", json=PROFILE)
    assert response.status_code == 200
    return response.json()
