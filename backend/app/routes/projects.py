"""Project endpoints — user-owned research projects referencing species.

(Formerly 'collections'. Migrated in Phase 1 of the research workspace.)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from slugify import slugify

from app.auth import RequireUser
from app.access import can_read_project_sync, can_edit_project_sync
from app.supabase_client import service_client

router = APIRouter(prefix="/projects", tags=["projects"])


# ─── payload shapes ────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    notes: str | None = None
    visibility: str = Field(default="private", pattern="^(private|unlisted|public)$")
    research_question: str | None = None
    hypotheses: list[str] | None = None
    objectives: list[str] | None = None
    status: str = Field(default="planning", pattern="^(planning|active|paused|done|archived)$")

class FolderLabelUpdate(BaseModel):
    folder_label: str | None = None

class StructureRef(BaseModel):
    name: str
    source: str = Field(pattern="^(upload|rcsb|alphafold)$")
    pdb_id: str | None = None
    subpath: str | None = None
    format: str = "pdb"  # pdb | cif | mmcif
    description: str | None = None
    added_at: str | None = None


class StructureRefsUpdate(BaseModel):
    structure_refs: list[StructureRef]

class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    notes: str | None = None
    visibility: str | None = Field(default=None, pattern="^(private|unlisted|public)$")
    research_question: str | None = None
    hypotheses: list[str] | None = None
    objectives: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(planning|active|paused|done|archived)$")


class SpeciesRef(BaseModel):
    species_id: str | None = None
    ncbi_tax_id: int | None = None
    role: str = "primary"
    notes: str | None = None


class SpeciesRoleUpdate(BaseModel):
    role: str | None = None
    notes: str | None = None


class TargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern="^(gene|protein|exon|region|primer|marker|transcript|domain|other)$")
    gene_symbol: str | None = None
    accession: str | None = None
    region: str | None = None
    species_id: str | None = None
    notes: str | None = None
    sort_order: int = 0

class NarrativeUpdate(BaseModel):
    narrative: str


class WorkflowStepCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    kind: str = Field(pattern="^(manual|external_tool|backend_job|file_upload|review)$")
    status: str = Field(default="pending", pattern="^(pending|in_progress|blocked|done|skipped)$")
    sort_order: int = 0
    external_url: str | None = None
    external_tool_name: str | None = None
    backend_job_type: str | None = None
    backend_job_params: dict[str, Any] | None = None
    input_refs: list[dict[str, Any]] | None = None
    output_data: dict[str, Any] | None = None
    notes: str | None = None


class WorkflowStepUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    kind: str | None = Field(default=None, pattern="^(manual|external_tool|backend_job|file_upload|review)$")
    status: str | None = Field(default=None, pattern="^(pending|in_progress|blocked|done|skipped)$")
    sort_order: int | None = None
    external_url: str | None = None
    external_tool_name: str | None = None
    backend_job_type: str | None = None
    backend_job_params: dict[str, Any] | None = None
    input_refs: list[dict[str, Any]] | None = None
    output_data: dict[str, Any] | None = None
    notes: str | None = None


class WorkflowReorder(BaseModel):
    step_ids_in_order: list[str]

class TargetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = Field(
        default=None,
        pattern="^(gene|protein|exon|region|primer|marker|transcript|domain|other)$",
    )
    gene_symbol: str | None = None
    accession: str | None = None
    region: str | None = None
    species_id: str | None = None
    notes: str | None = None
    sort_order: int | None = None


# ─── helpers ───────────────────────────────────────────────────────────

async def _generate_unique_slug(base: str) -> str:
    client = service_client()
    slug = slugify(base) or "project"
    candidate = slug
    i = 2
    while True:
        result = (
            client.table("projects")
            .select("id")
            .eq("slug", candidate)
            .maybe_single()
            .execute()
        )
        if not result or not result.data:
            return candidate
        candidate = f"{slug}-{i}"
        i += 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── CRUD ──────────────────────────────────────────────────────────────

@router.get("")
async def list_projects(user: RequireUser) -> list[dict[str, Any]]:
    client = service_client()
    own = (
        client.table("projects")
        .select("*")
        .eq("owner_id", user.id)
        .order("updated_at", desc=True)
        .execute()
    )
    public = (
        client.table("projects")
        .select("*")
        .eq("visibility", "public")
        .neq("owner_id", user.id)
        .order("updated_at", desc=True)
        .limit(50)
        .execute()
    )
    return (own.data or []) + (public.data or [])


@router.post("")
async def create_project(payload: ProjectCreate, user: RequireUser) -> dict[str, Any]:
    slug = await _generate_unique_slug(payload.name)
    client = service_client()
    row = {
        "slug": slug,
        "name": payload.name,
        "description": payload.description,
        "notes": payload.notes,
        "visibility": payload.visibility,
        "owner_id": user.id,
        "research_question": payload.research_question,
        "hypotheses": payload.hypotheses or [],
        "objectives": payload.objectives or [],
        "status": payload.status,
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = client.table("projects").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="insert_failed")
    return result.data[0]


@router.get("/{slug}")
async def get_project(slug: str, user: RequireUser) -> dict[str, Any]:
    client = service_client()
    result = (
        client.table("projects")
        .select("*")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    project = result.data

    if not can_read_project_sync(project, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    # Species with roles.
    species_rows = (
        client.table("project_species")
        .select("species_id, role, notes, added_at, structure_refs, species(*)")
        .eq("project_id", project["id"])
        .order("added_at", desc=True)
        .execute()
    )
    species = []
    for row in species_rows.data or []:
        sp = row.get("species")
        if sp:
            sp["_role"] = row.get("role")
            sp["_notes"] = row.get("notes")
            sp["_added_at"] = row.get("added_at")
            sp["_structure_refs"] = row.get("structure_refs") or []
            species.append(sp)
    project["species"] = species
    # Targets.
    targets = (
        client.table("targets")
        .select("*")
        .eq("project_id", project["id"])
        .order("sort_order", desc=False)
        .order("created_at", desc=False)
        .execute()
    )
    project["targets"] = targets.data or []

    return project


@router.patch("/{slug}")
async def update_project(
    slug: str, payload: ProjectUpdate, user: RequireUser
) -> dict[str, Any]:
    client = service_client()
    existing = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="not_found")
    if existing.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")
    patch["updated_at"] = _now()

    result = (
        client.table("projects")
        .update(patch)
        .eq("id", existing.data["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="update_failed")
    return result.data[0]


@router.delete("/{slug}")
async def delete_project(slug: str, user: RequireUser) -> dict[str, str]:
    client = service_client()
    existing = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="not_found")
    if existing.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")
    client.table("projects").delete().eq("id", existing.data["id"]).execute()
    return {"status": "deleted"}


# ─── species membership ────────────────────────────────────────────────

@router.post("/{slug}/species")
async def add_species(slug: str, payload: SpeciesRef, user: RequireUser) -> dict[str, Any]:
    if not payload.species_id and not payload.ncbi_tax_id:
        raise HTTPException(status_code=400, detail="need_species_id_or_taxid")

    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    species_id = payload.species_id
    if not species_id:
        sp = (
            client.table("species")
            .select("id")
            .eq("ncbi_tax_id", payload.ncbi_tax_id)
            .maybe_single()
            .execute()
        )
        if not sp or not sp.data:
            raise HTTPException(status_code=404, detail="species_not_in_db_yet")
        species_id = sp.data["id"]

    client.table("project_species").upsert(
        {
            "project_id": project.data["id"],
            "species_id": species_id,
            "role": payload.role,
            "notes": payload.notes,
        },
        on_conflict="project_id,species_id",
    ).execute()

    return {"status": "added", "species_id": species_id, "role": payload.role}


@router.patch("/{slug}/species/{species_id}")
async def update_species_role(
    slug: str,
    species_id: str,
    payload: SpeciesRoleUpdate,
    user: RequireUser,
) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")

    (
        client.table("project_species")
        .update(patch)
        .eq("project_id", project.data["id"])
        .eq("species_id", species_id)
        .execute()
    )
    return {"status": "updated"}


@router.delete("/{slug}/species/{species_id}")
async def remove_species(slug: str, species_id: str, user: RequireUser) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    (
        client.table("project_species")
        .delete()
        .eq("project_id", project.data["id"])
        .eq("species_id", species_id)
        .execute()
    )
    return {"status": "removed"}


# ─── targets ───────────────────────────────────────────────────────────

@router.post("/{slug}/targets")
async def create_target(
    slug: str, payload: TargetCreate, user: RequireUser
) -> dict[str, Any]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    row = {
        "project_id": project.data["id"],
        **payload.model_dump(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = client.table("targets").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="insert_failed")
    return result.data[0]


@router.patch("/{slug}/targets/{target_id}")
async def update_target(
    slug: str,
    target_id: str,
    payload: TargetUpdate,
    user: RequireUser,
) -> dict[str, Any]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")
    patch["updated_at"] = _now()

    result = (
        client.table("targets")
        .update(patch)
        .eq("id", target_id)
        .eq("project_id", project.data["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="target_not_found")
    return result.data[0]


@router.delete("/{slug}/targets/{target_id}")
async def delete_target(
    slug: str, target_id: str, user: RequireUser
) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    (
        client.table("targets")
        .delete()
        .eq("id", target_id)
        .eq("project_id", project.data["id"])
        .execute()
    )
    return {"status": "deleted"}

# ─── narrative ─────────────────────────────────────────────────────────

@router.get("/{slug}/narrative")
async def get_narrative(slug: str, user: RequireUser) -> dict[str, Any]:
    client = service_client()
    result = (
        client.table("projects")
        .select("id, owner_id, visibility, narrative")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    p = result.data
    if not can_read_project_sync(p, user.id):
        raise HTTPException(status_code=403, detail="access_denied")
    return {"narrative": p.get("narrative") or ""}


@router.put("/{slug}/narrative")
async def update_narrative(
    slug: str, payload: NarrativeUpdate, user: RequireUser
) -> dict[str, str]:
    client = service_client()
    existing = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="not_found")
    if not can_edit_project_sync(existing.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")
    client.table("projects").update(
        {"narrative": payload.narrative, "updated_at": _now()}
    ).eq("id", existing.data["id"]).execute()
    return {"status": "saved"}


# ─── workflow steps ────────────────────────────────────────────────────

@router.get("/{slug}/steps")
async def list_steps(slug: str, user: RequireUser) -> list[dict[str, Any]]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id, visibility")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    p = project.data
    if not can_read_project_sync(p, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    result = (
        client.table("workflow_steps")
        .select("*")
        .eq("project_id", p["id"])
        .order("sort_order", desc=False)
        .order("created_at", desc=False)
        .execute()
    )
    return result.data or []


@router.post("/{slug}/steps")
async def create_step(
    slug: str, payload: WorkflowStepCreate, user: RequireUser
) -> dict[str, Any]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    row = {
        "project_id": project.data["id"],
        **{k: v for k, v in payload.model_dump().items() if v is not None},
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = client.table("workflow_steps").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="insert_failed")
    return result.data[0]


@router.patch("/{slug}/steps/{step_id}")
async def update_step(
    slug: str,
    step_id: str,
    payload: WorkflowStepUpdate,
    user: RequireUser,
) -> dict[str, Any]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")
    patch["updated_at"] = _now()

    result = (
        client.table("workflow_steps")
        .update(patch)
        .eq("id", step_id)
        .eq("project_id", project.data["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="step_not_found")
    return result.data[0]


@router.delete("/{slug}/steps/{step_id}")
async def delete_step(
    slug: str, step_id: str, user: RequireUser
) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    (
        client.table("workflow_steps")
        .delete()
        .eq("id", step_id)
        .eq("project_id", project.data["id"])
        .execute()
    )
    return {"status": "deleted"}


@router.post("/{slug}/steps/reorder")
async def reorder_steps(
    slug: str, payload: WorkflowReorder, user: RequireUser
) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    for i, step_id in enumerate(payload.step_ids_in_order):
        client.table("workflow_steps").update(
            {"sort_order": i, "updated_at": _now()}
        ).eq("id", step_id).eq("project_id", project.data["id"]).execute()

    return {"status": "reordered"}

@router.put("/{slug}/species/{species_id}/structure-refs")
async def update_structure_refs(
    slug: str,
    species_id: str,
    payload: StructureRefsUpdate,
    user: RequireUser,
) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects")
        .select("id, owner_id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")
    (
        client.table("project_species")
        .update({"structure_refs": [s.model_dump() for s in payload.structure_refs]})
        .eq("project_id", project.data["id"])
        .eq("species_id", species_id)
        .execute()
    )
    return {"status": "updated"}

@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str, user: RequireUser) -> dict[str, Any]:
    client = service_client()
    result = (
        client.table("jobs").select("*").eq("id", job_id).maybe_single().execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="job_not_found")
    j = result.data
    if j["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")
    return j
