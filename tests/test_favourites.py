from backend import db
from tests.fakes import meal

DAY = "2026-10-25"


def _saved_breakfast(client) -> int:
    body = {"date": DAY, "meal_type": "breakfast", "analysis": meal("Porridge with banana").model_dump()}
    return client.post("/entries", json=body).json()["id"]


def _meals(client, day: str) -> list[dict]:
    return client.get("/entries", params={"date": day}).json()


def test_star_a_meal_and_list_it(client):
    meal_id = _saved_breakfast(client)
    favourite_id = client.post("/favourites", json={"meal_id": meal_id}).json()["id"]
    [fav] = client.get("/favourites").json()
    assert fav["id"] == favourite_id and fav["name"] == "Porridge with banana"
    assert fav["meal_type"] == "breakfast" and fav["calories"] == 200 and fav["use_count"] == 0
    assert _meals(client, DAY)[0]["favourite_id"] == favourite_id


def test_logging_a_favourite_copies_it_to_the_day(client):
    favourite_id = client.post("/favourites", json={"meal_id": _saved_breakfast(client)}).json()["id"]
    r = client.post(f"/favourites/{favourite_id}/log", json={"date": "2026-10-26"})
    assert r.status_code == 200
    [logged] = _meals(client, "2026-10-26")
    assert logged["meal_type"] == "breakfast" and logged["source"] == "favourite"
    assert logged["favourite_id"] == favourite_id and logged["description"] == "Porridge with banana"
    assert logged["items"][0]["protein_g"] == 7
    assert client.get("/favourites").json()[0]["use_count"] == 1


def test_favourite_from_an_old_meal_keeps_missing_macros_missing(client):
    meal_id = _saved_breakfast(client)
    with db._connect() as conn:
        conn.execute("UPDATE meal_items SET protein_g = NULL, carbs_g = NULL, fat_g = NULL")
    favourite_id = client.post("/favourites", json={"meal_id": meal_id}).json()["id"]
    client.post(f"/favourites/{favourite_id}/log", json={"date": "2026-10-26"})
    assert _meals(client, "2026-10-26")[0]["items"][0]["protein_g"] is None


def test_correcting_a_logged_copy_leaves_the_favourite_alone(client):
    favourite_id = client.post("/favourites", json={"meal_id": _saved_breakfast(client)}).json()["id"]
    copy_id = client.post(f"/favourites/{favourite_id}/log", json={"date": "2026-10-26"}).json()["id"]
    client.put(f"/entries/{copy_id}", json={"analysis": meal("Porridge with honey", calories=260).model_dump()})
    assert client.get("/favourites").json()[0]["calories"] == 200


def test_unstarring_keeps_logged_meals(client):
    favourite_id = client.post("/favourites", json={"meal_id": _saved_breakfast(client)}).json()["id"]
    client.post(f"/favourites/{favourite_id}/log", json={"date": "2026-10-26"})
    assert client.delete(f"/favourites/{favourite_id}").status_code == 200
    assert client.get("/favourites").json() == []
    assert _meals(client, "2026-10-26")[0]["favourite_id"] is None


def test_missing_meal_or_favourite_is_404(client):
    assert client.post("/favourites", json={"meal_id": 999}).status_code == 404
    assert client.post("/favourites/999/log", json={"date": DAY}).status_code == 404
    assert client.delete("/favourites/999").status_code == 404


def test_most_used_favourites_come_first(client):
    first = client.post("/favourites", json={"meal_id": _saved_breakfast(client)}).json()["id"]
    second = client.post("/favourites", json={"meal_id": _saved_breakfast(client), "name": "Eggs"}).json()["id"]
    client.post(f"/favourites/{second}/log", json={"date": DAY})
    assert [f["id"] for f in client.get("/favourites").json()] == [second, first]
