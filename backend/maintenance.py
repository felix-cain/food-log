"""Things that expire or need a periodic review, listed in maintenance.json at
the repo root. Checked weekly in CI (a failure makes GitHub email you) and
surfaced as a banner in the app."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

MAINTENANCE_FILE = Path(__file__).parent.parent / "maintenance.json"
REQUIRED_FIELDS = ("id", "what", "due", "warn_days", "action")


def load(path: Path = MAINTENANCE_FILE) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    for item in items:
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            raise ValueError(f"maintenance item {item.get('id', '?')} is missing {missing}")
        date.fromisoformat(item["due"])  # raises on a malformed date
    return items


def due(today: date, items: list[dict] | None = None) -> list[dict]:
    """Items due within their warning window (or overdue), soonest first."""
    out = []
    for item in load() if items is None else items:
        days_left = (date.fromisoformat(item["due"]) - today).days
        if days_left <= item["warn_days"]:
            out.append({**item, "days_left": days_left})
    return sorted(out, key=lambda i: i["days_left"])


def describe(item: dict) -> str:
    days = item["days_left"]
    when = "overdue" if days < 0 else "due today" if days == 0 else f"due in {days} day{'s' if days != 1 else ''}"
    return f"{item['what']}: {when}"
