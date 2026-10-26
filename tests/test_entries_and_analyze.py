import json

from backend import db
from tests.fakes import meal

TODAY = "2026-10-25"


def _save(client, **overrides) -> int:
    body = {"date": TODAY, "meal_type": "lunch", "analysis": meal().model_dump(), **overrides}
    response = client.post("/entries", json=body)
    assert response.status_code == 200
    return response.json()["id"]


def _entry(client, meal_id: int) -> dict:
    return next(m for m in client.get("/entries", params={"date": TODAY}).json() if m["id"] == meal_id)


# --- entries ----------------------------------------------------------------


def test_save_and_list_with_note_and_source(client):
    meal_id = _save(client, note="toast with pb", source="text")
    m = _entry(client, meal_id)
    assert m["note"] == "toast with pb" and m["source"] == "text" and m["updated_at"] is None
    assert m["items"][0]["name"] == "sourdough" and m["items"][0]["protein_g"] == 7


def test_photo_is_the_default_source(client):
    assert _entry(client, _save(client))["source"] == "photo"


def test_correcting_an_entry_replaces_items_and_marks_it_updated(client):
    meal_id = _save(client)
    revised = meal("Bigger toast", grams=160, calories=400).model_dump()
    assert client.put(f"/entries/{meal_id}", json={"analysis": revised, "note": "two slices"}).status_code == 200
    m = _entry(client, meal_id)
    assert m["description"] == "Bigger toast" and m["note"] == "two slices" and m["updated_at"]
    assert [i["calories"] for i in m["items"]] == [400]


def test_correcting_a_missing_entry_is_404(client):
    assert client.put("/entries/9999", json={"analysis": meal().model_dump()}).status_code == 404


def test_delete_removes_meal_and_its_items(client):
    meal_id = _save(client)
    assert client.delete(f"/entries/{meal_id}").status_code == 200
    assert client.get("/entries", params={"date": TODAY}).json() == []
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM meal_items").fetchall()[0][0] == 0
    assert client.delete(f"/entries/{meal_id}").status_code == 404


# --- analyze ----------------------------------------------------------------


def test_analyze_needs_a_photo_or_a_description(client):
    assert client.post("/analyze", data={}).status_code == 400
    assert client.post("/analyze", data={"note": "   "}).status_code == 400


def test_text_only_description(client, claude):
    r = client.post("/analyze", data={"note": "two slices of sourdough with peanut butter"})
    assert r.status_code == 200 and r.json()["description"] == "Toast (version 1)"
    assert claude.images() == 0
    assert "described a meal" in claude.prompt() and "Their description" in claude.prompt()


def test_claude_request_settings(client, claude):
    client.post("/analyze", data={"note": "an apple"})
    call = claude.calls[-1]
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "high"}


def test_photo_correction_sends_photo_previous_estimate_and_note(client, claude):
    previous = json.dumps(meal().model_dump())
    r = client.post(
        "/analyze",
        files={"photo": ("meal.jpg", b"\xff\xd8not-really-a-jpeg", "image/jpeg")},
        data={"note": "it was oat milk", "previous": previous},
    )
    assert r.json()["description"] == "Toast (version 2)"
    assert claude.images() == 1
    prompt = claude.prompt()
    assert "takes priority over the photo and the earlier estimate" in prompt
    assert "full revised list" in prompt and '"it was oat milk"' in prompt


def test_saved_entry_correction_has_no_photo(client, claude):
    client.post("/analyze", data={"note": "bigger portion", "previous": "{}"})
    assert claude.images() == 0
    assert "takes priority over the earlier estimate" in claude.prompt()


def test_analyze_rejects_bad_input(client):
    assert client.post("/analyze", data={"note": "x" * 2001}).status_code == 400
    assert client.post("/analyze", files={"photo": ("a.txt", b"hello", "text/plain")}).status_code == 400
    assert client.post("/analyze", data={"note": "x", "previous": "y" * 20001}).status_code == 400


def test_refusal_returns_a_readable_502(client, claude):
    claude.stop_reason = "refusal"
    r = client.post("/analyze", data={"note": "x"})
    assert r.status_code == 502 and r.json()["detail"] == "Claude declined to analyse this meal"
