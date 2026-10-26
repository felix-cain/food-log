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
- fat_g: estimated grams of fat in that portion
- fibre_g, free_sugar_g, sat_fat_g, salt_g: estimated grams in that portion. Free sugars are added sugars plus the sugars in honey, syrups and fruit juice, not the sugars naturally in whole fruit or milk
- food_group: where the item's nutrition mainly comes from. For a composite dish use its main ingredient (chicken curry -> "poultry", lentil dahl -> "legumes"); use "mixed_dish" only when nothing dominates. Oily fish is salmon, mackerel, sardines, trout, herring, pilchards, kippers or fresh tuna; other fish is "fish". Ham, bacon, sausages and salami are "processed_meat"
- processing: "whole" (fresh or minimally processed: fruit, veg, plain meat or fish, eggs, milk, plain yoghurt, oats, rice, dried or tinned beans), "processed" (whole foods with salt, sugar or oil added: cheese, tinned fish, bakery bread, home-cooked dishes) or "ultra_processed" (industrial products with additives: packaged snacks and sweets, fizzy drinks, most packaged bread and breakfast cereals, processed meat, ready meals)
- fruit_veg_portions: 80g portions of fruit, vegetables or pulses in the item; fruit juice or smoothie counts as at most 1
- plants: the distinct plant foods in the item, lowercase and singular (e.g. ["broccoli", "brown rice", "chickpea"]). Include fruit, vegetables, whole grains, legumes, nuts and seeds; leave out herbs, spices, oils, refined flour and sugar"""

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


FoodGroup = Literal[
    "vegetables", "fruit", "legumes", "whole_grains", "refined_grains", "potatoes", "nuts_seeds",
    "dairy", "eggs", "poultry", "red_meat", "processed_meat", "fish", "oily_fish", "plant_protein",
    "mixed_dish", "sweets_snacks", "sugary_drinks", "alcohol", "other_drinks", "fats_oils_sauces", "other",
]


class FoodItem(BaseModel):
    name: str = Field(description="Concise name of the food item")
    estimated_grams: float = Field(description="Estimated portion weight in grams")
    calories_estimate: int = Field(description="Estimated calories for this portion")
    protein_g: float = Field(description="Estimated grams of protein for this portion")
    carbs_g: float = Field(description="Estimated grams of carbohydrates for this portion")
    fat_g: float = Field(description="Estimated grams of fat for this portion")
    # Food-quality detail, for trend insights beyond calories and macros
    fibre_g: float = Field(description="Estimated grams of fibre for this portion")
    free_sugar_g: float = Field(description="Estimated grams of free sugars for this portion")
    sat_fat_g: float = Field(description="Estimated grams of saturated fat for this portion")
    salt_g: float = Field(description="Estimated grams of salt for this portion")
    food_group: FoodGroup
    processing: Literal["whole", "processed", "ultra_processed"]
    fruit_veg_portions: float = Field(description="80g portions of fruit, vegetables or pulses")
    plants: list[str] = Field(description="Distinct plant foods in the item, lowercase singular")
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
