from datetime import date

from backend import health
from tests.fakes import meal

TODAY = "2026-10-26"


def _log(client, day: str, meal_type: str, calories: int = 500, note: str | None = None) -> int:
    body = {"date": day, "meal_type": meal_type, "analysis": meal(calories=calories).model_dump(), "note": note}
    return client.post("/entries", json=body).json()["id"]


def _summary(client, **params):
    response = client.get("/summary", params={"days": 7, "today": TODAY, **params})
    assert response.status_code == 200
    return response.json()


def test_period_ends_yesterday_and_keeps_unlogged_days(client):
    s = _summary(client)
    assert (s["period_start"], s["period_end"]) == ("2026-10-19", "2026-10-25")
    assert [d["meal_count"] for d in s["daily_totals"]] == [0] * 7
    assert s["quick_numbers"]["avg_basis"] == "none"


def test_today_is_excluded(client):
    _log(client, TODAY, "lunch")
    assert _summary(client)["quick_numbers"]["days_logged"] == 0


def test_averages_use_fully_logged_days_when_there_are_any(client):
    for meal_type in ("breakfast", "lunch", "dinner"):
        _log(client, "2026-10-24", meal_type, calories=700)
    _log(client, "2026-10-25", "lunch", calories=100)  # partial day
    q = _summary(client)["quick_numbers"]
    assert q["avg_basis"] == "full" and q["full_days"] == 1 and q["days_logged"] == 2
    assert q["avg_calories_per_day"] == 2100


def test_partial_days_only_falls_back_to_logged_days(client):
    _log(client, "2026-10-25", "lunch", calories=600)
    q = _summary(client)["quick_numbers"]
    assert q["avg_basis"] == "logged" and q["avg_calories_per_day"] == 600


def test_prompt_marks_partial_days_and_includes_notes_and_targets(client, claude, with_profile):
    _log(client, "2026-10-25", "lunch", note="leftover curry")
    _summary(client)
    prompt = claude.calls[-1]["messages"][0]["content"]
    assert "2026-10-25 (partial)" in prompt
    assert 'their own words: "leftover curry"' in prompt
    assert "Estimated daily targets: ~2750 cal" in prompt
    assert "2026-10-19: (nothing logged)" in prompt


def test_analysis_is_cached_until_the_data_changes(client, claude):
    meal_id = _log(client, "2026-10-25", "lunch")
    _summary(client)
    _summary(client)
    assert len(claude.calls) == 1, "same data should reuse the cached analysis"

    _summary(client, refresh="true")
    assert len(claude.calls) == 2, "Refresh forces a new analysis"

    client.put(f"/entries/{meal_id}", json={"analysis": meal(calories=900).model_dump()})
    _summary(client)
    assert len(claude.calls) == 3, "editing a meal invalidates the cache"

    _summary(client, today="2026-10-27")
    assert len(claude.calls) == 4, "a new day is a new period"


def test_claude_failure_still_returns_the_stats(client, claude):
    _log(client, "2026-10-25", "lunch", calories=600)
    claude.fail = RuntimeError("service unavailable")
    s = _summary(client)
    assert s["analysis"] is None and "service unavailable" in s["analysis_error"]
    assert s["quick_numbers"]["avg_calories_per_day"] == 600

    claude.fail = None
    s = _summary(client)
    assert s["analysis"] is not None, "a failure isn't cached, so the next load retries"


def test_refusal_is_reported_as_an_error(client, claude):
    claude.stop_reason = "refusal"
    assert _summary(client)["analysis_error"] == "Analysis failed: Claude declined to analyse this data"


def test_watch_data_reaches_the_prompt_and_the_target(client, claude, with_profile, garmin_fake):
    assert health.sync_garmin(date.fromisoformat(TODAY))["status"] == "ok"
    s = _summary(client)
    prompt = claude.calls[-1]["messages"][0]["content"]
    assert "Sleep and activity from their watch:" in prompt
    assert "2026-10-25: night before: slept 7h00, score 78, HRV 55ms" in prompt
    assert "measured activity" in prompt
    assert s["targets"]["basis"] == "garmin" and s["targets"]["calories"] == 2500

    # Re-syncing identical data keeps the cached analysis
    health.sync_garmin(date.fromisoformat(TODAY), force=True)
    _summary(client)
    assert len(claude.calls) == 1


def test_days_out_of_range_is_400(client):
    assert client.get("/summary", params={"days": 0}).status_code == 400
    assert client.get("/summary", params={"days": 91}).status_code == 400
