"""Bearer-token auth.

Pattern: a single shared secret stored in the APP_TOKEN env var. Clients send
it on every API request via `Authorization: Bearer <token>`. If APP_TOKEN is
not set in the environment (e.g. local dev), auth is bypassed entirely so the
laptop workflow stays friction-free.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("APP_TOKEN")
    if not expected:
        return  # auth disabled — local dev mode
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    token = authorization[len("Bearer ") :].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(401, "Invalid token")
