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
    confidence TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);
"""


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
                "INSERT INTO meal_items (meal_id, name, grams, calories, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    meal_id,
                    item.name,
                    float(item.estimated_grams),
                    int(item.calories_estimate),
                    item.confidence,
                ),
            )
        return meal_id


def list_meals_for_date(date: str) -> list[dict]:
    with _connect() as conn:
        meal_rows = conn.execute(
            "SELECT id, date, meal_type, description, created_at "
            "FROM meals WHERE date = ? ORDER BY created_at",
            (date,),
        ).fetchall()
        result = []
        for row in meal_rows:
            meal_id = row[0]
            item_rows = conn.execute(
                "SELECT name, grams, calories, confidence FROM meal_items "
                "WHERE meal_id = ? ORDER BY id",
                (meal_id,),
            ).fetchall()
            result.append(
                {
                    "id": meal_id,
                    "date": row[1],
                    "meal_type": row[2],
                    "description": row[3],
                    "created_at": row[4],
                    "items": [
                        {
                            "name": i[0],
                            "grams": i[1],
                            "calories": i[2],
                            "confidence": i[3],
                        }
                        for i in item_rows
                    ],
                }
            )
        return result


def delete_meal(meal_id: int) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        return cursor.rowcount > 0
