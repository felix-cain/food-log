"""Wearable data: Garmin sync, parsing, and read helpers for the Sleep/Fitness
tabs, the summary analysis and the calorie target.

Field mapping notes:
- Sleep fields are confirmed against a real (empty) dailySleep response.
- Daily summaries, HRV and activities use Garmin Connect's long-standing field
  names and haven't been seen with real data yet. Every parser tries several
  candidate names and the raw response is stored, so a mapping fix can be
  applied without refetching.

Render's free tier has no scheduler, so syncing happens on request: the app
asks for a sync when it opens, and anything synced within the last hour is
skipped.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import garmin
from .db import (
    get_sync_state,
    list_activities_between,
    list_daily_activity_between,
    list_sleep_between,
    save_sync_state,
    upsert_activities,
    upsert_daily_activity,
    upsert_sleep_nights,
)

SOURCE = "garmin"
FIRST_SYNC_DAYS = 30      # history pulled on the very first sync
OVERLAP_DAYS = 3          # re-fetch recent days: watches sync late, today is partial
MAX_WINDOW_DAYS = 90      # never fetch more than this in one sync
RANGE_CHUNK_DAYS = 28     # Garmin range endpoints are kept to <=28 days per call
STALE_AFTER_SECONDS = 3600
ACTIVITY_PAGE = 100


# ============================================================================
# Parsing
# ============================================================================


def _pick(d, *paths):
    """First non-null value among dotted paths, e.g. 'sleepScores.overall.value'."""
    for path in paths:
        cur = d
        for part in path.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if cur is not None:
            return cur
    return None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _local_time(ms) -> str | None:
    """Garmin '...Local' timestamps are epoch ms of the local wall-clock time."""
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _records(value, *keys) -> list:
    """Unwrap a list response that may arrive bare or under a key."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return value[key]
    return []


