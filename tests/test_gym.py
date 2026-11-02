from datetime import date

from backend import health
from tests.fakes import programme

TODAY = "2026-10-25"
PNG = b"\x89PNG fake"


def _fitness(client, today: str = TODAY) -> dict:
    return client.get("/fitness", params={"days": 14, "today": today}).json()


def _save(client, **overrides) -> int:
    body = {"date": TODAY, "class_name": "Delta Strength", "programme": programme().model_dump(),
            "notes": "front squat 60kg, took the Int ladder", "effort_rating": 4, **overrides}
    response = client.post("/gym/sessions", json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


# --- reading screenshots ------------------------------------------------------


def test_analyze_sends_every_screenshot_and_returns_the_programme(client, claude):
    files = [("photos", ("a.png", PNG, "image/png")), ("photos", ("b.png", PNG, "image/png"))]
    r = client.post("/gym/analyze", files=files)
    assert r.status_code == 200
    assert r.json()["class_name"] == "Delta Strength" and r.json()["blocks"][0]["kind"] == "strength"
    assert claude.images() == 2
    assert "Programming notes" in claude.prompt()


def test_analyze_rejects_missing_non_image_empty_and_too_many(client):
    assert client.post("/gym/analyze").status_code == 400
    assert client.post("/gym/analyze", files=[("photos", ("a.txt", b"hi", "text/plain"))]).status_code == 400
    assert client.post("/gym/analyze", files=[("photos", ("a.png", b"", "image/png"))]).status_code == 400
    five = [("photos", (f"{i}.png", PNG, "image/png")) for i in range(5)]
    assert client.post("/gym/analyze", files=five).status_code == 400


def test_analyze_reports_a_claude_failure_as_502(client, claude):
    claude.stop_reason = "refusal"
    r = client.post("/gym/analyze", files=[("photos", ("a.png", PNG, "image/png"))])
    assert r.status_code == 502 and "declined" in r.json()["detail"]


# --- saving, editing, deleting -------------------------------------------------


def test_saved_class_comes_back_with_blocks_notes_and_rating(client):
    session_id = _save(client)
    [g] = _fitness(client)["gym_sessions"]
    assert g["id"] == session_id and g["local_date"] == TODAY and g["class_name"] == "Delta Strength"
    assert g["effort_rating"] == 4 and "front squat 60kg" in g["notes"]
    assert [b["kind"] for b in g["blocks"]] == ["strength", "accessory"]
    assert g["summary"] == "Upper-body pull and lower-body strength"


def test_a_class_can_be_logged_by_name_alone(client):
    _save(client, programme=None, notes="  ", effort_rating=None, class_name="  Hyrox Conditioning ")
    [g] = _fitness(client)["gym_sessions"]
    assert g["class_name"] == "Hyrox Conditioning" and g["blocks"] == []
    assert g["notes"] is None and g["effort_rating"] is None


def test_validation(client):
    assert client.post("/gym/sessions", json={"date": TODAY, "class_name": "  "}).status_code == 422
    assert client.post("/gym/sessions", json={"date": "today", "class_name": "x"}).status_code == 422
    for rating in (0, 6):
        assert client.post("/gym/sessions", json={"date": TODAY, "class_name": "x", "effort_rating": rating}).status_code == 422


def test_editing_changes_the_fields_but_keeps_the_programme(client):
    session_id = _save(client)
    r = client.put(f"/gym/sessions/{session_id}",
                   json={"date": "2026-10-24", "class_name": "Delta Engine", "notes": "scaled down", "effort_rating": 2})
    assert r.status_code == 200
    [g] = _fitness(client)["gym_sessions"]
    assert (g["local_date"], g["class_name"], g["notes"], g["effort_rating"]) == ("2026-10-24", "Delta Engine", "scaled down", 2)
    assert len(g["blocks"]) == 2 and g["updated_at"]
    assert client.put("/gym/sessions/9999", json={"date": TODAY, "class_name": "x"}).status_code == 404


def test_delete(client):
    session_id = _save(client)
    assert client.delete(f"/gym/sessions/{session_id}").status_code == 200
    assert _fitness(client)["gym_sessions"] == []
    assert client.delete(f"/gym/sessions/{session_id}").status_code == 404


def test_only_classes_in_the_window_are_listed(client):
    _save(client, date="2026-09-01")
    assert _fitness(client)["gym_sessions"] == []


# --- the analysis ---------------------------------------------------------------


def test_gym_class_reaches_the_summary_prompt_and_changes_the_cache(client, claude, with_profile):
    _save(client, date="2026-10-24")
    client.get("/summary", params={"days": 7, "today": TODAY})
    prompt = claude.calls[-1]["messages"][0]["content"]
    assert "Sleep, activity and gym training:" in prompt
    assert "2026-10-24: gym class: Delta Strength, programmed as [strength (strength): 4 sets; 8 chin ups" in prompt
    assert "they rated it 4/5" in prompt and 'their own words: "front squat 60kg, took the Int ladder"' in prompt

    # Same data: cached. Editing the notes: a fresh analysis
    client.get("/summary", params={"days": 7, "today": TODAY})
    assert len(claude.calls) == 1
    client.put("/gym/sessions/1", json={"date": "2026-10-24", "class_name": "Delta Strength", "notes": "heavier"})
    client.get("/summary", params={"days": 7, "today": TODAY})
    assert len(claude.calls) == 2


def test_a_class_without_notes_says_what_is_unknown(client):
    _save(client, notes=None, effort_rating=None)
    text = health.format_for_analysis([TODAY])
    assert "no notes on what they actually did" in text and "rated" not in text


def test_gym_and_watch_data_sit_on_the_same_day(client, garmin_fake):
    assert health.sync_garmin(date.fromisoformat(TODAY))["status"] == "ok"
    _save(client)
    line = health.format_for_analysis([TODAY])
    assert "workout: running" in line and "gym class: Delta Strength" in line
