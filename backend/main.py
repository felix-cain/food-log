"""FastAPI backend for the food-logging PWA.

Endpoints:
    GET  /                 - PWA frontend
    POST /analyze          - Send a photo, get Claude's structured guess (no save)
    GET  /entries?date=... - List meal entries for a given YYYY-MM-DD
    POST /entries          - Save an analyzed meal under a date + meal type
    DELETE /entries/{id}   - Remove a saved meal
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import require_token
from .db import delete_meal, init_db, list_meals_for_date, save_meal
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
