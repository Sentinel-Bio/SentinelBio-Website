"""Project access control.

Centralizes the question "does this user have permission to <verb> this project?"
in one place. All backend routes that touched `owner_id != user.id` should use
this instead.

Roles, ordered by permission level (high → low):
  - owner:  full control. Implicit from projects.owner_id; also a member row.
  - editor: read + write (run tools, upload files, edit metadata).
            Cannot delete project or manage members.
  - viewer: read only.

For public/unlisted projects, anonymous read access is allowed via visibility.
"""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

from app.auth import AuthedUser
from app.supabase_client import service_client


Role = Literal["owner", "editor", "viewer"]
RequiredRole = Literal["owner", "editor", "viewer", "any"]

_ROLE_ORDER: dict[str, int] = {"viewer": 0, "editor": 1, "owner": 2}


def _meets(role: str | None, required: RequiredRole) -> bool:
    if required == "any":
        return role is not None
    if role is None:
        return False
    return _ROLE_ORDER.get(role, -1) >= _ROLE_ORDER.get(required, 99)


async def get_project_and_role(
    slug: str,
    user: AuthedUser | None,
) -> tuple[dict, str | None]:
    """Resolve a project by slug and the requesting user's role on it.

    Returns (project_row, role) where role is one of:
        'owner' | 'editor' | 'viewer' | None

    'None' means the user isn't a member. The project_row is still returned
    so the caller can decide whether anonymous read (public/unlisted) is OK.
    Raises 404 if the project doesn't exist.
    """
    client = service_client()
    res = (
        client.table("projects")
        .select("*")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    project = res.data

    if user is None:
        return project, None

    if project.get("owner_id") == user.id:
        return project, "owner"

    member = (
        client.table("project_members")
        .select("role")
        .eq("project_id", project["id"])
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if member and member.data:
        return project, member.data["role"]
    return project, None


async def require_project_access(
    slug: str,
    user: AuthedUser,
    required: RequiredRole = "viewer",
) -> tuple[dict, str]:
    """Same as get_project_and_role but raises 403 if the user lacks the
    required role. Public/unlisted projects allow 'viewer' role to be inferred
    for non-members.

    Returns (project_row, effective_role).
    """
    project, role = await get_project_and_role(slug, user)

    # Public/unlisted give implicit viewer.
    if role is None and project.get("visibility") in ("public", "unlisted"):
        role = "viewer"

    if not _meets(role, required):
        raise HTTPException(
            status_code=403,
            detail=f"insufficient_permission (required: {required}, have: {role or 'none'})",
        )
    return project, role  # type: ignore[return-value]


async def has_any_project_membership(user: AuthedUser) -> bool:
    """For the allowlist gate: does this user have ANY membership or own
    ANY project? If not, they're effectively a new uninvited user."""
    client = service_client()
    owned = (
        client.table("projects")
        .select("id", count="exact")
        .eq("owner_id", user.id)
        .limit(1)
        .execute()
    )
    if owned.count and owned.count > 0:
        return True
    member = (
        client.table("project_members")
        .select("id", count="exact")
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    return bool(member.count and member.count > 0)


def role_for_project_sync(project: dict, user_id: str) -> str | None:
    """Synchronous role lookup given a project row already in hand.
    Used inside existing route handlers to minimize refactoring."""
    if project.get("owner_id") == user_id:
        return "owner"
    client = service_client()
    res = (
        client.table("project_members")
        .select("role")
        .eq("project_id", project["id"])
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if res and res.data:
        return res.data["role"]
    return None


def can_read_project_sync(project: dict, user_id: str) -> bool:
    """User can read this project: public, unlisted, owner, or member."""
    if project.get("visibility") in ("public", "unlisted"):
        return True
    return role_for_project_sync(project, user_id) is not None


def can_edit_project_sync(project: dict, user_id: str) -> bool:
    """User can edit project contents (files, species, tool_runs)."""
    return role_for_project_sync(project, user_id) in ("owner", "editor")

