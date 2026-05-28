"""Claude vision call: food photo -> structured meal entry."""

from __future__ import annotations

import base64
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

_VISION_PROMPT = """You are looking at a photo of food someone ate.

Identify each distinct food item in the image. For each item, give:
- name: a concise description (e.g. "grilled chicken breast", "white rice", "steamed broccoli")
- estimated_grams: best guess at the portion weight in grams
- calories_estimate: rough calorie count for that portion
- confidence: "high" if you can clearly see and identify the item, "medium" if the item is clear but portion is uncertain, "low" if the item itself is ambiguous

Also write a one-line description of the meal as a whole.

Be honest about uncertainty. Estimating portions from photos is hard - it is better to say "low" confidence than to invent numbers you cannot justify."""


class FoodItem(BaseModel):
    name: str = Field(description="Concise name of the food item")
    estimated_grams: float = Field(description="Estimated portion weight in grams")
    calories_estimate: int = Field(description="Estimated calories for this portion")
    confidence: Literal["high", "medium", "low"]


class MealAnalysis(BaseModel):
    description: str = Field(description="One-line summary of the whole meal")
    items: list[FoodItem]


def analyze_meal_photo(
    image_bytes: bytes, media_type: str = "image/jpeg"
) -> MealAnalysis:
    """Send a food photo to Claude and return a structured guess.

    Requires ANTHROPIC_API_KEY in the environment.
    """
    client = anthropic.Anthropic()
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = client.messages.parse(
        model="claude-opus-4-7",
        max_tokens=8192,
        thinking={"type": "adaptive"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }
        ],
        output_format=MealAnalysis,
    )

    return response.parsed_output
