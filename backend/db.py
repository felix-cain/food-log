"""SQLite storage for food log entries.

Switched by env var:
- TURSO_URL set: connect to Turso (hosted SQLite) via the libsql client
- Otherwise: open local `food_log.db` via the stdlib sqlite3 module

libsql is only imported when actually needed, so local dev doesn't need it
installed (handy because libsql has no wheel for Python 3.14 yet).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .vision import MealAnalysis

LOCAL_DB_PATH = Path(__file__).parent.parent / "food_log.db"

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
        import libsql  # imported lazily — only installed in the deploy env

        return libsql.connect(
            database=turso_url,
            auth_token=os.getenv("TURSO_AUTH_TOKEN"),
        )
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
