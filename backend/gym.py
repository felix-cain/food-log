"""Claude call: screenshots of a gym class's programming -> structured session.

The gym's app (WodBoard-based) has no export, so the way in is a screenshot of
the class's Programming page. That page says what was *programmed*, not what
the person did, so it's stored as the plan; what they actually did goes in
their own notes, which the summary analysis treats as the reliable part.
"""

from __future__ import annotations

import base64
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

_PROMPT = """These are screenshots of one gym class's Programming page from the gym's app. They may be consecutive scrolls of the same page, so don't repeat anything that appears in more than one.

Extract:
- class_name: the class title from the page header (e.g. "Delta Strength", "Hyrox Conditioning")
- summary: one short line saying what the session is (e.g. "Lower-body strength with accessory work")
- blocks: each distinct part of the session in order, such as a strength block, a WOD or conditioning piece, an accessory block, or a warm-up

For each block give:
- kind: "strength" (loaded lifts for reps and sets), "conditioning" (a WOD, intervals, EMOM, AMRAP, station work, running or rowing), "accessory" (smaller supporting work), "warmup" or "other"
- title: the block's own title if it has one (e.g. "Honesty Ladder", "Station Focused"), otherwise null
- format: how it is structured in a few words (e.g. "5 rounds ladder", "AMRAP 6 min", "teams of 3, 2 rounds per station"), or null
- movements: the movements in the block, one entry each, with their reps, distance or time as written (e.g. "8 chin ups", "400m run")
- scheme: anything that doesn't fit the movements and applies to the whole block: sets, time caps, rest, scaling levels. Keep scaling levels in the person's words (e.g. "Start 4/6/8/10/12, Int 6/8/10/12/14"). Null if there is nothing

Copy what the page says; don't invent weights, loads or levels that aren't there. Ignore the "Programming notes" section and anything about videos, bookings or the phone's status bar. If a screenshot isn't a gym programming page, return an empty list of blocks and say so in the summary."""


class GymBlock(BaseModel):
    kind: Literal["strength", "conditioning", "accessory", "warmup", "other"]
    title: str | None = Field(default=None, description="The block's own title, if it has one")
    format: str | None = Field(default=None, description="How the block is structured, in a few words")
    movements: list[str] = Field(description="Movements with their reps, distance or time as written")
    scheme: str | None = Field(default=None, description="Sets, time caps, rest or scaling levels for the block")


class GymProgramme(BaseModel):
    class_name: str = Field(description="The class title from the page header")
    summary: str = Field(description="One short line on what the session is")
    blocks: list[GymBlock]


def analyze_programme(images: list[tuple[bytes, str]]) -> GymProgramme:
    """Return Claude's structured reading of one or more programming screenshots.

    `images` is a list of (bytes, media type). Requires ANTHROPIC_API_KEY.
    """
    if not images:
        raise ValueError("Need at least one screenshot")

    content: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("utf-8"),
            },
        }
        for data, media_type in images
    ]
    content.append({"type": "text", "text": _PROMPT})

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model="claude-opus-5-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": content}],
        output_format=GymProgramme,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to read these screenshots")
    if response.parsed_output is None:
        raise RuntimeError(f"Claude returned no reading (stop reason: {response.stop_reason})")
    return response.parsed_output
