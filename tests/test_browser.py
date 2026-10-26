"""End-to-end checks: the real app in headless Chrome, with Claude and Garmin
faked. Each test seeds the database, then runs one scenario from
tests/browser/smoke.mjs.

Skipped locally if Node or Chrome is missing; in CI (CI=true) that's a failure.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import date
from pathlib import Path

import pytest
import uvicorn

from backend import db
from tests.conftest import PROFILE
from tests.fakes import meal

SMOKE = Path(__file__).resolve().parent / "browser" / "smoke.mjs"


@pytest.fixture(scope="module")
def live_server():
    from backend.main import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=5)


def run_scenario(url: str, name: str) -> str:
    node = shutil.which("node")
    if not node:
        _missing("node is not installed")
    result = subprocess.run([node, str(SMOKE), url, name], capture_output=True, text=True, timeout=90)
    lines = result.stdout.strip().splitlines()
    outcome = json.loads(lines[-1]) if lines else {"ok": False, "detail": result.stderr}
    if result.returncode == 3:
        _missing(outcome["detail"])
    assert outcome["ok"], f"{name}: {outcome['detail']}\npage errors: {outcome.get('pageErrors')}"
    return outcome["detail"]


def _missing(reason: str):
    if os.getenv("CI"):
        pytest.fail(f"browser tests must run in CI: {reason}")
    pytest.skip(reason)


def _seed_profile():
    db.save_profile(PROFILE)


def _seed_meal(day: date, meal_type: str = "lunch"):
    db.save_meal(day.isoformat(), meal_type, meal())


def test_food_log(live_server):
    _seed_profile()
    _seed_meal(date.today())
    run_scenario(live_server, "food_log")


def test_first_run_setup(live_server):
    run_scenario(live_server, "first_run_setup")


def test_describe_correct_save_and_edit(live_server):
    _seed_profile()
    run_scenario(live_server, "describe_correct_save_edit")


def test_summary(live_server):
    _seed_profile()
    _seed_meal(date.fromordinal(date.today().toordinal() - 1))
    run_scenario(live_server, "summary")


def test_summary_when_claude_is_down(live_server, claude):
    _seed_profile()
    claude.fail = RuntimeError("service unavailable")
    run_scenario(live_server, "summary_claude_down")


def test_sleep_and_fitness(live_server, garmin_fake):
    _seed_profile()
    run_scenario(live_server, "sleep_and_fitness")


def test_garmin_not_connected(live_server):
    _seed_profile()
    run_scenario(live_server, "garmin_not_connected")
