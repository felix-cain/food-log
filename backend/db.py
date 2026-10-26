"""SQLite storage for food log entries.

Switched by env var:
- TURSO_URL set: talk to Turso via the HRANA HTTP API (uses httpx)
- Otherwise: open local `food_log.db` via stdlib sqlite3

The Turso path uses Anthropic SDK's transitive httpx dep — no native code,
no Rust toolchain, no Python-version-specific wheels.

Note: queries on the Turso path are NOT transactional across separate
execute() calls. For this app the only multi-statement write is save_meal
(meal insert + N item inserts), which accepts a small risk of a partially
saved meal if a single HTTP call fails mid-operation. Acceptable for a
personal log.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .vision import MealAnalysis

# FOODLOG_DB_PATH lets tests point the app at a throwaway database
LOCAL_DB_PATH = Path(os.getenv("FOODLOG_DB_PATH") or Path(__file__).parent.parent / "food_log.db")


# ============================================================================
# Turso HTTP wrapper — minimal sqlite3-compatible surface for our usage
# ============================================================================


class _TursoCursor:
    """sqlite3.Cursor-shaped view over one HRANA execute response."""

    def __init__(self, result: dict) -> None:
        self._rows: list[tuple] = [
            tuple(_decode_hrana_value(cell) for cell in row)
            for row in result.get("rows", [])
        ]
        rowid = result.get("last_insert_rowid")
        self.lastrowid: int | None = int(rowid) if rowid is not None else None
        self.rowcount: int = result.get("affected_row_count", 0)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class _TursoConnection:
    """Minimal sqlite3.Connection-shaped wrapper over HRANA HTTP API."""

    def __init__(self, libsql_url: str, auth_token: str | None) -> None:
        url = libsql_url
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://") :]
        self._endpoint = url.rstrip("/") + "/v2/pipeline"
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

    def execute(self, sql: str, params: tuple = ()) -> _TursoCursor:
        import httpx  # transitive dep via anthropic; imported lazily

        body = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": sql,
                        "args": [_encode_hrana_value(p) for p in params],
                    },
                },
                {"type": "close"},
            ]
        }
        response = httpx.post(
            self._endpoint, headers=self._headers, json=body, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        exec_result = data["results"][0]
        if exec_result.get("type") != "ok":
            err = exec_result.get("error", {})
            raise RuntimeError(
                f"Turso execute error: {err.get('message', exec_result)}"
            )
        return _TursoCursor(exec_result["response"]["result"])

    def commit(self) -> None:
        # HRANA auto-commits each pipeline.
        return None

    def close(self) -> None:
        return None


def _encode_hrana_value(v: Any) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, str):
        return {"type": "text", "value": v}
    raise TypeError(f"Unsupported parameter type: {type(v).__name__}")


def _decode_hrana_value(cell: dict) -> Any:
    t = cell["type"]
    if t == "null":
        return None
    if t == "integer":
        return int(cell["value"])
    if t == "float":
        return float(cell["value"])
    if t == "text":
        return cell["value"]
    raise ValueError(f"Unknown HRANA type: {t}")


# ============================================================================
# Connection switcher + schema + public API
# ============================================================================


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    meal_type TEXT NOT NULL CHECK(meal_type IN ('breakfast','lunch','dinner','snack')),
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT,
    source TEXT,
    updated_at TEXT,
    favourite_id INTEGER
);

CREATE TABLE IF NOT EXISTS meal_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    grams REAL NOT NULL,
    calories INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    protein_g REAL,
    carbs_g REAL,
    fat_g REAL,
    fibre_g REAL,
    free_sugar_g REAL,
    sat_fat_g REAL,
    salt_g REAL,
    food_group TEXT,
    processing TEXT,
    fruit_veg_portions REAL,
    plants_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    age INTEGER NOT NULL,
    sex TEXT NOT NULL,
    height_cm REAL NOT NULL,
    weight_kg REAL NOT NULL,
    activity TEXT NOT NULL,
    goal TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- A starred meal that can be logged again in one tap. items_json holds the
-- meal's items in meal_items' shape (macros may be null on old meals).
CREATE TABLE IF NOT EXISTS favourite (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_type TEXT NOT NULL,
    name TEXT NOT NULL,
    items_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS summary_cache (
    period_days INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Wearable data. `source` leaves room for Eight Sleep alongside Garmin.
-- night_date is the date you woke up, so night N lines up with food on day N.
CREATE TABLE IF NOT EXISTS sleep_night (
    night_date TEXT NOT NULL,
    source TEXT NOT NULL,
    sleep_seconds REAL,
    deep_seconds REAL,
    light_seconds REAL,
    rem_seconds REAL,
    awake_seconds REAL,
    sleep_score REAL,
    bedtime_local TEXT,
    wake_local TEXT,
    resting_hr REAL,
    hrv_ms REAL,
    hrv_status TEXT,
    raw_json TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (night_date, source)
);

CREATE TABLE IF NOT EXISTS daily_activity (
    local_date TEXT NOT NULL,
    source TEXT NOT NULL,
    steps REAL,
    active_kcal REAL,
    bmr_kcal REAL,
    total_kcal REAL,
    resting_hr REAL,
    moderate_min REAL,
    vigorous_min REAL,
    body_battery_charged REAL,
    body_battery_drained REAL,
    raw_json TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (local_date, source)
);

CREATE TABLE IF NOT EXISTS activity (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    local_date TEXT NOT NULL,
    start_local TEXT,
    activity_type TEXT,
    name TEXT,
    duration_s REAL,
    distance_m REAL,
    calories REAL,
    avg_hr REAL,
    max_hr REAL,
    training_load REAL,
    aerobic_te REAL,
    raw_json TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_date ON activity(local_date);

CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    last_synced_date TEXT,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT
);
"""

