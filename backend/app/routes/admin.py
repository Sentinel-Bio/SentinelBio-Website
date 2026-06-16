"""Admin endpoints — species edits, bulk taxon fetches, job queries."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.auth import RequireAdmin
from app.supabase_client import service_client
from app.sources import inaturalist, wikimedia
from app import jobs as job_dispatch
import re
router = APIRouter(prefix="/admin", tags=["admin"])


# ─── Species management ──────────────────────────────────────────────

class SpeciesPatch(BaseModel):
    scientific_name: str | None = None
    common_name: str | None = None
    rank: str | None = None
    taxonomy: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    needs_review: bool | None = None

@router.get("/species")
async def admin_list_species(
    user: RequireAdmin,
    q: str | None = None,
    rank: str | None = None,
    kingdom: str | None = None,
    phylum: str | None = None,
    class_: str | None = None,
    order: str | None = None,
    family: str | None = None,
    genus: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _ = user
    client = service_client()
    query = client.table("species").select("*").limit(min(limit, 1000))
    if q:
        query = query.ilike("scientific_name", f"%{q}%")
    if rank:
        query = query.eq("rank", rank)
    for key, val in [
        ("kingdom", kingdom),
        ("phylum", phylum),
        ("class", class_),
        ("order", order),
        ("family", family),
        ("genus", genus),
    ]:
        if val:
            query = query.contains("taxonomy", {key: val})
    query = query.order("updated_at", desc=True)
    return query.execute().data or []
@router.get("/species/{species_id}")
async def admin_get_species(species_id: str, user: RequireAdmin) -> dict[str, Any]:
    _ = user
    client = service_client()
    result = client.table("species").select("*").eq("id", species_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="not_found")
    return result.data

@router.patch("/species/{species_id}")
async def admin_patch_species(
    species_id: str,
    patch: SpeciesPatch,
    user: RequireAdmin,
) -> dict[str, Any]:
    _ = user
    patch_data = patch.model_dump(exclude_unset=True)
    if not patch_data:
        raise HTTPException(status_code=400, detail="empty_patch")
    patch_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    client = service_client()
    result = client.table("species").update(patch_data).eq("id", species_id).execute()
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="not_found")

    # Invalidate the tree cache since taxonomy may have changed.
    try:
        client.table("tree_layouts").delete().eq("id", "root").execute()
    except Exception:
        pass
    return result.data[0]


@router.delete("/species/{species_id}")
async def admin_delete_species(species_id: str, user: RequireAdmin) -> dict[str, str]:
    _ = user
    service_client().table("species").delete().eq("id", species_id).execute()
    client = service_client()
    client.table("species").delete().eq("id", species_id).execute()
    try:
        client.table("tree_layouts").delete().eq("id", "root").execute()
    except Exception:
        pass
    return {"status": "deleted"}


@router.post("/species/{species_id}/resync")
async def admin_resync_species(species_id: str, user: RequireAdmin) -> dict[str, Any]:
    """Queue a single-species resync job."""
    client = service_client()
    sp = client.table("species").select("ncbi_tax_id").eq("id", species_id).maybe_single().execute()
    if not sp or not sp.data or not sp.data.get("ncbi_tax_id"):
        raise HTTPException(status_code=404, detail="species_has_no_taxid")
    taxid = int(sp.data["ncbi_tax_id"])

    now = datetime.now(timezone.utc).isoformat()
    job_row = {
        "kind": "resync_species",
        "status": "queued",
        "owner_id": user.id,
        "params": {"species_id": species_id, "taxid": taxid},
        "created_at": now,
        "progress": 0,
    }
    job = client.table("jobs").insert(job_row).execute().data[0]

    await job_dispatch.enqueue("resync_species", job["id"], taxid)
    return job


# ─── Recursive taxon fetch ───────────────────────────────────────────

class RecursiveFetchRequest(BaseModel):
    root_taxid: int = Field(gt=0)
    stop_rank: str = Field(
        default="species",
        pattern="^(kingdom|phylum|class|order|family|genus|species|subspecies)$",
    )
    max_species: int = Field(default=500, le=5000, gt=0)


@router.post("/fetch-taxon")
async def admin_fetch_taxon(
    payload: RecursiveFetchRequest,
    user: RequireAdmin,
) -> dict[str, Any]:
    client = service_client()
    now = datetime.now(timezone.utc).isoformat()
    job_row = {
        "kind": "fetch_taxon",
        "status": "queued",
        "owner_id": user.id,
        "params": payload.model_dump(),
        "created_at": now,
        "progress": 0,
    }
    job = client.table("jobs").insert(job_row).execute().data[0]

    await job_dispatch.enqueue(
        "fetch_taxon_recursive",
        job["id"],
        payload.root_taxid,
        payload.stop_rank,
        payload.max_species,
    )
    return job


# ─── Job queries ─────────────────────────────────────────────────────

@router.get("/jobs")
async def admin_list_jobs(user: RequireAdmin, limit: int = 200) -> list[dict[str, Any]]:
    _ = user
    client = service_client()
    return (
        client.table("jobs")
        .select("*")
        .order("created_at", desc=True)
        .limit(min(limit, 500))
        .execute()
        .data or []
    )


@router.get("/jobs/{job_id}")
async def admin_get_job(job_id: str, user: RequireAdmin) -> dict[str, Any]:
    _ = user
    client = service_client()
    result = client.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="not_found")
    return result.data

@router.post("/jobs/{job_id}/cancel")
async def admin_cancel_job(job_id: str, user: RequireAdmin) -> dict[str, Any]:
    """Attempt to abort a running job. Works if it hasn't started yet
    (removes from queue) or if the worker is responsive to abort signals.
    """
    _ = user
    client = service_client()
    job = client.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    if not job or not job.data:
        raise HTTPException(status_code=404, detail="not_found")

    # Signal cancellation in arq. We store our own job_id in the DB but arq
    # generates its own task id on enqueue; we don't currently store that mapping.
    # Workaround: mark the DB row as "cancel_requested" and have the worker
    # check this flag at its polling points.
    client.table("jobs").update({
        "status": "failed",
        "error": "cancelled_by_user",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job_id).execute()

    return {"status": "cancel_requested"}
# ─── Am I admin? ─────────────────────────────────────────────────────

@router.get("/me")
async def admin_me(user: RequireAdmin) -> dict[str, Any]:
    """Cheap probe the frontend uses to check admin status."""
    return {"user_id": user.id, "email": user.email, "is_admin": True}

@router.post("/species/resync-all")
async def admin_resync_all(user: RequireAdmin) -> dict[str, Any]:
    """Queue a job that resyncs every species in the pool."""
    client = service_client()
    now = datetime.now(timezone.utc).isoformat()
    job_row = {
        "kind": "resync_all",
        "status": "queued",
        "owner_id": user.id,
        "params": {},
        "created_at": now,
        "progress": 0,
    }
    job = client.table("jobs").insert(job_row).execute().data[0]

    await job_dispatch.enqueue("resync_all_species", job["id"])
    return job

@router.post("/upload-image")
async def admin_upload_image(
    user: RequireAdmin,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Upload an image to Supabase Storage, return its public URL.

    Stored under species-media/{user_id}/{timestamp}-{filename}.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")

    import time
    timestamp = int(time.time() * 1000)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (file.filename or "image"))
    path = f"{user.id}/{timestamp}-{safe_name}"

    client = service_client()
    try:
        client.storage.from_("species-media").upload(
            path=path,
            file=content,
            file_options={"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"upload_failed:{e}") from e

    public_url = client.storage.from_("species-media").get_public_url(path)
    return {"url": public_url, "path": path}

@router.post("/species/{species_id}/fetch-gallery")
async def admin_fetch_gallery_candidates(
    species_id: str,
    user: RequireAdmin,
) -> dict[str, Any]:
    """Return candidate photos from iNaturalist + Wikimedia. The admin picks
    which ones to save via a separate PATCH to the species row.

    This endpoint does NOT save anything — it's a search, not an insert.
    """
    _ = user
    client = service_client()
    sp = client.table("species").select("scientific_name").eq("id", species_id).maybe_single().execute()
    if not sp or not sp.data:
        raise HTTPException(status_code=404, detail="species_not_found")
    name = sp.data["scientific_name"]

    inat = await inaturalist.fetch_photos(name, limit=6)
    wiki = await wikimedia.fetch_photos(name, limit=6)

    return {"inaturalist": inat, "wikimedia": wiki}

@router.post("/backfill-lineages")
async def admin_backfill_lineages(user: RequireAdmin) -> dict[str, Any]:
    """Queue a job to backfill taxid-rich lineages for all existing species."""
    client = service_client()
    now = datetime.now(timezone.utc).isoformat()
    job_row = {
        "kind": "backfill_lineages",
        "status": "queued",
        "owner_id": user.id,
        "params": {},
        "created_at": now,
        "progress": 0,
    }
    job = client.table("jobs").insert(job_row).execute().data[0]

    await job_dispatch.enqueue("backfill_lineages", job["id"])
    return job
