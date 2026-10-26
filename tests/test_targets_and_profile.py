from backend.targets import Profile, compute_targets
from tests.conftest import PROFILE


def test_profile_targets_use_mifflin_st_jeor_and_activity_factor():
    # BMR = 10*80 + 6.25*180 - 5*30 + 5 = 1780; x1.55 (active) = 2759
    t = compute_targets(Profile(**PROFILE))
    assert (t.calories, t.calories_low, t.calories_high, t.protein_g) == (2750, 2500, 3050, 130)
    assert t.basis == "profile" and t.active_kcal_avg is None


def test_female_formula_and_goal_multiplier():
    p = Profile(**{**PROFILE, "sex": "female", "goal": "lose"})
    # BMR 1780 - 166 = 1614; x1.55 x0.85 = 2126
    assert compute_targets(p).calories == 2150


def test_measured_activity_replaces_the_activity_factor():
    # 1780 x1.2 + 0.6 x600 = 2496
    t = compute_targets(Profile(**PROFILE), active_kcal_avg=600)
    assert t.calories == 2500
    assert t.basis == "garmin" and t.active_kcal_avg == 600


def test_profile_starts_empty(client):
    assert client.get("/profile").json() == {"profile": None, "targets": None}


def test_profile_round_trip(client, with_profile):
    assert with_profile["targets"]["calories"] == 2750
    assert client.get("/profile").json()["profile"]["weight_kg"] == 80


def test_profile_rejects_out_of_range_values(client):
    assert client.put("/profile", json={**PROFILE, "age": 5}).status_code == 422
    assert client.put("/profile", json={**PROFILE, "activity": "couch"}).status_code == 422


def test_app_token_is_enforced_when_set(client, monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "secret")
    assert client.get("/profile").status_code == 401
    assert client.get("/profile", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/profile", headers={"Authorization": "Bearer secret"}).status_code == 200
    # The page itself stays public so the token can be entered
    assert client.get("/").status_code == 200