def parse_sleep(raw, requested_date: str, fetched_at: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    dto = raw.get("dailySleepDTO") if isinstance(raw.get("dailySleepDTO"), dict) else raw
    sleep_seconds = _num(dto.get("sleepTimeSeconds"))
    if not sleep_seconds:
        return None  # no sleep recorded that night
    return {
        "night_date": dto.get("calendarDate") or requested_date,
        "source": SOURCE,
        "sleep_seconds": sleep_seconds,
        "deep_seconds": _num(dto.get("deepSleepSeconds")),
        "light_seconds": _num(dto.get("lightSleepSeconds")),
        "rem_seconds": _num(dto.get("remSleepSeconds")),
        "awake_seconds": _num(dto.get("awakeSleepSeconds")),
        "sleep_score": _num(_pick(dto, "sleepScores.overall.value", "sleepScore", "overallSleepScore.value")),
        "bedtime_local": _local_time(dto.get("sleepStartTimestampLocal")),
        "wake_local": _local_time(dto.get("sleepEndTimestampLocal")),
        "resting_hr": _num(_pick(raw, "restingHeartRate", "dailySleepDTO.restingHeartRate")),
        "hrv_ms": _num(_pick(raw, "avgOvernightHrv")),
        "hrv_status": _pick(raw, "hrvStatus"),
        "raw_json": json.dumps(raw),
        "fetched_at": fetched_at,
    }


def parse_hrv(item) -> tuple[str, float | None, str | None] | None:
    if not isinstance(item, dict) or not item.get("calendarDate"):
        return None
    return (
        item["calendarDate"],
        _num(_pick(item, "lastNightAvg", "lastNight", "hrvSummary.lastNightAvg")),
        _pick(item, "status", "hrvSummary.status"),
    )


def parse_daily(item, fetched_at: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    day = _pick(item, "calendarDate", "date", "summaryDate")
    if not day:
        return None
    return {
        "local_date": str(day)[:10],
        "source": SOURCE,
        "steps": _num(_pick(item, "totalSteps", "steps")),
        "active_kcal": _num(_pick(item, "activeKilocalories", "activeCalories", "activeKcal")),
        "bmr_kcal": _num(_pick(item, "bmrKilocalories", "bmrCalories")),
        "total_kcal": _num(_pick(item, "totalKilocalories", "totalCalories")),
        "resting_hr": _num(_pick(item, "restingHeartRate", "restingHr")),
        "moderate_min": _num(_pick(item, "moderateIntensityMinutes", "moderateMinutes")),
        "vigorous_min": _num(_pick(item, "vigorousIntensityMinutes", "vigorousMinutes")),
        "raw_json": json.dumps(item),
        "fetched_at": fetched_at,
    }


def parse_activity(item, fetched_at: str) -> dict | None:
    if not isinstance(item, dict) or item.get("activityId") is None:
        return None
    start = _pick(item, "startTimeLocal", "summaryDTO.startTimeLocal")
    if not isinstance(start, str) or len(start) < 10:
        return None
    return {
        "source": SOURCE,
        "source_id": str(item["activityId"]),
        "local_date": start[:10],
        "start_local": start[:16].replace(" ", "T"),
        "activity_type": _pick(item, "activityType.typeKey", "activityTypeDTO.typeKey"),
        "name": item.get("activityName"),
        "duration_s": _num(_pick(item, "duration", "summaryDTO.duration")),
        "distance_m": _num(_pick(item, "distance", "summaryDTO.distance")),
        "calories": _num(_pick(item, "calories", "summaryDTO.calories")),
        "avg_hr": _num(_pick(item, "averageHR", "averageHr", "summaryDTO.averageHR")),
        "max_hr": _num(_pick(item, "maxHR", "maxHr", "summaryDTO.maxHR")),
        "training_load": _num(_pick(item, "activityTrainingLoad", "summaryDTO.activityTrainingLoad")),
        "aerobic_te": _num(_pick(item, "aerobicTrainingEffect", "summaryDTO.trainingEffect")),
        "raw_json": json.dumps(item),
        "fetched_at": fetched_at,
    }


# ============================================================================
# Sync
# ============================================================================


def _chunks(start: date, end: date, size: int):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=size - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def _is_fresh(state: dict | None) -> bool:
    if not state or not state.get("last_success_at"):
        return False
    last = datetime.fromisoformat(state["last_success_at"])
    return (datetime.now(timezone.utc) - last).total_seconds() < STALE_AFTER_SECONDS


def sync_status() -> dict:
    state = get_sync_state(SOURCE) or {}
    return {
        "configured": garmin.is_configured(),
        "last_success_at": state.get("last_success_at"),
        "last_error": state.get("last_error"),
    }


def sync_garmin(today: date, force: bool = False) -> dict:
    """Pull recent Garmin data into the DB. Returns {status, ...counts}."""
    if not garmin.is_configured():
        return {"status": "not_configured"}
    state = get_sync_state(SOURCE)
    if not force and _is_fresh(state):
        return {"status": "fresh"}

    if state and state.get("last_synced_date"):
        start = date.fromisoformat(state["last_synced_date"]) - timedelta(days=OVERLAP_DAYS)
    else:
        start = today - timedelta(days=FIRST_SYNC_DAYS)
    start = max(start, today - timedelta(days=MAX_WINDOW_DAYS))
    fetched_at = datetime.now(timezone.utc).isoformat()

    try:
        with garmin.GarminClient() as g:
            # Sleep: one call per night (the per-night record is the confirmed shape)
            nights: dict[str, dict] = {}
            day = start
            while day <= today:
                row = parse_sleep(g.daily_sleep(day.isoformat()), day.isoformat(), fetched_at)
                if row:
                    nights[row["night_date"]] = row
                day += timedelta(days=1)

            dailies: dict[str, dict] = {}
            for a, b in _chunks(start, today, RANGE_CHUNK_DAYS):
                s, e = a.isoformat(), b.isoformat()
                for item in _records(g.hrv_summaries(s, e), "hrvSummaries", "data"):
                    parsed = parse_hrv(item)
                    if parsed and parsed[0] in nights:
                        night = nights[parsed[0]]
                        night["hrv_ms"] = night["hrv_ms"] if night["hrv_ms"] is not None else parsed[1]
                        night["hrv_status"] = night["hrv_status"] or parsed[2]
                for item in _records(g.daily_summaries(s, e), "data", "dailySummaries"):
                    row = parse_daily(item, fetched_at)
                    if row:
                        dailies[row["local_date"]] = row
                for item in _records(g.body_battery(s, e)):
                    day_key = item.get("date") if isinstance(item, dict) else None
                    if not day_key or (item.get("charged") is None and item.get("drained") is None):
                        continue
                    row = dailies.setdefault(day_key, {
                        "local_date": day_key, "source": SOURCE, "raw_json": None, "fetched_at": fetched_at,
                    })
                    row["body_battery_charged"] = _num(item.get("charged"))
                    row["body_battery_drained"] = _num(item.get("drained"))

            activities = []
            offset = 0
            while True:
                page = _records(
                    g.search_activities(start.isoformat(), today.isoformat(), start=offset, limit=ACTIVITY_PAGE),
                    "activityList",
                )
                activities += [r for r in (parse_activity(i, fetched_at) for i in page) if r]
                if len(page) < ACTIVITY_PAGE:
                    break
                offset += ACTIVITY_PAGE
    except (garmin.GarminError, garmin.httpx.HTTPError) as e:
        save_sync_state(SOURCE, error=str(e))
        return {"status": "error", "error": str(e)}

    # The sleep record may omit resting HR; the same day's summary usually has it
    for night in nights.values():
        if night["resting_hr"] is None and night["night_date"] in dailies:
            night["resting_hr"] = dailies[night["night_date"]].get("resting_hr")

    upsert_sleep_nights(list(nights.values()))
    upsert_daily_activity(list(dailies.values()))
    upsert_activities(activities)
    save_sync_state(SOURCE, synced_date=today.isoformat())
    return {
        "status": "ok",
        "nights": len(nights),
        "days": len(dailies),
        "activities": len(activities),
    }


# ============================================================================
# Reads
# ============================================================================


def sleep_between(start: str, end: str) -> list[dict]:
    return list_sleep_between(start, end)


def fitness_between(start: str, end: str) -> dict:
    return {
        "days": list_daily_activity_between(start, end),
        "activities": list_activities_between(start, end),
    }


def recent_active_kcal_avg(today: date, days: int = 7, min_days: int = 3) -> float | None:
    """Average measured active kcal over the `days` before today.

    Only days the watch was clearly worn (steps recorded) count, and at least
    `min_days` of them are needed; otherwise None (fall back to the profile).
    """
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    worn = [
        d["active_kcal"]
        for d in list_daily_activity_between(start.isoformat(), end.isoformat())
        if d.get("active_kcal") is not None and (d.get("steps") or 0) > 0
    ]
    return sum(worn) / len(worn) if len(worn) >= min_days else None


def _hm(seconds: float | None) -> str:
    if not seconds:
        return "?"
    minutes = round(seconds / 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


def format_for_analysis(dates: list[str]) -> str:
    """Per-day sleep and activity lines for the summary prompt ('' if none)."""
    if not dates:
        return ""
    start, end = dates[0], dates[-1]
    sleep = {s["night_date"]: s for s in list_sleep_between(start, end)}
    daily = {d["local_date"]: d for d in list_daily_activity_between(start, end)}
    acts: dict[str, list[dict]] = {}
    for a in list_activities_between(start, end):
        acts.setdefault(a["local_date"], []).append(a)
    if not (sleep or daily or acts):
        return ""

    lines = []
    for d in dates:
        parts = []
        if s := sleep.get(d):
            bits = [f"slept {_hm(s['sleep_seconds'])}"]
            if s.get("sleep_score") is not None:
                bits.append(f"score {round(s['sleep_score'])}")
            if s.get("hrv_ms") is not None:
                bits.append(f"HRV {round(s['hrv_ms'])}ms")
            if s.get("deep_seconds") is not None:
                bits.append(f"deep {_hm(s['deep_seconds'])}")
            parts.append("night before: " + ", ".join(bits))
        if (a := daily.get(d)) and (a.get("steps") or a.get("active_kcal")):
            bits = []
            if a.get("steps") is not None:
                bits.append(f"{round(a['steps'])} steps")
            if a.get("active_kcal") is not None:
                bits.append(f"{round(a['active_kcal'])} active kcal")
            parts.append(", ".join(bits))
        for w in acts.get(d, []):
            desc = (w.get("activity_type") or w.get("name") or "workout").replace("_", " ")
            bits = [desc]
            if w.get("duration_s"):
                bits.append(f"{round(w['duration_s'] / 60)} min")
            if w.get("calories"):
                bits.append(f"{round(w['calories'])} kcal")
            if w.get("training_load"):
                bits.append(f"load {round(w['training_load'])}")
            parts.append("workout: " + " ".join(bits))
        lines.append(f"{d}: " + ("; ".join(parts) if parts else "(no watch data)"))
    return "\n".join(lines)