# Columns added after the initial schema. Run ADD COLUMN idempotently on
# init so existing DBs (local sqlite from earlier sessions, Turso from the
# first deploy) get the new columns without manual migration.
_MIGRATION_COLUMNS = (
    ("meal_items", "protein_g", "REAL"),
    ("meal_items", "carbs_g", "REAL"),
    ("meal_items", "fat_g", "REAL"),
    ("meals", "note", "TEXT"),  # user's own description / corrections
    ("meals", "source", "TEXT"),  # 'photo' or 'text'
    ("meals", "updated_at", "TEXT"),  # set when an entry is corrected
    ("meals", "favourite_id", "INTEGER"),  # the favourite it was logged from, or starred as
    # Food-quality detail (null on meals logged before it existed)
    ("meal_items", "fibre_g", "REAL"),
    ("meal_items", "free_sugar_g", "REAL"),
    ("meal_items", "sat_fat_g", "REAL"),
    ("meal_items", "salt_g", "REAL"),
    ("meal_items", "food_group", "TEXT"),
    ("meal_items", "processing", "TEXT"),
    ("meal_items", "fruit_veg_portions", "REAL"),
    ("meal_items", "plants_json", "TEXT"),
)


def _open_connection():
    turso_url = os.getenv("TURSO_URL")
    if turso_url:
        return _TursoConnection(turso_url, os.getenv("TURSO_AUTH_TOKEN"))
    conn = sqlite3.connect(LOCAL_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _connect() -> Iterator:
    conn = _open_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        for stmt in filter(None, (s.strip() for s in _SCHEMA.split(";"))):
            conn.execute(stmt)
        for table, col, coltype in _MIGRATION_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # column already exists


def save_meal(
    date: str,
    meal_type: str,
    analysis: MealAnalysis,
    note: str | None = None,
    source: str = "photo",
) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO meals (date, meal_type, description, created_at, note, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, meal_type, analysis.description, created_at, note, source),
        )
        meal_id = cursor.lastrowid
        _insert_items(conn, meal_id, analysis)
        return meal_id


