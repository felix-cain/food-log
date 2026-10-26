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

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .vision import MealAnalysis

LOCAL_DB_PATH = Path(__file__).parent.parent / "food_log.db"


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
    created_at TEXT NOT NULL
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
    fat_g REAL
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

CREATE TABLE IF NOT EXISTS summary_cache (
    period_days INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added after the initial schema. Run ADD COLUMN idempotently on
# init so existing DBs (local sqlite from earlier sessions, Turso from the
# first deploy) get the new columns without manual migration.
_MIGRATION_COLUMNS = (
    ("meal_items", "protein_g", "REAL"),
    ("meal_items", "carbs_g", "REAL"),
    ("meal_items", "fat_g", "REAL"),
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


def save_meal(date: str, meal_type: str, analysis: MealAnalysis) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO meals (date, meal_type, description, created_at) "
            "VALUES (?, ?, ?, ?)",
            (date, meal_type, analysis.description, created_at),
        )
        meal_id = cursor.lastrowid
        for item in analysis.items:
            conn.execute(
                "INSERT INTO meal_items "
                "(meal_id, name, grams, calories, confidence, protein_g, carbs_g, fat_g) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    meal_id,
                    item.name,
                    float(item.estimated_grams),
                    int(item.calories_estimate),
                    item.confidence,
                    float(item.protein_g),
                    float(item.carbs_g),
                    float(item.fat_g),
                ),
            )
        return meal_id


def list_meals_for_date(date: str) -> list[dict]:
    return list_meals_between(date, date)


def list_meals_between(start: str, end: str) -> list[dict]:
    """All meals with start <= date <= end (inclusive), oldest first.

    Two queries regardless of range size — matters on the Turso path where
    each query is an HTTP round trip.
    """
    with _connect() as conn:
        meal_rows = conn.execute(
            "SELECT id, date, meal_type, description, created_at "
            "FROM meals WHERE date BETWEEN ? AND ? ORDER BY date, created_at",
            (start, end),
        ).fetchall()
        item_rows = conn.execute(
            "SELECT mi.meal_id, mi.name, mi.grams, mi.calories, mi.confidence, "
            "mi.protein_g, mi.carbs_g, mi.fat_g "
            "FROM meal_items mi JOIN meals m ON m.id = mi.meal_id "
            "WHERE m.date BETWEEN ? AND ? ORDER BY mi.id",
            (start, end),
        ).fetchall()

    items_by_meal: dict[int, list[dict]] = {}
    for i in item_rows:
        items_by_meal.setdefault(i[0], []).append(
            {
                "name": i[1],
                "grams": i[2],
                "calories": i[3],
                "confidence": i[4],
                "protein_g": i[5],
                "carbs_g": i[6],
                "fat_g": i[7],
            }
        )
    return [
        {
            "id": row[0],
            "date": row[1],
            "meal_type": row[2],
            "description": row[3],
            "created_at": row[4],
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
