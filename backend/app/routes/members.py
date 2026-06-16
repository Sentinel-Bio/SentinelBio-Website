"""Project member (collaborator) management endpoints.

Routes:
  GET    /api/projects/{slug}/members        — list members (any member sees all)
  POST   /api/projects/{slug}/members        — invite by email (owner only)
  PATCH  /api/projects/{slug}/members/{id}   — change role (owner only)
  DELETE /api/projects/{slug}/members/{id}   — remove member (owner only)

Invite-by-email semantics:
  - If the email is already a Supabase user, we look up their user_id and
    insert directly with that user_id.
  - If not, we insert with user_id=NULL and email=X. On their first sign-in,
    the resolve_pending_invites trigger fills in user_id.

The 'owner' role on project_members table is never created via this endpoint;
the project owner is implicit from projects.owner_id. We surface it as a
synthetic member row in the GET response so the UI can show "you + N others".
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.access import require_project_access
from app.auth import RequireUser
from app.supabase_client import service_client


router = APIRouter(prefix="/projects", tags=["members"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="editor", pattern="^(editor|viewer)$")


class RoleUpdate(BaseModel):
    role: str = Field(pattern="^(editor|viewer)$")


async def _resolve_user_by_email(email: str) -> dict[str, Any] | None:
    """Look up an existing auth.users row by email.
    Returns {'id', 'email'} or None."""
    client = service_client()
    # Supabase exposes auth.users via the auth admin API. We use the
    # supabase python client's auth.admin if available; otherwise this is
    # a no-op (the email path still works via the resolve trigger).
    try:
        # The python supabase client's auth.admin.list_users is the supported way
        result = client.auth.admin.list_users()
        users = result.users if hasattr(result, "users") else result
        for u in users:
            u_email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
            if u_email and u_email.lower() == email.lower():
                u_id = getattr(u, "id", None) or u.get("id")
                return {"id": str(u_id), "email": u_email}
    except Exception as e:
        # If we can't reach the admin API, just return None and rely on
        # the email-pending path. The trigger will resolve it on signup.
        print(f"[members] auth admin lookup failed: {e}")
    return None


@router.get("/{slug}/members")
async def list_members(slug: str, user: RequireUser) -> list[dict[str, Any]]:
    project, role = await require_project_access(slug, user, required="viewer")
    client = service_client()

    members_res = (
        client.table("project_members")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
    )
    members = members_res.data or []

    # Pull owner info for the synthetic owner row at the top of the list.
    owner_id = project.get("owner_id")
    owner_email = None
    try:
        # Try to fetch the owner's email via auth admin
        result = client.auth.admin.get_user_by_id(owner_id) if owner_id else None
        if result:
            owner_email = (
                getattr(result.user, "email", None) if hasattr(result, "user")
                else (result.get("user", {}).get("email") if isinstance(result, dict) else None)
            )
    except Exception:
        pass

    owner_row = {
        "id": "owner",                  # synthetic id; not deletable
        "project_id": project["id"],
        "user_id": owner_id,
        "email": owner_email,
        "role": "owner",
        "invited_by": None,
        "invited_at": project.get("created_at"),
        "accepted_at": project.get("created_at"),
        "is_owner_row": True,
    }

    # Enrich members with email when user_id is set
    user_ids = [m["user_id"] for m in members if m.get("user_id")]
    email_by_user_id: dict[str, str] = {}
    if user_ids:
        try:
            res = client.auth.admin.list_users()
            users = res.users if hasattr(res, "users") else res
            for u in users:
                u_id = str(getattr(u, "id", None) or u.get("id", "") if not isinstance(u, dict) else u.get("id", ""))
                u_email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
                if u_id and u_email:
                    email_by_user_id[u_id] = u_email
        except Exception:
            pass

    for m in members:
        if m.get("user_id") and not m.get("email"):
            m["email"] = email_by_user_id.get(m["user_id"])
        m["is_owner_row"] = False

    return [owner_row, *members]


@router.post("/{slug}/members")
async def invite_member(
    slug: str,
    payload: InviteRequest,
    user: RequireUser,
) -> dict[str, Any]:
    project, _ = await require_project_access(slug, user, required="owner")
    client = service_client()

    email_lower = payload.email.lower().strip()

    # Don't invite the owner themselves.
    try:
        owner_res = client.auth.admin.get_user_by_id(project["owner_id"])
        owner_email = None
        if owner_res:
            u_obj = getattr(owner_res, "user", None) or owner_res
            owner_email = getattr(u_obj, "email", None)
            if owner_email and owner_email.lower() == email_lower:
                raise HTTPException(status_code=400, detail="cannot_invite_owner")
    except HTTPException:
        raise
    except Exception:
        pass

    existing_user = await _resolve_user_by_email(email_lower)

    # Check for existing membership with this email or user_id
    existing_member_res = (
        client.table("project_members")
        .select("id")
        .eq("project_id", project["id"])
        .execute()
    )
    for m in (existing_member_res.data or []):
        if existing_user and m.get("user_id") == existing_user["id"]:
            raise HTTPException(status_code=409, detail="already_a_member")
        # Email-pending duplicate check happens at insert via unique constraint

    row = {
        "project_id": project["id"],
        "user_id": existing_user["id"] if existing_user else None,
        "email": email_lower if not existing_user else (existing_user.get("email") or email_lower),
        "role": payload.role,
        "invited_by": user.id,
        "invited_at": _now(),
        "accepted_at": _now() if existing_user else None,
    }
    res = client.table("project_members").insert(row).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="invite_insert_failed")
    return res.data[0]


@router.patch("/{slug}/members/{member_id}")
async def update_member_role(
    slug: str,
    member_id: str,
    payload: RoleUpdate,
    user: RequireUser,
) -> dict[str, Any]:
    project, _ = await require_project_access(slug, user, required="owner")
    if member_id == "owner":
        raise HTTPException(status_code=400, detail="cannot_change_owner_role_via_endpoint")

    client = service_client()
    existing = (
        client.table("project_members")
        .select("id, project_id")
        .eq("id", member_id)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data or existing.data["project_id"] != project["id"]:
        raise HTTPException(status_code=404, detail="member_not_found")
    res = (
        client.table("project_members")
        .update({"role": payload.role})
        .eq("id", member_id)
        .execute()
    )
    return res.data[0] if res.data else {"status": "updated"}


@router.delete("/{slug}/members/{member_id}")
async def remove_member(
    slug: str,
    member_id: str,
    user: RequireUser,
) -> dict[str, str]:
    project, role = await require_project_access(slug, user, required="viewer")
    if member_id == "owner":
        raise HTTPException(status_code=400, detail="cannot_remove_owner")
    client = service_client()
    existing = (
        client.table("project_members")
        .select("id, project_id, user_id")
        .eq("id", member_id)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data or existing.data["project_id"] != project["id"]:
        raise HTTPException(status_code=404, detail="member_not_found")

    # Owner can remove anyone; non-owner can only remove THEMSELVES (leave project).
    if role != "owner" and existing.data.get("user_id") != user.id:
        raise HTTPException(status_code=403, detail="only_owner_or_self")

    client.table("project_members").delete().eq("id", member_id).execute()
    return {"status": "removed"}
