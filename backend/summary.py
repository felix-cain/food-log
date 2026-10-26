"""Summary endpoint: aggregate stats + Claude-written analysis of eating habits.

The capture flow (photo -> /analyze -> /entries) only exists to feed THIS.
The summary is where the user gets actual value: trends, patterns, and
specific suggestions for what to try next.

The period always ends yesterday: today is still being logged, so including
it would drag every average down. Claude's analysis is cached in the DB and
only regenerated when the underlying data changes (new day, new/deleted
meal, profile edit) or the user forces a refresh.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .db import get_cached_analysis, get_profile, list_meals_between, save_cached_analysis
from .targets import Profile, Targets, compute_targets

MAIN_MEALS = ("breakfast", "lunch", "dinner")

logger = logging.getLogger(__name__)


# ============================================================================
# Response schemas
# ============================================================================


class ClaudeAnalysis(BaseModel):
    observations: list[str] = Field(
        description=(
            "3-5 specific patterns you noticed in the meal data. Concrete beats "
            "generic ('weekday breakfasts are coffee-only', 'protein averages "
            "65g/day'). If data is too thin to see patterns, say so."
        ),
    )
    suggestions: list[str] = Field(
        description=(
            "2-3 concrete things to try this week. Specific actions, not "
            "platitudes. Examples: 'add a green vegetable to two dinners', "
            "'log dinners closer to when you eat them'."
        ),
    )


class DailyTotal(BaseModel):
    date: str
    calories: int
    protein_g: float | None
    meal_count: int
    meal_types: list[str]


class QuickNumbers(BaseModel):
    avg_calories_per_day: int
    # Which days the averages cover: fully logged days (breakfast + lunch +
    # dinner) when there are any, otherwise every day with something logged
    avg_basis: Literal["full", "logged", "none"]
    days_logged: int
    full_days: int
    total_meals: int
    days_in_period: int
    avg_protein_g: float | None
    avg_carbs_g: float | None
    avg_fat_g: float | None


class SummaryResponse(BaseModel):
    period_days: int
    period_start: str
    period_end: str
    quick_numbers: QuickNumbers
    # None when the Claude call failed; the stats and charts still render
    analysis: ClaudeAnalysis | None
    analysis_error: str | None
    daily_totals: list[DailyTotal]
    targets: Targets | None


# ============================================================================
# Builder
# ============================================================================


_ANALYSIS_PROMPT = """You are looking at someone's food log for the last {days} days. Below is the data — for each day, the meals they logged with items, portions, calories, and macronutrient estimates (protein/carbs/fat in grams).

Your job: surface what's actually interesting about their eating patterns. Specific observations beat generic ones. Honest beats encouraging — if their data is thin or scattered, say so.

Guidance:
- "observations" should be 3-5 specific patterns. Examples: "weekday breakfasts skipped or coffee-only", "protein averages 65g/day, low for an active person", "vegetables appear in only 2 of 7 dinners", "Wednesday is consistently your highest-calorie day". Avoid platitudes. If you can't see real patterns, that's a valid observation ("only 4 of 7 days logged — not enough to see trends yet").
- "suggestions" should be 2-3 concrete actions to try this coming week. Specific, not "eat healthier". Examples: "add a green vegetable to two dinners", "swap one ultra-processed snack for fruit", "log meals when you eat them, not retroactively".
- Where a meal includes "their own words", that's what the person said they ate. Treat it as the most reliable part of the record.
- Days marked "(partial)" are missing at least one of breakfast, lunch or dinner. Their totals are probably incomplete — don't read a low-calorie partial day as under-eating. Days marked "(nothing logged)" are unknown, not fasting.

