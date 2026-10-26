"""Print the STRUCTURE of Garmin Personal API responses: field names and types only.

No values are printed, so the output is safe to share when mapping fields.
Run from the project folder with your key set for this terminal session:

    $env:GARMIN_PERSONAL_API_KEY = "<your key>"; python garmin_probe.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from backend.garmin import GarminClient, GarminError

SAMPLE = 5  # list elements merged when describing a list's items


def _type(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    return type(v).__name__


def shape(v, depth: int = 0, max_depth: int = 5):
    """Describe a JSON value by its keys and types, never its values."""
    if isinstance(v, dict):
        if depth >= max_depth:
            return f"dict({len(v)} keys)"
        return {k: shape(x, depth + 1, max_depth) for k, x in v.items()}
    if isinstance(v, list):
        if not v:
            return "list[0]"
        merged = _merge([shape(x, depth + 1, max_depth) for x in v[:SAMPLE]])
        return [f"list[{len(v)}]", merged]
    return _type(v)


def _merge(shapes: list):
    """Union of several element shapes; a non-null type wins over null."""
    if all(isinstance(s, dict) for s in shapes):
        out: dict = {}
        for s in shapes:
            for k, t in s.items():
                if k not in out or out[k] == "null":
                    out[k] = t
        return out
    non_null = [s for s in shapes if s != "null"]
    return non_null[0] if non_null else "null"


def main() -> None:
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    month_ago = (today - timedelta(days=30)).isoformat()

    with GarminClient() as g:
        print(f"token lifetime: ~{g.token_lifetime_hours():.1f} hours\n")
        calls = {
            "daily_sleep(yesterday)": lambda: g.daily_sleep(yesterday),
            "daily_sleep_summaries(7d)": lambda: g.daily_sleep_summaries(week_ago, yesterday),
            "hrv_summaries(7d)": lambda: g.hrv_summaries(week_ago, yesterday),
            "daily_summaries(7d)": lambda: g.daily_summaries(week_ago, yesterday),
            "body_battery(7d)": lambda: g.body_battery(week_ago, yesterday),
            "search_activities(30d)": lambda: g.search_activities(month_ago, today.isoformat(), limit=5),
        }
        results = {}
        for name, call in calls.items():
            try:
                results[name] = shape(call())
            except GarminError as e:
                results[name] = f"ERROR {e.status_code}: {e}"

        # One full activity record, if the search returned any
        try:
            acts = g.search_activities(month_ago, today.isoformat(), limit=1)
            items = acts if isinstance(acts, list) else (acts or {}).get("activityList") or []
            if items and isinstance(items[0], dict) and "activityId" in items[0]:
                results["activity(first)"] = shape(g.activity(items[0]["activityId"]), max_depth=3)
        except GarminError as e:
            results["activity(first)"] = f"ERROR {e.status_code}: {e}"

    print(json.dumps(results, indent=1))


def _count(value) -> int:
    """Number of records in a response, whatever its wrapper."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("data", "activityList", "hrvSummaries", "dailySleepSummaries"):
            if isinstance(value.get(key), list):
                return len(value[key])
        return 1
    return 0


def counts() -> None:
    """How much data exists over longer windows: counts only, no values."""
    today = date.today()
    ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    lines = []

    def attempt(label, call):
        try:
            lines.append(f"{label}: {call()}")
        except GarminError as e:
            lines.append(f"{label}: ERROR {e.status_code}: {e}")

    with GarminClient() as g:
        def nights_with_sleep():
            hits = 0
            for n in range(0, 14):
                rec = g.daily_sleep(ago(n)) or {}
                dto = rec.get("dailySleepDTO", rec) if isinstance(rec, dict) else {}
                hits += bool(dto.get("sleepTimeSeconds"))
            return f"{hits} of the last 14 days (including today)"

        attempt("dailySleep, per day", nights_with_sleep)
        for days in (28, 90):
            attempt(f"sleep summaries, {days}d", lambda d=days: _count(g.daily_sleep_summaries(ago(d), today.isoformat())))
            attempt(f"HRV summaries, {days}d", lambda d=days: _count(g.hrv_summaries(ago(d), today.isoformat())))
            attempt(f"daily summaries, {days}d", lambda d=days: _count(g.daily_summaries(ago(d), today.isoformat())))
        for days in (30, 365):
            attempt(f"activities, {days}d", lambda d=days: _count(g.search_activities(ago(d), today.isoformat())))
        attempt("activities, no date filter", lambda: _count(g.get(
            "/activitylist-service/activities/search/activities", {"start": 0, "limit": 20})))
        attempt("body battery days with readings, 7d", lambda: sum(
            1 for r in (g.body_battery(ago(7), today.isoformat()) or []) if r.get("bodyBatteryValuesArray")))

    print("\n".join(lines))


if __name__ == "__main__":
    import sys

    counts() if "--counts" in sys.argv else main()
