"""Supabase client singletons.

Two clients:
- `user_client(token)` — executes as the logged-in user, so RLS applies
- `service_client` — executes as service_role, bypasses RLS (for server-owned writes)

Use `user_client` for anything user-scoped (their collections, etc).
Use `service_client` for the species pool (global, admin-written) and job runs.
"""
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def service_client() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


def user_client(access_token: str) -> Client:
    """Build a client that acts as the authed user. Respects RLS."""
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client
