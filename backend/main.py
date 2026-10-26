"""FastAPI backend for the food-logging PWA.

Endpoints:
    GET  /                 - PWA frontend
    POST /analyze          - Send a photo, get Claude's structured guess (no save)
    GET  /entries?date=... - List meal entries for a given YYYY-MM-DD
    POST /entries          - Save an analyzed meal under a date + meal type
    DELETE /entries/{id}   - Remove a saved meal
    GET  /profile          - Profile + derived calorie/protein targets (nulls if unset)
    PUT  /profile          - Save the profile
    GET  /summary?days=... - Stats + Claude analysis for the N days ending yesterday
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import require_token
from .db import delete_meal, get_profile, init_db, list_meals_for_date, save_meal, save_profile
from .summary import SummaryResponse, build_summary
from .targets import Profile, Targets, compute_targets
from .vision import MealAnalysis, analyze_meal_photo

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Health Project - Food Logger")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

init_db()


MealType = Literal["breakfast", "lunch", "dinner", "snack"]


class SaveEntryRequest(BaseModel):
    date: str  # YYYY-MM-DD, client supplies local date
    meal_type: MealType
    analysis: MealAnalysis


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post(
    "/analyze",
    response_model=MealAnalysis,
    dependencies=[Depends(require_token)],
)
async def analyze(photo: UploadFile = File(...)) -> MealAnalysis:
    if not photo.content_type or not photo.content_type.startswith("image/"):
        raise HTTPException(400, "Expected an image upload")
    image_bytes = await photo.read()
    if not image_bytes:
        raise HTTPException(400, "Empty file")
    return analyze_meal_photo(image_bytes, media_type=photo.content_type)


@app.post("/entries", dependencies=[Depends(require_token)])
def create_entry(req: SaveEntryRequest) -> dict:
    meal_id = save_meal(req.date, req.meal_type, req.analysis)
    return {"id": meal_id, "status": "saved"}


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
