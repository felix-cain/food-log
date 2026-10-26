from backend import db, nutrition
from tests.fakes import item, meal


def _meal(meal_type: str, *items: dict) -> dict:
    return {"meal_type": meal_type, "items": list(items)}


def _row(**kw) -> dict:
    """An item as list_meals_between returns it."""
    base = {"name": "x", "grams": 100, "calories": 100, "protein_g": 0, "fibre_g": 0, "free_sugar_g": 0,
            "sat_fat_g": 0, "salt_g": 0, "food_group": "other", "processing": "whole",
            "fruit_veg_portions": 0, "plants": []}
    return {**base, **kw}


def _measure(fq, key):
    return next(m for m in fq.measures if m.key == key)


def test_no_detail_means_no_measures():
    old = {"name": "toast", "grams": 80, "calories": 200, "protein_g": 7, "food_group": None}
    fq = nutrition.food_quality({"2026-10-25": [_meal("lunch", old)]}, ["2026-10-25"], "male")
    assert fq.measures == [] and fq.meals_with_detail == 0 and fq.meals_total == 1


def test_per_day_measures_and_guidelines():
    day1 = [_meal("breakfast", _row(name="porridge", fibre_g=8, plants=["oat"])),
            _meal("dinner", _row(name="steak", grams=200, food_group="red_meat", protein_g=50, salt_g=2),
                  _row(name="broccoli", fibre_g=4, fruit_veg_portions=1, plants=["broccoli"], food_group="vegetables"))]
    day2 = [_meal("lunch", _row(name="salmon", food_group="oily_fish", protein_g=30, fibre_g=0, salt_g=1),
                  _row(name="crisps", processing="ultra_processed", calories=300, free_sugar_g=1, salt_g=1))]
    fq = nutrition.food_quality({"2026-10-24": day1, "2026-10-25": day2}, ["2026-10-24", "2026-10-25"], "female")

    assert _measure(fq, "fibre").value == 6.0  # (8 + 4) / 2 days
    assert _measure(fq, "red_meat").value == 100.0 and _measure(fq, "red_meat").direction == "at_most"
    assert _measure(fq, "salt").value == 2.0
    assert _measure(fq, "sat_fat").guideline == 20  # female limit
    assert _measure(fq, "oily_fish").value == 3.5  # 1 meal in 2 days of data -> per week
    assert _measure(fq, "plants").value == 2 and fq.distinct_plants == 2
    assert fq.upf_calorie_share == 43  # 300 of 700 calories
    assert [(s.group, s.share) for s in fq.protein_sources] == [("red_meat", 62), ("oily_fish", 38)]


def test_plants_are_counted_per_week():
    days = {f"2026-10-{d:02d}": [_meal("lunch", _row(plants=[f"plant{d % 7}"]))] for d in range(1, 15)}
    fq = nutrition.food_quality(days, list(days), "male")
    assert _measure(fq, "plants").value == 7.0  # 7 distinct in each 7-day block
    assert fq.distinct_plants == 7


def test_frequent_foods_need_two_appearances():
    days = {"2026-10-24": [_meal("breakfast", _row(name="Porridge")), _meal("lunch", _row(name="soup"))],
            "2026-10-25": [_meal("breakfast", _row(name="porridge "))]}
    fq = nutrition.food_quality(days, list(days), None)
    assert [(f.name, f.count) for f in fq.frequent_foods] == [("porridge", 2)]


def test_items_round_trip_through_the_database():
    meal_id = db.save_meal("2026-10-25", "lunch", meal(items=[item(plants=["chickpea", "spinach"], fibre_g=9)]))
    [saved] = [m for m in db.list_meals_between("2026-10-25", "2026-10-25") if m["id"] == meal_id]
    assert saved["items"][0]["plants"] == ["chickpea", "spinach"] and saved["items"][0]["fibre_g"] == 9
    assert saved["items"][0]["food_group"] == "whole_grains"


def test_summary_returns_quality_and_gives_it_to_claude(client, claude, with_profile):
    lunch = meal(items=[item(name="lentil dahl", food_group="legumes", fibre_g=12, protein_g=18, plants=["lentil"])])
    client.post("/entries", json={"date": "2026-10-25", "meal_type": "lunch", "analysis": lunch.model_dump()})
    s = client.get("/summary", params={"days": 7, "today": "2026-10-26"}).json()
    fq = s["food_quality"]
    assert fq["meals_with_detail"] == 1 and fq["protein_sources"][0]["group"] == "legumes"
    prompt = claude.calls[-1]["messages"][0]["content"]
    assert "- Fibre: 12g per day (guideline: at least 30g)" in prompt
    assert "lentil dahl (80g, 200cal, 18p/38c/1f, legumes, processed, fibre 12g)" in prompt
    assert "not calorie counting" in prompt
