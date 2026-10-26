"""Daily calorie and protein targets from the user's profile.

Calories: Mifflin-St Jeor BMR x activity factor, adjusted for goal.
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


def _round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


def compute_targets(p: Profile) -> Targets:
    bmr = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age
    bmr += 5 if p.sex == "male" else -161
    factor, protein_per_kg = _ACTIVITY[p.activity]
    calories = bmr * factor * _GOAL_MULTIPLIER[p.goal]
    return Targets(
        calories=_round_to(calories, 50),
        calories_low=_round_to(calories * (1 - _BAND), 50),
        calories_high=_round_to(calories * (1 + _BAND), 50),
        protein_g=_round_to(p.weight_kg * protein_per_kg, 5),
    )
