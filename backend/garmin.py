"""Garmin Connect Personal API client: official, read-only, pull-only.

Follows the HTTP contract of Garmin's own Python client
(github.com/garmin/connect-personal-api-python, Apache-2.0) using httpx, so
the app needs no extra dependency and no on-disk token cache:

- POST {base}/personalapitoken-service/... with `Authorization: ApiKey <key>`
  returns a JWT as plain text (expiry in its `exp` claim)
- every data call is a GET with `Authorization: Bearer <token>`

The key comes from GARMIN_PERSONAL_API_KEY. Keys last at most a year and are
created at connect.garmin.com/app/settings/personalApiToken.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import time

import certifi
import httpx

BASE_URL = os.getenv("GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com").rstrip("/")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
TIMEOUT_SECONDS = 30.0
# Re-exchange the key when the token has less than this left
TOKEN_MIN_REMAINING_SECONDS = 3600


class GarminError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# Process-lifetime token cache; Render restarts simply re-exchange the key
_token_cache: dict[str, tuple[str, float]] = {}


def _tls_context() -> ssl.SSLContext:
    # certifi's bundle plus the OS trust store: networks that inspect TLS
    # (e.g. a corporate laptop) re-sign traffic with a root only the OS trusts
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_default_certs()
    return ctx


def is_configured() -> bool:
    return bool(os.getenv("GARMIN_PERSONAL_API_KEY", "").strip())


def _jwt_expiry(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError) as e:
        raise GarminError("Garmin returned a token without an expiry") from e


class GarminClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (api_key or os.getenv("GARMIN_PERSONAL_API_KEY", "")).strip()
        if not self._api_key:
            raise GarminError("GARMIN_PERSONAL_API_KEY is not set")
        self._http = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_SECONDS, verify=_tls_context())

    def __enter__(self) -> GarminClient:
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def token_lifetime_hours(self) -> float:
        """Hours until the current token expires (for diagnostics)."""
        self._token()
        return (_token_cache[self._api_key][1] - time.time()) / 3600

    def _token(self, force_refresh: bool = False) -> str:
        cached = _token_cache.get(self._api_key)
        if cached and not force_refresh and cached[1] - time.time() > TOKEN_MIN_REMAINING_SECONDS:
            return cached[0]
        response = self._http.post(
            TOKEN_PATH, headers={"Accept": "*/*", "Authorization": f"ApiKey {self._api_key}"}
        )
        if response.status_code in (401, 403):
            raise GarminError(
                "Garmin rejected the API key. It may have expired; create a new one in Garmin Connect.",
                response.status_code,
            )
        if not response.is_success:
            raise GarminError(f"Garmin token request failed ({response.status_code})", response.status_code)
        token = response.text.strip()
        _token_cache[self._api_key] = (token, _jwt_expiry(token))
        return token

    def get(self, path: str, params: dict | None = None):
        """GET a Personal API path; returns decoded JSON, or None for an empty body."""
        for attempt in range(2):
            response = self._http.get(
                path,
                params=params,
                headers={"Accept": "application/json", "Authorization": f"Bearer {self._token(force_refresh=attempt > 0)}"},
            )
            # A token can be revoked before its expiry; retry once with a fresh one
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code == 429:
                raise GarminError("Garmin is rate-limiting requests. Try again later.", 429)
            if not response.is_success:
                raise GarminError(f"Garmin request failed ({response.status_code}) for {path}", response.status_code)
            return response.json() if response.content else None
        raise GarminError("Garmin rejected the token", 401)

    # Dates are YYYY-MM-DD; ranges are inclusive

    def daily_sleep(self, date: str):
        return self.get("/sleep-service/sleep/dailySleep", {"date": date})

    def daily_sleep_summaries(self, start: str, end: str):
        return self.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": start, "endDate": end})

    def hrv_summaries(self, start: str, end: str):
        return self.get(f"/hrv-service/hrv/daily/{start}/{end}")

    def daily_summaries(self, start: str, end: str):
        return self.get("/usersummary-service/v2/dailysummaries", {"startDate": start, "endDate": end})

    def body_battery(self, start: str, end: str):
        return self.get("/wellness-service/wellness/bodyBattery/reports/daily", {"startDate": start, "endDate": end})

    def search_activities(self, start_date: str, end_date: str, start: int = 0, limit: int = 100):
        return self.get(
            "/activitylist-service/activities/search/activities",
            {"startDate": start_date, "endDate": end_date, "start": start, "limit": limit},
        )

    def activity(self, activity_id: int):
        return self.get(f"/activity-service/activity/{int(activity_id)}")
