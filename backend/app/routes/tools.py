"""Tool runs — list available tools, submit runs, fetch results."""
from __future__ import annotations

import os 
import tempfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.auth import RequireUser
from app.supabase_client import service_client
from app.tools.registry import list_tools, get_tool

from fastapi import UploadFile, File, Form


router = APIRouter(prefix="/projects", tags=["tools"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

class ToolRunCreate(BaseModel):
    tool: str
    label: str | None = None
    inputs: dict[str, Any] = {}
    params: dict[str, Any] = {}

@router.post("/{slug}/runs")
async def create_run(
    slug: str, payload: ToolRunCreate, user: RequireUser
) -> dict[str, Any]:
    """Create a tool run with JSON inputs (no file upload)."""
    if not get_tool(payload.tool):
        raise HTTPException(status_code=400, detail=f"unknown_tool: {payload.tool}")

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

    row = {
        "project_id": project.data["id"],
        "owner_id": user.id,
        "tool": payload.tool,
        "label": payload.label,
        "status": "queued",
        "progress": 0,
        "inputs": payload.inputs,
        "params": payload.params,    # ← add
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = client.table("tool_runs").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="insert_failed")
    return result.data[0]

@router.post("/{slug}/runs/{run_id}/retry")
async def retry_run(slug: str, run_id: str, user: RequireUser) -> dict[str, Any]:
    """Re-run a previous run identically: clone its tool/inputs/params into a new
    queued run. The original is left untouched (kept as history).

    Note on inputs: runs created via multipart upload store a tmp `upload_path`
    that is deleted after the original run. Those can't be cloned verbatim — the
    retry will fail at input resolution if it depended on a vanished upload.
    file_ids / fasta_text / pasted params all clone fine. The frontend warns for
    the upload case and offers edit-and-retry instead.
    """
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

    orig = (
        client.table("tool_runs").select("*")
        .eq("id", run_id).eq("project_id", project.data["id"])
        .maybe_single().execute()
    )
    if not orig or not orig.data:
        raise HTTPException(status_code=404, detail="run_not_found")
    src = orig.data

    base_label = src.get("label") or src["tool"]
    row = {
        "project_id": project.data["id"],
        "owner_id": user.id,
        "tool": src["tool"],
        "label": f"{base_label} (retry)",
        "status": "queued",
        "progress": 0,
        "inputs": src.get("inputs") or {},
        "params": src.get("params") or {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = client.table("tool_runs").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="retry_insert_failed")
    return result.data[0]


@router.get("/tools/registry")
async def get_registry() -> list[dict[str, Any]]:
    return list_tools()


@router.get("/{slug}/runs")
async def list_runs(slug: str, user: RequireUser) -> list[dict[str, Any]]:
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["visibility"] == "private" and project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="private")

    result = (
        client.table("tool_runs").select("*")
        .eq("project_id", project.data["id"])
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@router.get("/{slug}/runs/{run_id}")
async def get_run(slug: str, run_id: str, user: RequireUser) -> dict[str, Any]:
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["visibility"] == "private" and project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="private")

    result = (
        client.table("tool_runs").select("*")
        .eq("id", run_id).eq("project_id", project.data["id"])
        .maybe_single().execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="run_not_found")
    return result.data


@router.patch("/{slug}/runs/{run_id}")
async def rename_run(
    slug: str, run_id: str,
    payload: dict[str, Any],
    user: RequireUser,
) -> dict[str, Any]:
    """Rename a tool run (changes the label, not the underlying tool/inputs)."""
    new_label = (payload.get("label") or "").strip()
    if not new_label:
        raise HTTPException(status_code=400, detail="label_required")
    if len(new_label) > 200:
        raise HTTPException(status_code=400, detail="label_too_long")

    from app.access import can_edit_project_sync

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    run_check = (
        client.table("tool_runs")
        .select("id")
        .eq("id", run_id)
        .eq("project_id", project.data["id"])
        .maybe_single().execute()
    )
    if not run_check or not run_check.data:
        raise HTTPException(status_code=404, detail="run_not_found")

    result = (
        client.table("tool_runs")
        .update({"label": new_label})
        .eq("id", run_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="rename_failed")
    return result.data[0]


@router.delete("/{slug}/runs/{run_id}")
async def delete_run(slug: str, run_id: str, user: RequireUser) -> dict[str, str]:
    from app.access import can_edit_project_sync
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")
    client.table("tool_runs").delete().eq("id", run_id).eq("project_id", project.data["id"]).execute()
    return {"status": "deleted"}

UPLOAD_SCRATCH = os.environ.get("SENTINEL_UPLOAD_DIR", tempfile.gettempdir())

@router.post("/{slug}/runs/upload")
async def create_run_with_upload(
    slug: str,
    user: RequireUser,
    file: UploadFile = File(...),
    tool: str = Form(...),
    label: str | None = Form(None),
    species_id: str | None = Form(None),
    params_json: str = Form("{}"),    # ← add
) -> dict[str, Any]:
    if not get_tool(tool):
        raise HTTPException(status_code=400, detail=f"unknown_tool: {tool}")

    import json
    try:
        params = json.loads(params_json) if params_json else {}
    except Exception:
        raise HTTPException(status_code=400, detail="invalid params_json")

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

    safe_name = "".join(c for c in (file.filename or "input") if c.isalnum() or c in "._-")
    fd, tmp_path = tempfile.mkstemp(prefix="sentinel_upload_", suffix=f"_{safe_name}", dir=UPLOAD_SCRATCH)
    with os.fdopen(fd, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    inputs = {
        "filename": file.filename,
        "upload_path": tmp_path,
    }
    if species_id:
        inputs["species_id"] = species_id

    row = {
        "project_id": project.data["id"],
        "owner_id": user.id,
        "tool": tool,
        "label": label,
        "status": "queued",
        "progress": 0,
        "inputs": inputs,
        "params": params,      # ← add
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = client.table("tool_runs").insert(row).execute()
    if not result.data:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="insert_failed")
    return result.data[0]
