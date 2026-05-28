"""Parse the three Garmin/manual sheets in health_log.xlsx into clean DataFrames.

Public API:
    load_all(path) -> dict with keys 'food', 'activities', 'sleep'
    refresh_local_copy(source, dest) -> bool — copy OneDrive file if not locked
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_SOURCE = Path(
    r"c:/Users/felix.cain/OneDrive - Newton/Documents/Personal/Etc/health_log.xlsx"
)
DEFAULT_LOCAL = Path(__file__).parent / "health_log.xlsx"

_DURATION_RE = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?", re.IGNORECASE)


def refresh_local_copy(
    source: Path = DEFAULT_SOURCE, dest: Path = DEFAULT_LOCAL
) -> bool:
    """Copy the OneDrive workbook to the project folder. Returns True on success.

    If the source is open in Excel it will be locked; we keep the existing local
    copy and return False rather than raising.
    """
    try:
        shutil.copy2(source, dest)
        return True
    except PermissionError:
        return False


def _to_minutes_from_hhmmss(value) -> float:
    """Parse 'HH:MM:SS' / 'MM:SS' duration strings to minutes. NaN-safe."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    s = str(value).strip()
    if s in {"", "--", "nan", "NaT"}:
        return np.nan
    parts = s.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return np.nan
    if len(parts) == 3:
        h, m, sec = parts
        return h * 60 + m + sec / 60
    if len(parts) == 2:
        m, sec = parts
        return m + sec / 60
    return np.nan


def _to_minutes_from_words(value) -> float:
    """Parse '9h 17min' / '8h 0min' strings to minutes. NaN-safe."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    s = str(value).strip()
    if s in {"", "--", "nan"}:
        return np.nan
    m = _DURATION_RE.fullmatch(s)
    if not m or not any(m.groups()):
        return np.nan
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    return h * 60 + mins


def _to_time_of_day(value):
    """Parse a clock time. Returns pandas Timestamp on a dummy date or NaT."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value
    try:
        return pd.to_datetime(str(value), errors="coerce")
    except Exception:
        return pd.NaT


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """Convert sentinel '--' / blank to NaN, then to float."""
    return pd.to_numeric(series.replace({"--": np.nan, "": np.nan}), errors="coerce")


def load_food(path: Path = DEFAULT_LOCAL) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Food Log", header=2)
    raw = raw.rename(columns={"Water / Drinks": "Drinks"})
    raw["Date"] = pd.to_datetime(raw["Date"], errors="coerce")
    raw = raw.dropna(subset=["Date"]).copy()

    # Empty template rows: all meal columns NaN
    meal_cols = ["Breakfast", "Lunch", "Dinner", "Snacks", "Drinks", "Alcohol"]
    populated = raw[meal_cols].notna().any(axis=1) | raw["Day Rating"].notna()
    food = raw.loc[populated].copy()

    food["Day Rating"] = (
        food["Day Rating"].astype("string").str.strip().str.title()
    )
    rating_order = ["Good", "Mixed", "Poor"]
    food["Day Rating"] = pd.Categorical(
        food["Day Rating"], categories=rating_order, ordered=True
    )

    # Coarse flags
    food["had_alcohol"] = (
        food["Alcohol"].astype("string").str.strip().str.lower().notna()
        & ~food["Alcohol"].astype("string").str.strip().str.lower().isin(
            {"none", "n/a", "na", "no", "0", ""}
        )
    )
    food["skipped_breakfast"] = (
        food["Breakfast"]
        .astype("string")
        .str.strip()
        .str.lower()
        .str.startswith(("n/a", "skipped"))
        .fillna(False)
    )

    return food.reset_index(drop=True)


def load_activities(path: Path = DEFAULT_LOCAL) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Activities Log")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).copy()

    # Numeric coercions for columns that often carry '--'
    numeric_cols = [
        "Distance",
        "Calories",
        "Avg HR",
        "Max HR",
        "Aerobic TE",
        "Training Stress Score®",
        "Steps",
        "Body Battery Drain",
        "Total Ascent",
        "Total Descent",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = _coerce_numeric(df[c])

    df["duration_min"] = df["Time"].apply(_to_minutes_from_hhmmss)
    df["moving_min"] = df["Moving Time"].apply(_to_minutes_from_hhmmss)
    df["avg_pace_min_per_km"] = df["Avg Pace"].apply(_to_minutes_from_hhmmss)
    df["date_only"] = df["Date"].dt.normalize()

    return df.reset_index(drop=True)


def load_sleep(path: Path = DEFAULT_LOCAL) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Sleep Log")
    df = df.rename(columns={"Sleep Score 4 Weeks": "Date"})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).copy()

    for c in ["Score", "Resting Heart Rate", "Body Battery", "Respiration", "HRV Status"]:
        if c in df.columns:
            df[c] = _coerce_numeric(df[c])

    df["duration_min"] = df["Duration"].apply(_to_minutes_from_words)
    df["sleep_need_min"] = df["Sleep Need"].apply(_to_minutes_from_words)
    df["sleep_deficit_min"] = df["sleep_need_min"] - df["duration_min"]

    df["Bedtime"] = df["Bedtime"].apply(_to_time_of_day)
    df["Wake Time"] = df["Wake Time"].apply(_to_time_of_day)

    # Bedtime hour normalized so that evening looks late and post-midnight looks
    # later still. 21:00 -> 21.0, 00:30 -> 24.5, 02:00 -> 26.0.
    def _bedtime_decimal(ts):
        if pd.isna(ts):
            return np.nan
        h = ts.hour + ts.minute / 60
        return h if h >= 12 else h + 24

    df["bedtime_decimal"] = df["Bedtime"].apply(_bedtime_decimal)

    quality_order = ["Poor", "Fair", "Good", "Excellent"]
    quality_clean = (
        df["Quality"].astype("string").str.strip().replace({"--": pd.NA})
    )
    df["Quality"] = pd.Categorical(
        quality_clean, categories=quality_order, ordered=True
    )

    return df.sort_values("Date").reset_index(drop=True)


def load_all(path: Path = DEFAULT_LOCAL) -> dict[str, pd.DataFrame]:
    return {
        "food": load_food(path),
        "activities": load_activities(path),
        "sleep": load_sleep(path),
    }


if __name__ == "__main__":
    data = load_all()
    for name, df in data.items():
        print(f"\n=== {name} ({len(df)} rows) ===")
        print(df.head(3).to_string())