def update_meal(meal_id: int, analysis: MealAnalysis, note: str | None) -> bool:
    """Replace a saved meal's description, items and note (after a correction)."""
    updated_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE meals SET description = ?, note = ?, updated_at = ? WHERE id = ?",
            (analysis.description, note, updated_at, meal_id),
        )
        if cursor.rowcount == 0:
            return False
        conn.execute("DELETE FROM meal_items WHERE meal_id = ?", (meal_id,))
        _insert_items(conn, meal_id, analysis)
        return True


def _insert_items(conn, meal_id: int, analysis: MealAnalysis) -> None:
    _insert_item_rows(conn, meal_id, [
        {
            "name": item.name,
            "grams": float(item.estimated_grams),
            "calories": int(item.calories_estimate),
            "confidence": item.confidence,
            "protein_g": float(item.protein_g),
            "carbs_g": float(item.carbs_g),
            "fat_g": float(item.fat_g),
            "fibre_g": float(item.fibre_g),
            "free_sugar_g": float(item.free_sugar_g),
            "sat_fat_g": float(item.sat_fat_g),
            "salt_g": float(item.salt_g),
            "food_group": item.food_group,
            "processing": item.processing,
            "fruit_veg_portions": float(item.fruit_veg_portions),
            "plants": item.plants,
        }
        for item in analysis.items
    ])


# Item fields in list order; "plants" is stored as JSON in plants_json
_ITEM_FIELDS = (
    "name", "grams", "calories", "confidence", "protein_g", "carbs_g", "fat_g",
    "fibre_g", "free_sugar_g", "sat_fat_g", "salt_g", "food_group", "processing",
    "fruit_veg_portions", "plants",
)
_ITEM_COLUMNS = tuple("plants_json" if f == "plants" else f for f in _ITEM_FIELDS)


def _insert_item_rows(conn, meal_id: int, rows: list[dict]) -> None:
    """Insert items already in meal_items' shape (as listed by list_meals_between)."""
    for row in rows:
        values = [
            json.dumps(row["plants"]) if f == "plants" and row.get("plants") is not None else row.get(f)
            for f in _ITEM_FIELDS
        ]
        conn.execute(
            f"INSERT INTO meal_items (meal_id, {', '.join(_ITEM_COLUMNS)}) "
            f"VALUES (?, {', '.join('?' for _ in _ITEM_COLUMNS)})",
            (meal_id, *values),
        )


def list_meals_for_date(date: str) -> list[dict]:
    return list_meals_between(date, date)


def list_meals_between(start: str, end: str) -> list[dict]:
    """All meals with start <= date <= end (inclusive), oldest first.

    Two queries regardless of range size — matters on the Turso path where
    each query is an HTTP round trip.
    """
    with _connect() as conn:
        meal_rows = conn.execute(
            "SELECT id, date, meal_type, description, created_at, note, source, updated_at, favourite_id "
            "FROM meals WHERE date BETWEEN ? AND ? ORDER BY date, created_at",
            (start, end),
        ).fetchall()
        item_rows = conn.execute(
            f"SELECT mi.meal_id, {', '.join('mi.' + c for c in _ITEM_COLUMNS)} "
            "FROM meal_items mi JOIN meals m ON m.id = mi.meal_id "
            "WHERE m.date BETWEEN ? AND ? ORDER BY mi.id",
            (start, end),
        ).fetchall()

    items_by_meal: dict[int, list[dict]] = {}
    for i in item_rows:
        item = dict(zip(_ITEM_FIELDS, i[1:]))
        item["plants"] = json.loads(item["plants"]) if item["plants"] else None
        items_by_meal.setdefault(i[0], []).append(item)
    return [
        {
            "id": row[0],
            "date": row[1],
            "meal_type": row[2],
            "description": row[3],
            "created_at": row[4],
            "note": row[5],
            "source": row[6] or "photo",
            "updated_at": row[7],
            "favourite_id": row[8],
            "items": items_by_meal.get(row[0], []),
        }
        for row in meal_rows
    ]