Don't invent patterns the data doesn't support. Treat the user as someone who wants honest feedback, not encouragement.
{context}
Data:
{data}"""

_GOAL_TEXT = {"lose": "lose weight", "maintain": "maintain weight", "gain": "gain weight"}
_ACTIVITY_TEXT = {
    "sedentary": "sedentary",
    "light": "lightly active (exercise 1-3 days/week)",
    "active": "active (exercise 3-5 days/week)",
    "very_active": "very active (exercise 6-7 days/week)",
}


def _format_context(profile: Profile | None, targets: Targets | None) -> str:
    if not profile or not targets:
        return ""
    return (
        f"\nTheir goal is to {_GOAL_TEXT[profile.goal]}; activity level: "
        f"{_ACTIVITY_TEXT[profile.activity]}. Estimated daily targets: "
        f"~{targets.calories} cal ({targets.calories_low}-{targets.calories_high}), "
        f"~{targets.protein_g}g protein. Compare against these where the data allows.\n"
    )


def _format_meal_data(daily_meals: dict[str, list[dict]]) -> str:
    """Format the meals dictionary as readable text for the Claude prompt."""
    lines = []
    for day, meals in daily_meals.items():
        if not meals:
            lines.append(f"{day}: (nothing logged)")
            continue
        partial = "" if _is_full_day(meals) else " (partial)"
        lines.append(f"\n{day}{partial}:")
        for meal in meals:
            items_text = ", ".join(
                _format_item(i) for i in meal["items"]
            )
            note = f' (their own words: "{meal["note"]}")' if meal.get("note") else ""
            lines.append(f"  {meal['meal_type']}: {items_text}{note}")
    return "\n".join(lines)


def _format_item(item: dict) -> str:
    """e.g. 'grilled chicken (130g, 215cal, 40p/0c/5f)'."""
    macros = []
    for key, label in (("protein_g", "p"), ("carbs_g", "c"), ("fat_g", "f")):
        v = item.get(key)
        macros.append(f"{round(v)}{label}" if v is not None else f"?{label}")
    macro_str = "/".join(macros)
    return (
        f"{item['name']} ({int(item['grams'])}g, "
        f"{item['calories']}cal, {macro_str})"
    )


def _is_full_day(meals: list[dict]) -> bool:
    types = {m["meal_type"] for m in meals}
    return all(t in types for t in MAIN_MEALS)


def _fingerprint(start: str, end: str, meals: list[dict], profile: dict | None) -> str:
    """Changes whenever the analysis input changes, so the cache invalidates."""
    parts = [start, end, profile["updated_at"] if profile else "no-profile"]
    parts += [f"{m['id']}:{len(m['items'])}:{m.get('updated_at') or ''}" for m in meals]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def build_summary(days: int = 7, today: date | None = None, refresh: bool = False) -> SummaryResponse:
    end = (today or date.today()) - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    meals = list_meals_between(dates[0], dates[-1])
    daily_meals: dict[str, list[dict]] = {d: [] for d in dates}
    for meal in meals:
        daily_meals[meal["date"]].append(meal)

    profile_row = get_profile()
    profile = Profile(**profile_row) if profile_row else None
    targets = compute_targets(profile) if profile else None

    # Per-day totals
    daily_totals: list[DailyTotal] = []
    for day, day_meals in daily_meals.items():
        items = [i for m in day_meals for i in m["items"]]
        has_macros = any(i.get("protein_g") is not None for i in items)
        daily_totals.append(
            DailyTotal(
                date=day,
                calories=sum(i["calories"] for i in items),
                protein_g=round(sum(i.get("protein_g") or 0 for i in items), 1) if has_macros else None,
                meal_count=len(day_meals),
                meal_types=sorted({m["meal_type"] for m in day_meals}),
            )
        )

    # Averages over fully logged days where possible — partial days understate intake
    logged_days = [d for d in dates if daily_meals[d]]
    full_days = [d for d in logged_days if _is_full_day(daily_meals[d])]
    basis_days = full_days or logged_days
    avg_basis = "full" if full_days else ("logged" if logged_days else "none")

    basis_items = [i for d in basis_days for m in daily_meals[d] for i in m["items"]]
    macro_items = [i for i in basis_items if i.get("protein_g") is not None]
    n = len(basis_days)

    def avg_macro(key: str) -> float | None:
        if not macro_items:
            return None
        return round(sum(i.get(key) or 0 for i in macro_items) / n, 1)

    quick = QuickNumbers(
        avg_calories_per_day=round(sum(i["calories"] for i in basis_items) / n) if n else 0,
        avg_basis=avg_basis,
        days_logged=len(logged_days),
        full_days=len(full_days),
        total_meals=len(meals),
        days_in_period=days,
        avg_protein_g=avg_macro("protein_g"),
        avg_carbs_g=avg_macro("carbs_g"),
        avg_fat_g=avg_macro("fat_g"),
    )

    # Claude analysis, cached until the data changes. Failures are reported
    # alongside the stats rather than failing the whole request.
    fingerprint = _fingerprint(dates[0], dates[-1], meals, profile_row)
    cached = None if refresh else get_cached_analysis(days, fingerprint)
    analysis: ClaudeAnalysis | None = None
    analysis_error: str | None = None
    if cached:
        analysis = ClaudeAnalysis.model_validate_json(cached)
    else:
        prompt = _ANALYSIS_PROMPT.format(
            days=days,
            context=_format_context(profile, targets),
            data=_format_meal_data(daily_meals),
        )
        try:
            analysis = _run_analysis(prompt)
            save_cached_analysis(days, fingerprint, analysis.model_dump_json())
        except anthropic.AuthenticationError:
            analysis_error = "Claude rejected the API key. Check ANTHROPIC_API_KEY."
        except anthropic.RateLimitError:
            analysis_error = "Claude is rate-limiting requests. Try again in a minute."
        except anthropic.APIStatusError as e:
            analysis_error = f"Claude API error ({e.status_code}). Try again shortly."
        except anthropic.APIConnectionError:
            analysis_error = "Couldn't reach Claude. Check the connection and try again."
        except Exception as e:
            logger.exception("Summary analysis failed")
            analysis_error = f"Analysis failed: {e}"

    return SummaryResponse(
        period_days=days,
        period_start=dates[0],
        period_end=dates[-1],
        quick_numbers=quick,
        analysis=analysis,
        analysis_error=analysis_error,
        daily_totals=daily_totals,
        targets=targets,
    )


def _run_analysis(prompt: str) -> ClaudeAnalysis:
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model="claude-opus-5-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        # Opus 5.5 defaults to medium; high matches what Opus 4.7 ran at
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
        output_format=ClaudeAnalysis,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to analyse this data")
    if response.parsed_output is None:
        raise RuntimeError(f"Claude returned no analysis (stop reason: {response.stop_reason})")
    return response.parsed_output
