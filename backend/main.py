"""FastAPI backend for the food-logging PWA.

Endpoints:
    GET  /                 - PWA frontend
    POST /analyze          - Photo and/or description (+ optional earlier estimate
                             to correct) -> Claude's structured guess (no save)
    GET  /entries?date=... - List meal entries for a given YYYY-MM-DD
    POST /entries          - Save an analyzed meal under a date + meal type
    PUT  /entries/{id}     - Replace a saved meal's items after a correction
    DELETE /entries/{id}   - Remove a saved meal
    GET  /profile          - Profile + derived calorie/protein targets (nulls if unset)
    PUT  /profile          - Save the profile
    GET  /summary?days=... - Stats + Claude analysis for the N days ending yesterday
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .auth import require_token
from .db import (
    delete_meal,
    get_profile,
    init_db,
    list_meals_for_date,
    save_meal,
    save_profile,
    update_meal,
)
from .summary import SummaryResponse, build_summary
from .targets import Profile, Targets, compute_targets
from .vision import MealAnalysis, analyze_meal

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Health Project - Food Logger")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

init_db()


MealType = Literal["breakfast", "lunch", "dinner", "snack"]


class SaveEntryRequest(BaseModel):
    date: str  # YYYY-MM-DD, client supplies local date
    meal_type: MealType
    analysis: MealAnalysis
    note: str | None = Field(default=None, max_length=4000)
    source: Literal["photo", "text"] = "photo"


class UpdateEntryRequest(BaseModel):
    analysis: MealAnalysis
    note: str | None = Field(default=None, max_length=4000)


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post(
    "/analyze",
    response_model=MealAnalysis,
    dependencies=[Depends(require_token)],
)
async def analyze(
    photo: UploadFile | None = File(None),
    note: str | None = Form(None),
    previous: str | None = Form(None),  # earlier estimate (JSON) being corrected
) -> MealAnalysis:
    note = (note or "").strip() or None
    if photo is None and not note:
        raise HTTPException(400, "Send a photo, a description, or both")
    if note and len(note) > 2000:
        raise HTTPException(400, "Description is too long (2,000 characters max)")
    if previous and len(previous) > 20000:
        raise HTTPException(400, "Earlier estimate is too large")

    image_bytes = None
    media_type = "image/jpeg"
    if photo is not None:
        if not photo.content_type or not photo.content_type.startswith("image/"):
            raise HTTPException(400, "Expected an image upload")
        image_bytes = await photo.read()
        if not image_bytes:
            raise HTTPException(400, "Empty file")
        media_type = photo.content_type

    # The Claude call blocks for 15-30s, so keep it off the event loop
    try:
        return await run_in_threadpool(
            analyze_meal, image_bytes, media_type=media_type, note=note, previous=previous
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/entries", dependencies=[Depends(require_token)])
def create_entry(req: SaveEntryRequest) -> dict:
    meal_id = save_meal(req.date, req.meal_type, req.analysis, note=req.note, source=req.source)
    return {"id": meal_id, "status": "saved"}


@app.put("/entries/{meal_id}", dependencies=[Depends(require_token)])
def correct_entry(meal_id: int, req: UpdateEntryRequest) -> dict:
    if not update_meal(meal_id, req.analysis, req.note):
        raise HTTPException(404, "Entry not found")
    return {"id": meal_id, "status": "updated"}


@app.get("/entries", dependencies=[Depends(require_token)])
def get_entries(date: str) -> list[dict]:
    return list_meals_for_date(date)


@app.delete("/entries/{meal_id}", dependencies=[Depends(require_token)])
def remove_entry(meal_id: int) -> dict:
    if not delete_meal(meal_id):
        raise HTTPException(404, "Entry not found")
    return {"status": "deleted"}


@app.get(
    "/summary",
    response_model=SummaryResponse,
    dependencies=[Depends(require_token)],
)
def get_summary(days: int = 7, today: date | None = None, refresh: bool = False) -> SummaryResponse:
    # `today` comes from the client so the period follows the phone's local
    # date rather than the server's (UTC on Render)
    if days < 1 or days > 90:
        raise HTTPException(400, "days must be between 1 and 90")
    return build_summary(days, today=today, refresh=refresh)


class ProfileResponse(BaseModel):
    profile: Profile | None
    targets: Targets | None


def _profile_response() -> ProfileResponse:
    row = get_profile()
    if not row:
        return ProfileResponse(profile=None, targets=None)
    profile = Profile(**row)
    return ProfileResponse(profile=profile, targets=compute_targets(profile))


@app.get("/profile", response_model=ProfileResponse, dependencies=[Depends(require_token)])
def read_profile() -> ProfileResponse:
    return _profile_response()


@app.put("/profile", response_model=ProfileResponse, dependencies=[Depends(require_token)])
def update_profile(profile: Profile) -> ProfileResponse:
    save_profile(profile.model_dump())
    return _profile_response()