def delete_meal(meal_id: int) -> bool:
    with _connect() as conn:
        # The Turso path never sets PRAGMA foreign_keys, so ON DELETE CASCADE
        # doesn't fire there — remove items explicitly
        conn.execute("DELETE FROM meal_items WHERE meal_id = ?", (meal_id,))
        cursor = conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        return cursor.rowcount > 0


# ============================================================================
# Favourites: star a saved meal, then log a copy of it in one tap
# ============================================================================


def _get_meal(meal_id: int) -> dict | None:
    with _connect() as conn:
        rows = conn.execute("SELECT date FROM meals WHERE id = ?", (meal_id,)).fetchall()
    if not rows:
        return None
    return next(m for m in list_meals_between(rows[0][0], rows[0][0]) if m["id"] == meal_id)


def list_favourites() -> list[dict]:
    """Most-used first. Each carries its items and total calories for display."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, meal_type, name, items_json, use_count, last_used_at FROM favourite "
            "ORDER BY use_count DESC, created_at DESC"
        ).fetchall()
    favourites = []
    for r in rows:
        items = json.loads(r[3])
        favourites.append({
            "id": r[0], "meal_type": r[1], "name": r[2], "items": items,
            "calories": sum(i.get("calories") or 0 for i in items),
            "use_count": r[4], "last_used_at": r[5],
        })
    return favourites


def create_favourite_from_meal(meal_id: int, name: str | None = None) -> int | None:
    """Star a saved meal. Returns the new favourite's id, or None if no such meal."""
    meal = _get_meal(meal_id)
    if meal is None:
        return None
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO favourite (meal_type, name, items_json, created_at) VALUES (?, ?, ?, ?)",
            (meal["meal_type"], name or meal["description"], json.dumps(meal["items"]), created_at),
        )
        favourite_id = cursor.lastrowid
        conn.execute("UPDATE meals SET favourite_id = ? WHERE id = ?", (favourite_id, meal_id))
    return favourite_id


def delete_favourite(favourite_id: int) -> bool:
    """Un-star. Meals logged from it stay; they just lose the link."""
    with _connect() as conn:
        conn.execute("UPDATE meals SET favourite_id = NULL WHERE favourite_id = ?", (favourite_id,))
        cursor = conn.execute("DELETE FROM favourite WHERE id = ?", (favourite_id,))
        return cursor.rowcount > 0


