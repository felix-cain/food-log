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
from .health import format_for_analysis, recent_active_kcal_avg
from .nutrition import FoodQuality, food_quality
from .nutrition import format_for_analysis as format_quality
from .targets import Profile, Targets, compute_targets

MAIN_MEALS = ("breakfast", "lunch", "dinner")

logger = logging.getLogger(__name__)


# ============================================================================
# Response schemas
# ============================================================================


class ClaudeAnalysis(BaseModel):
    observations: list[str] = Field(
        description=(
            "3-5 specific patterns in what they eat. Concrete beats generic "
            "('most of your protein comes from chicken', 'brassicas appear once "
            "in 7 days', 'fibre averages 18g against 30g'). If data is too thin "
            "to see patterns, say so."
        ),
    )
    suggestions: list[str] = Field(
        description=(
            "2-3 specific food-level things to try this week, grounded in common "
            "nutrition guidelines. Name foods and swaps, e.g. 'add broccoli or "
            "cabbage to three dinners', 'swap one chicken lunch for lentils'."
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
    food_quality: FoodQuality


# ============================================================================
# Builder
# ============================================================================


# Bump when the prompt changes meaningfully, so cached analyses written with
# the old prompt aren't reused
PROMPT_VERSION = "2-food-quality"

_ANALYSIS_PROMPT = """You are looking at someone's food log for the last {days} days. For each day there are the meals they logged, with each item's portion, calories and macros (protein/carbs/fat), plus where available its food group, how processed it is, fibre and the plant foods in it. Above the log there are food-quality measures already worked out from it; those numbers are exact, so use them rather than re-adding the log.

What they want from you: insight into the food they eat and how to improve it, not calorie counting. Look at what their diet is made of: which foods and food groups dominate, where their protein comes from, how much fibre, fruit, vegetables and plant variety they get, how much is ultra-processed, oily fish, red and processed meat, sugar and salt, and how these vary across meals and days. Calories and their targets matter only as context.

Judge against common nutrition guidelines (UK NHS and SACN guidance unless something else is clearly more relevant): 30g fibre a day, 5 portions of fruit and veg a day, oily fish at least once a week, no more than 70g red and processed meat a day, no more than 30g free sugars and 6g salt a day, saturated fat under 30g (men) or 20g (women). Thirty different plants a week is a popular, less official target for variety. Beyond those guidelines there are no restrictions on what you can suggest.

Guidance:
- "observations" should be 3-5 specific food patterns. Examples: "most of your protein comes from chicken and dairy; beans and fish barely feature", "brassicas (broccoli, cabbage, kale) appeared once in 7 days", "fibre averages 18g a day, well under 30g, and breakfast contributes almost none", "your lunches are consistently ultra-processed". If you can't see real patterns, say so ("only 4 of 7 days logged — not enough to see trends yet").
- "suggestions" should be 2-3 specific, food-level things to try this coming week that would close the biggest gaps. Name foods, swaps and where they'd fit, e.g. "add a handful of frozen peas or spinach to your usual pasta dinner", "swap two chicken lunches for chickpeas or lentils to diversify protein and add fibre", "have mackerel or salmon once this week". Not "eat healthier".
- Where a meal includes "their own words", that's what the person said they ate. Treat it as the most reliable part of the record.
- Some older meals don't have food-quality detail; the measures note how many meals they cover. Don't treat missing detail as zero.
- Days marked "(partial)" are missing at least one of breakfast, lunch or dinner. Their totals are probably incomplete — don't read a low-calorie partial day as under-eating. Days marked "(nothing logged)" are unknown, not fasting.

- If sleep and activity data from their watch is included, look for links with eating where the data supports them: e.g. short or poor sleep followed by higher-calorie or more snack-heavy days, intake on training days versus rest days, protein after hard sessions. "Night before" on a date is the sleep that ended that morning. Days without watch data are unknown, not zero.

Don't invent patterns the data doesn't support. Treat the user as someone who wants honest feedback, not encouragement.
{context}
{quality}
Data:
{data}{health}"""

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
    basis = (
        f" (calories based on their watch's measured activity, ~{targets.active_kcal_avg} active kcal/day)"
        if targets.basis == "garmin" else ""
    )
    return (
        f"\nTheir goal is to {_GOAL_TEXT[profile.goal]}; activity level: "
        f"{_ACTIVITY_TEXT[profile.activity]}. Estimated daily targets: "
        f"~{targets.calories} cal ({targets.calories_low}-{targets.calories_high}), "
        f"~{targets.protein_g}g protein{basis}. Compare against these where the data allows.\n"
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
    """e.g. 'grilled chicken (130g, 215cal, 40p/0c/5f, poultry, whole, fibre 0g)'."""
    macros = []
    for key, label in (("protein_g", "p"), ("carbs_g", "c"), ("fat_g", "f")):
        v = item.get(key)
        macros.append(f"{round(v)}{label}" if v is not None else f"?{label}")
    parts = [f"{int(item['grams'])}g", f"{item['calories']}cal", "/".join(macros)]
    if item.get("food_group"):
        parts += [item["food_group"], item.get("processing") or "?", f"fibre {round(item.get('fibre_g') or 0)}g"]
    return f"{item['name']} ({', '.join(parts)})"


def _is_full_day(meals: list[dict]) -> bool:
    types = {m["meal_type"] for m in meals}
    return all(t in types for t in MAIN_MEALS)


def _fingerprint(start: str, end: str, meals: list[dict], profile: dict | None, health: str) -> str:
    """Changes whenever the analysis input changes, so the cache invalidates."""
    # health is the formatted watch data, so re-syncing unchanged data keeps the cache
    parts = [PROMPT_VERSION, start, end, profile["updated_at"] if profile else "no-profile", health]
    parts += [f"{m['id']}:{len(m['items'])}:{m.get('updated_at') or ''}" for m in meals]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def build_summary(days: int = 7, today: date | None = None, refresh: bool = False) -> SummaryResponse:
    today = today or date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    meals = list_meals_between(dates[0], dates[-1])
    daily_meals: dict[str, list[dict]] = {d: [] for d in dates}
    for meal in meals:
        daily_meals[meal["date"]].append(meal)

    profile_row = get_profile()
    profile = Profile(**profile_row) if profile_row else None
    targets = compute_targets(profile, recent_active_kcal_avg(today)) if profile else None
    health_text = format_for_analysis(dates)

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
    quality = food_quality(daily_meals, basis_days, profile.sex if profile else None)

    # Claude analysis, cached until the data changes. Failures are reported
    # alongside the stats rather than failing the whole request.
    fingerprint = _fingerprint(dates[0], dates[-1], meals, profile_row, health_text)
    cached = None if refresh else get_cached_analysis(days, fingerprint)
    analysis: ClaudeAnalysis | None = None
    analysis_error: str | None = None
    if cached:
        analysis = ClaudeAnalysis.model_validate_json(cached)
    else:
        prompt = _ANALYSIS_PROMPT.format(
            days=days,
            context=_format_context(profile, targets),
            quality=format_quality(quality),
            data=_format_meal_data(daily_meals),
            health=f"\n\nSleep and activity from their watch:\n{health_text}" if health_text else "",
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
        food_quality=quality,
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
