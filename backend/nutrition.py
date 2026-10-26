"""Food-quality measures for the Summary, compared with common UK guidelines.

Computed here rather than left to Claude so the numbers are exact; Claude
gets them as facts and writes the insights.

Guidelines (adults):
- fibre: at least 30g a day (SACN, 2015)
- fruit and veg: at least 5 x 80g portions a day (NHS 5 A Day)
- plant variety: 30 different plants a week (widely cited from the American
  Gut Project; not an official UK guideline)
- oily fish: at least 1 portion a week (NHS)
- red and processed meat: no more than 70g a day (NHS)
- free sugars: no more than 30g a day (SACN)
- salt: no more than 6g a day (NHS)
- saturated fat: no more than 30g a day for men, 20g for women (NHS)

Only items carrying the detail count; meals logged before it existed are
reported as not covered rather than counted as zero.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Literal

from pydantic import BaseModel

GROUP_LABELS = {
    "vegetables": "Vegetables", "fruit": "Fruit", "legumes": "Beans, lentils and peas",
    "whole_grains": "Whole grains", "refined_grains": "Refined grains", "potatoes": "Potatoes",
    "nuts_seeds": "Nuts and seeds", "dairy": "Dairy", "eggs": "Eggs", "poultry": "Poultry",
    "red_meat": "Red meat", "processed_meat": "Processed meat", "fish": "White fish and seafood",
    "oily_fish": "Oily fish", "plant_protein": "Tofu and other plant protein", "mixed_dish": "Mixed dishes",
    "sweets_snacks": "Sweets and snacks", "sugary_drinks": "Sugary drinks", "alcohol": "Alcohol",
    "other_drinks": "Other drinks", "fats_oils_sauces": "Fats, oils and sauces", "other": "Other",
}


class Measure(BaseModel):
    key: str
    label: str
    value: float
    unit: str
    guideline: float
    direction: Literal["at_least", "at_most"]
    per: Literal["day", "week"]


class ProteinSource(BaseModel):
    group: str
    label: str
    share: float  # percent of protein from items with detail


class FrequentFood(BaseModel):
    name: str
    count: int


class FoodQuality(BaseModel):
    meals_with_detail: int
    meals_total: int
    days_counted: int  # days the per-day averages are taken over
    measures: list[Measure]
    upf_calorie_share: float | None  # percent of calories from ultra-processed items
    distinct_plants: int  # different plants across the whole period
    protein_sources: list[ProteinSource]
    frequent_foods: list[FrequentFood]


def _has_detail(item: dict) -> bool:
    return item.get("food_group") is not None


def food_quality(daily_meals: dict[str, list[dict]], basis_days: list[str], sex: str | None) -> FoodQuality:
    """`daily_meals` maps every date in the period to its meals (oldest first);
    per-day averages use `basis_days` (fully logged days where possible)."""
    meals = [m for day in daily_meals.values() for m in day]
    detailed_meals = [m for m in meals if any(_has_detail(i) for i in m["items"])]
    items = [i for m in detailed_meals for i in m["items"] if _has_detail(i)]
    empty = FoodQuality(
        meals_with_detail=0, meals_total=len(meals), days_counted=0, measures=[],
        upf_calorie_share=None, distinct_plants=0, protein_sources=[], frequent_foods=[],
    )
    if not items:
        return empty

    def day_items(day: str) -> list[dict]:
        return [i for m in daily_meals[day] for i in m["items"] if _has_detail(i)]

    days = [d for d in basis_days if day_items(d)]
    detail_days = [d for d in daily_meals if day_items(d)]
    n = len(days)

    def per_day(fn) -> float:
        return round(sum(fn(i) for d in days for i in day_items(d)) / n, 1) if n else 0.0

    def amount(key: str):
        return lambda i: i.get(key) or 0

    red_meat = lambda i: (i.get("grams") or 0) if i["food_group"] in ("red_meat", "processed_meat") else 0  # noqa: E731
    oily_fish_meals = sum(1 for m in detailed_meals if any(i.get("food_group") == "oily_fish" for i in m["items"]))
    sat_fat_limit = 20 if sex == "female" else 30

    measures = [
        Measure(key="fibre", label="Fibre", value=per_day(amount("fibre_g")), unit="g",
                guideline=30, direction="at_least", per="day"),
        Measure(key="fruit_veg", label="Fruit and veg", value=per_day(amount("fruit_veg_portions")),
                unit=" portions", guideline=5, direction="at_least", per="day"),
        Measure(key="plants", label="Different plants", value=_plants_per_week(daily_meals),
                unit="", guideline=30, direction="at_least", per="week"),
        Measure(key="oily_fish", label="Oily fish", value=round(oily_fish_meals * 7 / len(detail_days), 1),
                unit=" portions", guideline=1, direction="at_least", per="week"),
        Measure(key="red_meat", label="Red and processed meat", value=per_day(red_meat),
                unit="g", guideline=70, direction="at_most", per="day"),
        Measure(key="free_sugar", label="Free sugars", value=per_day(amount("free_sugar_g")),
                unit="g", guideline=30, direction="at_most", per="day"),
        Measure(key="salt", label="Salt", value=per_day(amount("salt_g")),
                unit="g", guideline=6, direction="at_most", per="day"),
        Measure(key="sat_fat", label="Saturated fat", value=per_day(amount("sat_fat_g")),
                unit="g", guideline=sat_fat_limit, direction="at_most", per="day"),
    ]

    total_cal = sum(i.get("calories") or 0 for i in items)
    upf_cal = sum(i.get("calories") or 0 for i in items if i.get("processing") == "ultra_processed")

    protein: dict[str, float] = defaultdict(float)
    for i in items:
        protein[i["food_group"]] += i.get("protein_g") or 0
    total_protein = sum(protein.values())
    sources = [
        ProteinSource(group=g, label=GROUP_LABELS.get(g, g), share=round(100 * p / total_protein))
        for g, p in sorted(protein.items(), key=lambda kv: -kv[1])
        if total_protein and p / total_protein >= 0.05
    ][:6]

    names = Counter(i["name"].strip().lower() for m in meals for i in m["items"])
    frequent = [FrequentFood(name=name, count=c) for name, c in names.most_common(8) if c >= 2]

    return FoodQuality(
        meals_with_detail=len(detailed_meals),
        meals_total=len(meals),
        days_counted=n,
        measures=measures,
        upf_calorie_share=round(100 * upf_cal / total_cal) if total_cal else None,
        distinct_plants=len(_plants(i for i in items)),
        protein_sources=sources,
        frequent_foods=frequent,
    )


def _plants(items) -> set[str]:
    return {p.strip().lower() for i in items for p in (i.get("plants") or []) if p.strip()}


def _plants_per_week(daily_meals: dict[str, list[dict]]) -> float:
    """Different plants per 7-day block of the period, averaged over blocks with data."""
    days = list(daily_meals)
    counts = []
    for start in range(0, len(days), 7):
        block = [i for d in days[start:start + 7] for m in daily_meals[d] for i in m["items"] if _has_detail(i)]
        if block:
            counts.append(len(_plants(block)))
    return round(sum(counts) / len(counts), 1) if counts else 0.0


def format_for_analysis(fq: FoodQuality) -> str:
    """The measures as plain lines for the summary prompt ('' if no detail)."""
    if not fq.measures:
        return ""
    lines = [f"Based on {fq.meals_with_detail} of {fq.meals_total} meals that have food-quality detail:"]
    for m in fq.measures:
        word = "at least" if m.direction == "at_least" else "at most"
        lines.append(f"- {m.label}: {m.value:g}{m.unit} per {m.per} (guideline: {word} {m.guideline:g}{m.unit})")
    if fq.upf_calorie_share is not None:
        lines.append(f"- Ultra-processed foods: {fq.upf_calorie_share}% of calories")
    lines.append(f"- Different plants across the whole period: {fq.distinct_plants}")
    if fq.protein_sources:
        lines.append("- Protein sources: " + ", ".join(f"{s.label} {s.share:g}%" for s in fq.protein_sources))
    if fq.frequent_foods:
        lines.append("- Most frequent foods: " + ", ".join(f"{f.name} x{f.count}" for f in fq.frequent_foods))
    return "\n".join(lines)
