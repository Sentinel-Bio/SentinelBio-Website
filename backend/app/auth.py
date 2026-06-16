"""Supabase JWT verification (supports both HS256 legacy and ES256 asymmetric)."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

import httpx
from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt

from app.config import get_settings


class AuthedUser:
    def __init__(self, id: str, email: str | None = None):
        self.id = id
        self.email = email


@lru_cache
def _jwks() -> dict[str, Any]:
    """Fetch Supabase's public JWKS once per process. Cached forever.

    If you ever rotate keys, restart the backend.
    """
    s = get_settings()
    url = f"{s.supabase_url}/auth/v1/.well-known/jwks.json"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    return r.json()


def _verify_with_jwks(token: str) -> dict[str, Any]:
    """Verify an asymmetric (ES256/RS256) Supabase token using JWKS."""
    unverified = jwt.get_unverified_header(token)
    kid = unverified.get("kid")
    alg = unverified.get("alg", "ES256")

    jwks = _jwks()
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key:
        raise HTTPException(status_code=401, detail=f"unknown_kid:{kid}")
    return jwt.decode(
        token,
        key,
        algorithms=[alg],
        audience="authenticated",
    )


def _verify_with_secret(token: str) -> dict[str, Any]:
    """Verify a legacy HS256 Supabase token with the shared secret."""
    s = get_settings()
    return jwt.decode(
        token,
        s.supabase_jwt_secret,
        algorithms=["HS256"],
        audience="authenticated",
    )


def verify_token(authorization: Annotated[str | None, Header()] = None) -> AuthedUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_token")
    token = authorization.removeprefix("Bearer ")

    # Try asymmetric first (new projects); fall back to HS256 (legacy).
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"bad_header:{e}") from e

    try:
        if alg.startswith("ES") or alg.startswith("RS"):
            payload = _verify_with_jwks(token)
        else:
            payload = _verify_with_secret(token)
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid_token:{e}") from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"jwks_fetch_failed:{e}") from e

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="no_sub_in_token")
    return AuthedUser(id=user_id, email=payload.get("email"))


def try_user(authorization: Annotated[str | None, Header()] = None) -> AuthedUser | None:
    if not authorization:
        return None
    try:
        return verify_token(authorization)
    except HTTPException:
        return None


async def is_admin(user: AuthedUser) -> bool:
    from app.supabase_client import service_client
    client = service_client()
    result = (
        client.table("profiles")
        .select("role")
        .eq("id", user.id)
        .single()
        .execute()
    )
    role = (result.data or {}).get("role")
    return role in ("admin", "super_admin")

async def require_admin(user: RequireUser) -> AuthedUser:
    """Dependency that requires admin or super_admin role."""
    if not await is_admin(user):
        raise HTTPException(status_code=403, detail="admin_required")
    return user


RequireAdmin = Annotated[AuthedUser, Depends(require_admin)]
RequireUser = Annotated[AuthedUser, Depends(verify_token)]
OptionalUser = Annotated[AuthedUser | None, Depends(try_user)]
