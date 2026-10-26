"""Claude call: food photo and/or the user's description -> structured meal entry.

Three ways in, one output shape:
- photo only: identify what's visible
- description only: the user typed or dictated what they ate (no photo)
- correction: a photo or an earlier estimate, plus the user's note on what
  to change; the note wins where they conflict
"""

from __future__ import annotations

import base64
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

_ITEM_FIELDS = """For each item, give:
- name: a concise description (e.g. "grilled chicken breast", "white rice", "steamed broccoli")
- estimated_grams: best guess at the portion weight in grams
- calories_estimate: rough calorie count for that portion
- protein_g: estimated grams of protein in that portion
- carbs_g: estimated grams of carbohydrates in that portion
- fat_g: estimated grams of fat in that portion"""

_PHOTO_PROMPT = f"""You are looking at a photo of food someone ate.

Identify each distinct food item in the image. {_ITEM_FIELDS}
- confidence: "high" if you can clearly see and identify the item, "medium" if the item is clear but portion is uncertain, "low" if the item itself is ambiguous

Also write a one-line description of the meal as a whole.

Be honest about uncertainty. Estimating portions from photos is hard - it is better to say "low" confidence than to invent numbers you cannot justify. Macro estimates should be reasonable defaults for typical preparations; don't pretend high precision."""

_TEXT_PROMPT = f"""Someone has described a meal they ate in their own words. It may be typed or dictated, so expect informal phrasing and the odd transcription slip.

Identify each distinct food item they mention. {_ITEM_FIELDS}
- confidence: "high" if both the food and the amount are clear from what they said, "medium" if the food is clear but you assumed the amount, "low" if the food itself is vague

Where they don't give an amount, assume a typical adult portion. Also write a one-line description of the meal as a whole.

Macro estimates should be reasonable defaults for typical preparations; don't pretend high precision."""


class FoodItem(BaseModel):
    name: str = Field(description="Concise name of the food item")
    estimated_grams: float = Field(description="Estimated portion weight in grams")
    calories_estimate: int = Field(description="Estimated calories for this portion")
    protein_g: float = Field(description="Estimated grams of protein for this portion")
    carbs_g: float = Field(description="Estimated grams of carbohydrates for this portion")
    fat_g: float = Field(description="Estimated grams of fat for this portion")
    confidence: Literal["high", "medium", "low"]


class MealAnalysis(BaseModel):
    description: str = Field(description="One-line summary of the whole meal")
    items: list[FoodItem]


def _build_prompt(has_image: bool, note: str | None, previous: str | None) -> str:
    parts = [_PHOTO_PROMPT if has_image else _TEXT_PROMPT]
    if previous:
        parts.append(f"An earlier estimate of this meal was:\n{previous}")
    if note and (has_image or previous):
        source = "the photo and the earlier estimate" if has_image and previous else (
            "the photo" if has_image else "the earlier estimate"
        )
        parts.append(
            f"The person added this about the meal. It takes priority over {source} "
            f'where they conflict:\n"{note}"'
        )
    elif note:
        parts.append(f'Their description:\n"{note}"')
    if previous:
        parts.append(
            "Return the full revised list of items, not just the changes. Keep "
            "anything from the earlier estimate that their note doesn't contradict."
        )
    return "\n\n".join(parts)


def analyze_meal(
    image_bytes: bytes | None = None,
    media_type: str = "image/jpeg",
    note: str | None = None,
    previous: str | None = None,
) -> MealAnalysis:
    """Return Claude's structured guess at a meal from a photo, a description, or both.

    `previous` is an earlier estimate (JSON text) being corrected by `note`.
    Requires ANTHROPIC_API_KEY in the environment.
    """
    if image_bytes is None and not note:
        raise ValueError("Need a photo or a description")

    content: list[dict] = []
    if image_bytes is not None:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
                },
            }
        )
    content.append({"type": "text", "text": _build_prompt(image_bytes is not None, note, previous)})

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model="claude-opus-5-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        # Opus 5.5 defaults to medium; high matches what Opus 4.7 ran at
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": content}],
        output_format=MealAnalysis,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to analyse this meal")
    if response.parsed_output is None:
        raise RuntimeError(f"Claude returned no analysis (stop reason: {response.stop_reason})")
    return response.parsed_output
