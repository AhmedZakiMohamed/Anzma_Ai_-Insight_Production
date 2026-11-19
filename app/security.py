"""Replaceable bearer authentication for normal Core REST and Socket.IO access."""

from __future__ import annotations

import hmac
import os
from http.cookies import SimpleCookie
from typing import Protocol

from fastapi import HTTPException, Request


class Authenticator(Protocol):
    def authenticate(self, token: str | None) -> bool: ...


class EnvironmentBearerAuthenticator:
    """Compare a deployment-provided bearer secret without logging or storing it."""

    def authenticate(self, token: str | None) -> bool:
        expected = os.getenv("ANZMA_API_TOKEN", "")
        return bool(len(expected) >= 32 and token and hmac.compare_digest(expected, token))


authenticator: Authenticator = EnvironmentBearerAuthenticator()


def token_from_authorization(value: str | None) -> str | None:
    if not value:
        return None
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    return token.strip() or None


def require_api_auth(request: Request) -> None:
    """FastAPI dependency for ordinary user/control API routes."""
    authorization = request.headers.get("authorization")
    token = token_from_authorization(authorization)
    if not authorization:
        raise HTTPException(401, detail={"code": "AUTH_MISSING", "message": "Bearer credentials are required."},
                            headers={"WWW-Authenticate": "Bearer"})
    if not authenticator.authenticate(token):
        raise HTTPException(401, detail={"code": "AUTH_INVALID", "message": "Bearer credentials are invalid."},
                            headers={"WWW-Authenticate": "Bearer"})


def socket_authenticate(auth: dict | None, cookie_header: str | None = None) -> bool:
    token = auth.get("token") if isinstance(auth, dict) else None
    if not token and cookie_header:
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            token = cookie.get("anzma_session").value if cookie.get("anzma_session") else None
        except Exception:
            token = None
    return authenticator.authenticate(token if isinstance(token, str) else None)
