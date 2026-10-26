"""The Garmin client against a mocked HTTP layer (no network)."""

import base64
import json
import time

import httpx
import pytest

from backend import garmin


def _jwt(expires_in: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + expires_in}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


@pytest.fixture(autouse=True)
def clear_token_cache():
    garmin._token_cache.clear()
    yield
    garmin._token_cache.clear()


def _client(handler) -> garmin.GarminClient:
    g = garmin.GarminClient(api_key="test-key")
    g._http = httpx.Client(base_url=garmin.BASE_URL, transport=httpx.MockTransport(handler))
    return g


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY", raising=False)
    assert garmin.is_configured() is False
    with pytest.raises(garmin.GarminError, match="not set"):
        garmin.GarminClient()


def test_key_exchange_then_bearer_calls_with_token_reuse():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == garmin.TOKEN_PATH:
            return httpx.Response(200, text=_jwt(24 * 3600))
        return httpx.Response(200, json={"calendarDate": request.url.params["date"]})

    g = _client(handler)
    assert g.daily_sleep("2026-10-25") == {"calendarDate": "2026-10-25"}
    g.daily_sleep("2026-10-24")
    assert seen[0] == ("POST", garmin.TOKEN_PATH, "ApiKey test-key")
    assert seen[1][2].startswith("Bearer header.")
    assert sum(1 for s in seen if s[1] == garmin.TOKEN_PATH) == 1, "token is cached between calls"
    assert 23 < g.token_lifetime_hours() <= 24


def test_expiring_token_is_refreshed():
    tokens = iter([_jwt(600), _jwt(24 * 3600)])  # first expires in 10 min

    def handler(request):
        if request.url.path == garmin.TOKEN_PATH:
            return httpx.Response(200, text=next(tokens))
        return httpx.Response(200, json={})

    g = _client(handler)
    g.daily_summaries("2026-10-01", "2026-10-02")
    g.daily_summaries("2026-10-01", "2026-10-02")
    assert g.token_lifetime_hours() > 23


def test_revoked_token_is_retried_once_with_a_fresh_one():
    data_calls = []

    def handler(request):
        if request.url.path == garmin.TOKEN_PATH:
            return httpx.Response(200, text=_jwt(24 * 3600))
        data_calls.append(request)
        return httpx.Response(401 if len(data_calls) == 1 else 200, json={"ok": True})

    assert _client(handler).hrv_summaries("2026-10-01", "2026-10-02") == {"ok": True}
    assert len(data_calls) == 2


@pytest.mark.parametrize("status, message", [(401, "rejected the API key"), (500, "token request failed")])
def test_token_errors(status, message):
    g = _client(lambda request: httpx.Response(status, text="nope"))
    with pytest.raises(garmin.GarminError, match=message):
        g.daily_sleep("2026-10-25")


def test_rate_limit_and_empty_bodies():
    def handler(request):
        if request.url.path == garmin.TOKEN_PATH:
            return httpx.Response(200, text=_jwt(24 * 3600))
        if "sleep" in request.url.path:
            return httpx.Response(200, content=b"")
        return httpx.Response(429)

    g = _client(handler)
    assert g.daily_sleep("2026-10-25") is None
    with pytest.raises(garmin.GarminError) as err:
        g.search_activities("2026-10-01", "2026-10-02")
    assert err.value.status_code == 429


def test_token_without_expiry_is_rejected():
    g = _client(lambda request: httpx.Response(200, text="not-a-jwt"))
    with pytest.raises(garmin.GarminError, match="without an expiry"):
        g.daily_sleep("2026-10-25")
