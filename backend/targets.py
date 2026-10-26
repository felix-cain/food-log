"""Daily calorie and protein targets from the user's profile.

Calories: Mifflin-St Jeor BMR x activity factor, adjusted for goal. When the
watch has measured activity, the fixed factor is replaced by a light base
factor plus a damped share of measured active calories (7-day average), so
activity isn't counted twice and wrist-based calorie error is softened.
Protein: grams per kg of bodyweight, scaled by activity level.

These are population-level estimates, good enough to give the summary
charts a reference line, not a prescription.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Activity = Literal["sedentary", "light", "active", "very_active"]
Goal = Literal["lose", "maintain", "gain"]

# activity -> (BMR multiplier, protein g/kg)
_ACTIVITY = {
    "sedentary": (1.2, 1.2),
    "light": (1.375, 1.4),
    "active": (1.55, 1.6),
    "very_active": (1.725, 1.8),
}

_GOAL_MULTIPLIER = {"lose": 0.85, "maintain": 1.0, "gain": 1.1}

# Target band either side of the calorie target
_BAND = 0.10

# With measured activity: BMR x base factor + this share of active kcal.
# Wrist-worn energy estimates commonly run 20%+ out, hence < 1.
_MEASURED_BASE_FACTOR = 1.2
_ACTIVE_KCAL_SHARE = 0.6


class Profile(BaseModel):
    age: int = Field(ge=14, le=100)
    sex: Literal["male", "female"]
    height_cm: float = Field(ge=120, le=230)
    weight_kg: float = Field(ge=30, le=300)
    activity: Activity
    goal: Goal


class Targets(BaseModel):
    calories: int
    calories_low: int
    calories_high: int
    protein_g: int
    # "garmin" when the calorie target uses measured activity
    basis: Literal["profile", "garmin"] = "profile"
    active_kcal_avg: int | None = None


def _round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


def compute_targets(p: Profile, active_kcal_avg: float | None = None) -> Targets:
    """Targets from the profile; `active_kcal_avg` (measured, 7-day) refines calories."""
    bmr = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age
    bmr += 5 if p.sex == "male" else -161
    factor, protein_per_kg = _ACTIVITY[p.activity]
    if active_kcal_avg is not None:
        maintenance = bmr * _MEASURED_BASE_FACTOR + _ACTIVE_KCAL_SHARE * active_kcal_avg
    else:
        maintenance = bmr * factor
    calories = maintenance * _GOAL_MULTIPLIER[p.goal]
    return Targets(
        calories=_round_to(calories, 50),
        calories_low=_round_to(calories * (1 - _BAND), 50),
        calories_high=_round_to(calories * (1 + _BAND), 50),
        protein_g=_round_to(p.weight_kg * protein_per_kg, 5),
        basis="garmin" if active_kcal_avg is not None else "profile",
        active_kcal_avg=round(active_kcal_avg) if active_kcal_avg is not None else None,
    )
