from datetime import date, timedelta

import pytest

from backend import garmin, health
from backend.db import upsert_daily_activity

TODAY = date(2026, 10, 26)
NOW = "2026-10-26T00:00:00+00:00"


# --- parsing ----------------------------------------------------------------


def test_empty_sleep_record_is_skipped():
    # Shape of a real dailySleep response for a night without data
    empty = {"id": None, "calendarDate": None, "sleepTimeSeconds": None, "deviceRemCapable": True, "retro": False}
    assert health.parse_sleep(empty, "2026-10-25", NOW) is None
    assert health.parse_sleep(None, "2026-10-25", NOW) is None


def test_sleep_record_is_parsed_flat_or_wrapped():
    record = {"calendarDate": "2026-10-25", "sleepTimeSeconds": 25200, "deepSleepSeconds": 5400,
              "sleepScores": {"overall": {"value": 81}}, "sleepStartTimestampLocal": 1761433200000}
    for raw in (record, {"dailySleepDTO": record, "avgOvernightHrv": 48, "restingHeartRate": 50}):
        row = health.parse_sleep(raw, "2026-10-25", NOW)
        assert row["night_date"] == "2026-10-25" and row["sleep_seconds"] == 25200
        assert row["sleep_score"] == 81 and row["bedtime_local"].endswith("T23:00")
    assert row["hrv_ms"] == 48 and row["resting_hr"] == 50


def test_daily_summary_accepts_alternative_field_names():
    row = health.parse_daily({"date": "2026-10-25", "steps": 5000, "activeCalories": 300}, NOW)
    assert (row["local_date"], row["steps"], row["active_kcal"]) == ("2026-10-25", 5000, 300)
    assert health.parse_daily({"totalSteps": 1}, NOW) is None


def test_activity_parsing():
    row = health.parse_activity({"activityId": 7, "startTimeLocal": "2026-10-25 07:30:00", "duration": 1800,
                                 "activityTypeDTO": {"typeKey": "cycling"}}, NOW)
    assert row["source_id"] == "7" and row["local_date"] == "2026-10-25" and row["start_local"] == "2026-10-25T07:30"
    assert row["activity_type"] == "cycling"
    assert health.parse_activity({"activityId": 8}, NOW) is None


# --- sync -------------------------------------------------------------------


def test_sync_without_a_key_does_nothing(client):
    assert health.sync_garmin(TODAY) == {"status": "not_configured"}
    assert client.post("/sync").json()["configured"] is False


def test_first_sync_pulls_30_days_in_chunks(garmin_fake):
    result = health.sync_garmin(TODAY)
    assert result["status"] == "ok" and result["days"] == 31
    sleep_calls = [c for c in garmin_fake.calls if c[0] == "sleep"]
    assert len(sleep_calls) == 31 and sleep_calls[0][1] == "2026-09-26"
    ranges = [c[1:] for c in garmin_fake.calls if c[0] == "daily"]
    assert ranges == [("2026-09-26", "2026-10-23"), ("2026-10-24", "2026-10-26")]


def test_recent_sync_is_skipped_and_forced_sync_only_refetches_the_overlap(garmin_fake):
    health.sync_garmin(TODAY)
    garmin_fake.calls.clear()
    assert health.sync_garmin(TODAY)["status"] == "fresh"
    assert garmin_fake.calls == []
    health.sync_garmin(TODAY, force=True)
    assert len([c for c in garmin_fake.calls if c[0] == "sleep"]) == 4  # 3-day overlap + today


def test_sync_merges_hrv_and_fills_resting_hr(garmin_fake):
    health.sync_garmin(TODAY)
    night = health.sleep_between("2026-10-25", "2026-10-25")[0]
    assert night["hrv_ms"] == 55 and night["hrv_status"] == "BALANCED"
    assert night["resting_hr"] == 52  # from the daily summary
    assert len(health.sleep_between("2026-09-26", "2026-10-26")) == 21  # every 3rd night unworn


def test_sync_failure_is_recorded_not_raised(garmin_fake):
    garmin_fake.fail = garmin.GarminError("Garmin rejected the API key.", 401)
    result = health.sync_garmin(TODAY)
    assert result == {"status": "error", "error": "Garmin rejected the API key."}
    status = health.sync_status()
    assert status["last_error"] == "Garmin rejected the API key." and status["last_success_at"] is None


def test_sleep_and_fitness_endpoints(client, garmin_fake):
    client.post("/sync", params={"today": TODAY.isoformat()})
    sleep = client.get("/sleep", params={"days": 14, "today": TODAY.isoformat()}).json()
    assert sleep["configured"] and len(sleep["nights"]) == 10
    assert "raw_json" not in sleep["nights"][0]
    fitness = client.get("/fitness", params={"days": 14, "today": TODAY.isoformat()}).json()
    assert len(fitness["days"]) == 14 and fitness["activities"][0]["activity_type"] == "running"
    assert client.get("/sleep", params={"days": 0}).status_code == 400


# --- derived values ---------------------------------------------------------


def _days_worn(steps_and_kcal):
    upsert_daily_activity([
        {"local_date": (TODAY - timedelta(days=i + 1)).isoformat(), "source": "garmin",
         "steps": steps, "active_kcal": kcal, "fetched_at": NOW}
        for i, (steps, kcal) in enumerate(steps_and_kcal)
    ])


def test_active_kcal_average_needs_three_worn_days():
    _days_worn([(9000, 600), (8000, 400)])
    assert health.recent_active_kcal_avg(TODAY) is None
    _days_worn([(9000, 600), (8000, 400), (0, 0), (7000, 500)])  # the 0-step day wasn't worn
    assert health.recent_active_kcal_avg(TODAY) == pytest.approx(500)


def test_active_kcal_average_ignores_today():
    upsert_daily_activity([{"local_date": TODAY.isoformat(), "source": "garmin", "steps": 9000,
                            "active_kcal": 5000, "fetched_at": NOW}])
    _days_worn([(9000, 600)] * 3)
    assert health.recent_active_kcal_avg(TODAY) == pytest.approx(600)


def test_format_for_analysis(garmin_fake):
    health.sync_garmin(TODAY)
    text = health.format_for_analysis(["2026-10-24", "2026-10-25", "2026-10-26"])
    lines = text.splitlines()
    assert lines[0] == "2026-10-24: 9000 steps, 600 active kcal"  # 24th: no sleep (every 3rd night)
    assert lines[1].startswith("2026-10-25: night before: slept 7h00, score 78, HRV 55ms, deep 1h30")
    assert "workout: running 45 min 520 kcal load 140" in lines[2]
    assert health.format_for_analysis(["2025-01-01"]) == ""
