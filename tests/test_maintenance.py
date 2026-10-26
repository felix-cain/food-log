import subprocess
import sys
from datetime import date
from pathlib import Path

from backend import db, maintenance

ROOT = Path(__file__).resolve().parent.parent
ITEM = {"id": "k", "what": "Key expires", "due": "2026-11-20", "warn_days": 30, "action": "Renew it."}


def test_repo_maintenance_file_is_valid():
    items = maintenance.load()
    assert {"garmin-key", "claude-model", "anthropic-sdk"} <= {i["id"] for i in items}


def test_due_respects_the_warning_window():
    assert maintenance.due(date(2026, 10, 20), [ITEM]) == []  # 31 days out
    [hit] = maintenance.due(date(2026, 10, 21), [ITEM])
    assert hit["days_left"] == 30 and maintenance.describe(hit) == "Key expires: due in 30 days"
    assert maintenance.describe(maintenance.due(date(2026, 11, 22), [ITEM])[0]) == "Key expires: overdue"


def test_check_script_exit_codes(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_maintenance

    monkeypatch.setattr(check_maintenance, "due", lambda today: [])
    assert check_maintenance.main() == 0
    monkeypatch.setattr(check_maintenance, "due", lambda today: [{**ITEM, "days_left": 3}])
    assert check_maintenance.main() == 1
    assert "Key expires: due in 3 days" in capsys.readouterr().out


def test_check_script_runs_standalone():
    # As CI runs it: no app dependencies installed beyond the standard library
    result = subprocess.run([sys.executable, "scripts/check_maintenance.py"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode in (0, 1), result.stderr


def test_notices_list_due_items(client, monkeypatch):
    monkeypatch.setattr(maintenance, "load", lambda: [ITEM])
    assert client.get("/notices", params={"today": "2026-10-01"}).json() == {"notices": []}
    [notice] = client.get("/notices", params={"today": "2026-11-10"}).json()["notices"]
    assert notice == {"text": "Key expires: due in 10 days", "action": "Renew it."}


def test_notices_flag_a_rejected_garmin_key(client, monkeypatch):
    monkeypatch.setattr(maintenance, "load", lambda: [])
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "test-key")
    db.save_sync_state("garmin", error="Garmin rejected the API key. It may have expired.")
    [notice] = client.get("/notices").json()["notices"]
    assert notice["text"].startswith("Garmin rejected the API key")