def log_favourite(favourite_id: int, date: str) -> int | None:
    """Save a copy of the favourite as a new meal on `date`. Returns the meal id."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT meal_type, name, items_json FROM favourite WHERE id = ?", (favourite_id,)
        ).fetchall()
        if not rows:
            return None
        meal_type, name, items_json = rows[0]
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO meals (date, meal_type, description, created_at, source, favourite_id) "
            "VALUES (?, ?, ?, ?, 'favourite', ?)",
            (date, meal_type, name, now, favourite_id),
        )
        meal_id = cursor.lastrowid
        _insert_item_rows(conn, meal_id, json.loads(items_json))
        conn.execute(
            "UPDATE favourite SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
            (now, favourite_id),
        )
        return meal_id


# ============================================================================
# Profile (single row) + summary analysis cache
# ============================================================================


_PROFILE_FIELDS = ("age", "sex", "height_cm", "weight_kg", "activity", "goal")


def get_profile() -> dict | None:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_PROFILE_FIELDS)}, updated_at FROM profile WHERE id = 1"
        ).fetchall()
    if not rows:
        return None
    return dict(zip((*_PROFILE_FIELDS, "updated_at"), rows[0]))


def save_profile(profile: dict) -> None:
    updated_at = datetime.now(timezone.utc).isoformat()
    columns = (*_PROFILE_FIELDS, "updated_at")
    with _connect() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO profile (id, {', '.join(columns)}) "
            f"VALUES (1, {', '.join('?' for _ in columns)})",
            (*(profile[f] for f in _PROFILE_FIELDS), updated_at),
        )


def get_cached_analysis(period_days: int, fingerprint: str) -> str | None:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT analysis_json FROM summary_cache "
            "WHERE period_days = ? AND fingerprint = ?",
            (period_days, fingerprint),
        ).fetchall()
    return rows[0][0] if rows else None


def save_cached_analysis(period_days: int, fingerprint: str, analysis_json: str) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO summary_cache "
            "(period_days, fingerprint, analysis_json, created_at) VALUES (?, ?, ?, ?)",
            (period_days, fingerprint, analysis_json, created_at),
        )


# ============================================================================
# Wearable data (sleep, daily activity, workouts) + sync bookkeeping
# ============================================================================

_SLEEP_COLUMNS = (
    "night_date", "source", "sleep_seconds", "deep_seconds", "light_seconds", "rem_seconds",
    "awake_seconds", "sleep_score", "bedtime_local", "wake_local", "resting_hr", "hrv_ms",
    "hrv_status", "raw_json", "fetched_at",
)
_DAILY_COLUMNS = (
    "local_date", "source", "steps", "active_kcal", "bmr_kcal", "total_kcal", "resting_hr",
    "moderate_min", "vigorous_min", "body_battery_charged", "body_battery_drained",
    "raw_json", "fetched_at",
)
_ACTIVITY_COLUMNS = (
    "source", "source_id", "local_date", "start_local", "activity_type", "name", "duration_s",
    "distance_m", "calories", "avg_hr", "max_hr", "training_load", "aerobic_te",
    "raw_json", "fetched_at",
)


def _upsert(table: str, columns: tuple[str, ...], rows: list[dict]) -> None:
    # Table and column names come from the constants above, never from input
    if not rows:
        return
    sql = (
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )
    with _connect() as conn:
        for row in rows:
            conn.execute(sql, tuple(row.get(c) for c in columns))


def _select(table: str, columns: tuple[str, ...], date_col: str, start: str, end: str) -> list[dict]:
    cols = [c for c in columns if c != "raw_json"]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} WHERE {date_col} BETWEEN ? AND ? "
            f"ORDER BY {date_col}",
            (start, end),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def upsert_sleep_nights(rows: list[dict]) -> None:
    _upsert("sleep_night", _SLEEP_COLUMNS, rows)


def upsert_daily_activity(rows: list[dict]) -> None:
    _upsert("daily_activity", _DAILY_COLUMNS, rows)


def upsert_activities(rows: list[dict]) -> None:
    _upsert("activity", _ACTIVITY_COLUMNS, rows)


def list_sleep_between(start: str, end: str) -> list[dict]:
    return _select("sleep_night", _SLEEP_COLUMNS, "night_date", start, end)


def list_daily_activity_between(start: str, end: str) -> list[dict]:
    return _select("daily_activity", _DAILY_COLUMNS, "local_date", start, end)


def list_activities_between(start: str, end: str) -> list[dict]:
    return _select("activity", _ACTIVITY_COLUMNS, "local_date", start, end)


def get_sync_state(source: str) -> dict | None:
    cols = ("source", "last_synced_date", "last_success_at", "last_attempt_at", "last_error")
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM sync_state WHERE source = ?", (source,)
        ).fetchall()
    return dict(zip(cols, rows[0])) if rows else None


def save_sync_state(
    source: str, *, synced_date: str | None = None, error: str | None = None
) -> None:
    """Record a sync attempt; a success (no error) also advances last_synced_date."""
    now = datetime.now(timezone.utc).isoformat()
    previous = get_sync_state(source) or {}
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sync_state "
            "(source, last_synced_date, last_success_at, last_attempt_at, last_error) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                source,
                synced_date if error is None else previous.get("last_synced_date"),
                now if error is None else previous.get("last_success_at"),
                now,
                error,
            ),
        )

