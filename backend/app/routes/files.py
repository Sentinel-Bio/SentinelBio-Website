"""Project file management endpoints."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth import RequireUser
from app.access import can_read_project_sync, can_edit_project_sync
from app.supabase_client import service_client
from app.files import store


router = APIRouter(prefix="/projects", tags=["files"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_mime_hint(filename: str, sample_bytes: bytes | None = None) -> str:
    """Determine the technical format of an uploaded file.
    Extension first, content-sniff as fallback for ambiguous cases."""
    name = filename.lower()
    # Strip a trailing .gz to look at the underlying type
    base = name[:-3] if name.endswith(".gz") else name

    if base.endswith((".fasta", ".fa", ".fna", ".faa", ".ffn", ".frn", ".aln", ".fas")):
        return "fasta"
    if base.endswith((".fastq", ".fq")):
        return "fastq"
    if base.endswith(".pdb"):
        return "pdb"
    if base.endswith((".cif", ".mmcif")):
        return "cif"
    if base.endswith((".newick", ".nwk", ".treefile", ".tree")):
        return "newick"
    if base.endswith((".gff", ".gff3", ".gtf")):
        return "annotation"
    if base.endswith((".gb", ".gbk", ".genbank")):
        return "genbank"
    if base.endswith(".json"):
        return "json"
    if base.endswith((".tsv", ".csv")):
        return "table"
    if name.endswith(".gz"):
        return "gzip"

    # Ambiguous (e.g. .txt, no extension) — sniff content.
    if sample_bytes is None:
        return "other"
    try:
        text_sample = sample_bytes[:2048].decode("utf-8", errors="ignore").lstrip()
    except Exception:
        return "other"

    if text_sample.startswith(">"):
        # FASTA. Could be DNA or protein — we don't separate at mime level;
        # use category() for that.
        return "fasta"
    if text_sample.startswith("@"):
        # Could be FASTQ; check structure (4 lines, second is sequence, third is '+')
        lines = text_sample.split("\n")
        if len(lines) >= 4 and lines[2].startswith("+"):
            return "fastq"
    if text_sample.startswith(("HEADER ", "ATOM  ", "HETATM", "REMARK")):
        return "pdb"
    if text_sample.startswith(("data_", "loop_", "_atom_site")):
        return "cif"
    if text_sample.startswith(("LOCUS  ", "LOCUS\t")):
        return "genbank"
    if text_sample.startswith("("):
        # Could be Newick — minimal sanity check
        if ";" in text_sample[:4096] and ":" in text_sample:
            return "newick"
    if text_sample.startswith(("##gff", "##sequence-region")):
        return "annotation"
    return "other"


def _detect_category(filename: str, mime_hint: str, sample_bytes: bytes | None = None) -> str:
    """Semantic role of the file. Computed from mime + filename heuristics.
    Used for picker filtering and UI grouping.

    Categories:
      - assembly_genome: whole-genome FASTA (NCBI assembly or similar)
      - gene_fasta:      single-gene or transcript FASTA
      - protein_fasta:   protein-sequence FASTA
      - cds_fasta:       coding-sequence FASTA
      - aligned_fasta:   multiple alignment (after MAFFT)
      - reads_fastq:     raw sequencing reads
      - structure:       3D structure (PDB/CIF)
      - tree:            phylogenetic tree
      - annotation:      GFF/GenBank
      - results:         JSON/TSV from tool outputs
      - other:           anything else
    """
    name = filename.lower()

    if mime_hint == "fastq":
        return "reads_fastq"
    if mime_hint == "pdb" or mime_hint == "cif":
        return "structure"
    if mime_hint == "newick":
        return "tree"
    if mime_hint in ("annotation", "genbank"):
        return "annotation"
    if mime_hint in ("json", "table"):
        return "results"

    if mime_hint == "fasta":
        # Heuristic by filename
        if "genomic" in name or "_assembly" in name or "wgs" in name:
            return "assembly_genome"
        if ".protein" in name or "_aa" in name or name.endswith(".faa") or name.endswith(".faa.gz"):
            return "protein_fasta"
        if ".cds" in name or "_cds" in name or name.endswith((".fna", ".ffn", ".frn")):
            return "cds_fasta"
        if "aligned" in name or "alignment" in name or "_aln" in name or name.endswith(".aln"):
            return "aligned_fasta"
        # Sniff: protein chars vs DNA chars in the first record
        if sample_bytes:
            try:
                txt = sample_bytes[:8192].decode("utf-8", errors="ignore")
            except Exception:
                txt = ""
            seq_chunk = []
            in_record = False
            for line in txt.split("\n"):
                if line.startswith(">"):
                    if in_record:
                        break
                    in_record = True
                    continue
                if in_record:
                    seq_chunk.append(line.strip().upper())
                    if sum(len(s) for s in seq_chunk) > 400:
                        break
            if seq_chunk:
                seq = "".join(seq_chunk)
                if seq:
                    dna_chars = sum(1 for c in seq if c in "ACGTN-")
                    if dna_chars / len(seq) > 0.9:
                        return "gene_fasta"
                    return "protein_fasta"
        return "gene_fasta"

    return "other"


# ─── Listing ────────────────────────────────────────────────────────

@router.get("/{slug}/files")
async def list_files(
    slug: str,
    user: RequireUser,
    kind: str | None = None,
    category: str | None = None,
    species_id: str | None = None,
    # Smart filters — match against source_metadata (including inferred values
    # for files that pre-date the structured naming convention).
    gene: str | None = None,
    meta_kind: str | None = None,
    tool: str | None = None,
    species_ids: str | None = None,
    name_substring: str | None = None,
) -> list[dict[str, Any]]:
    from app.tools._naming import enrich_file_metadata

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_read_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    q = (
        client.table("project_files").select("*")
        .eq("project_id", project.data["id"])
        .order("created_at", desc=True)
    )
    if kind:
        kinds = [k.strip() for k in kind.split(",") if k.strip()]
        if len(kinds) == 1:
            q = q.eq("mime_hint", kinds[0])
        elif kinds:
            q = q.in_("mime_hint", kinds)
    if species_id:
        q = q.eq("species_id", species_id)
    if species_ids:
        ids = [s.strip() for s in species_ids.split(",") if s.strip()]
        if ids:
            q = q.in_("species_id", ids)
    result = q.execute()

    rows = result.data or []

    # Decorate each row: ensure category is set, and add inferred metadata
    # from the filename for files that don't already have it.
    enriched: list[dict[str, Any]] = []
    for r in rows:
        meta = r.get("source_metadata") or {}
        if not meta.get("category"):
            meta["category"] = _detect_category(r["name"], r["mime_hint"], None)
            r["source_metadata"] = meta
        enriched.append(enrich_file_metadata(r))

    # Now apply smart filters against the enriched metadata.
    def _matches(row: dict[str, Any]) -> bool:
        m = row.get("source_metadata") or {}
        if category and m.get("category") != category:
            return False
        if gene:
            g = (m.get("gene") or "").upper()
            if g != gene.upper():
                return False
        if meta_kind:
            k = (m.get("kind") or "").lower()
            if k != meta_kind.lower():
                return False
        if tool:
            t = (m.get("tool") or "").lower()
            if t != tool.lower():
                return False
        if name_substring:
            if name_substring.lower() not in row.get("name", "").lower():
                return False
        return True

    return [r for r in enriched if _matches(r)]


@router.get("/{slug}/files/facets")
async def file_facets(
    slug: str,
    user: RequireUser,
) -> dict[str, Any]:
    """Return distinct filter values present in the project's files.

    Used by the smart file picker to populate filter dropdowns. Includes
    inferred metadata (so old files contribute too)."""
    from app.tools._naming import enrich_file_metadata

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_read_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    result = (
        client.table("project_files")
        .select("name, source_metadata, species_id, mime_hint")
        .eq("project_id", project.data["id"])
        .execute()
    )
    rows = result.data or []

    genes: set[str] = set()
    kinds: set[str] = set()
    tools: set[str] = set()
    species_ids: set[str] = set()
    categories: set[str] = set()

    for r in rows:
        enriched = enrich_file_metadata(r)
        m = enriched.get("source_metadata") or {}
        if g := m.get("gene"):
            genes.add(g)
        if k := m.get("kind"):
            kinds.add(k)
        if t := m.get("tool"):
            tools.add(t)
        if c := m.get("category"):
            categories.add(c)
        if sid := r.get("species_id"):
            species_ids.add(sid)

    # Resolve species_ids to readable info (name + short code)
    species_info: list[dict[str, Any]] = []
    if species_ids:
        sres = (
            client.table("species")
            .select("id, scientific_name, common_name")
            .in_("id", list(species_ids))
            .execute()
        )
        from app.tools._naming import species_short_code
        for s in (sres.data or []):
            species_info.append({
                "id": s["id"],
                "name": s["scientific_name"],
                "common": s.get("common_name"),
                "code": species_short_code(s["scientific_name"]),
            })

    return {
        "genes": sorted(genes),
        "kinds": sorted(kinds),
        "tools": sorted(tools),
        "categories": sorted(categories),
        "species": sorted(species_info, key=lambda x: x.get("code") or ""),
        "n_files": len(rows),
    }


# ─── Upload ─────────────────────────────────────────────────────────

@router.post("/{slug}/files")
async def upload_file(
    slug: str,
    user: RequireUser,
    file: UploadFile = File(...),
    species_id: str | None = Form(None),
) -> dict[str, Any]:
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    meta = await store.write_upload(
        project.data["id"], file, subdir="uploads",
        filename=file.filename or "upload",
        return_head=8192,
    )
    sample = meta.pop("head_bytes", None)
    mime_hint = _detect_mime_hint(meta["name"], sample)
    category = _detect_category(meta["name"], mime_hint, sample)

    row = {
        "project_id": project.data["id"],
        "owner_id": user.id,
        "name": meta["name"],
        "storage_path": meta["storage_path"],
        "size": meta["size"],
        "sha256": meta["sha256"],
        "mime_hint": mime_hint,
        "source": "upload",
        "source_metadata": {"category": category},
        "species_id": species_id,
        "created_at": _now(),
    }
    result = client.table("project_files").insert(row).execute()
    if not result.data:
        # Roll back the file write — orphaned file otherwise.
        try:
            store.delete(project.data["id"], meta["storage_path"])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="insert_failed")
    return result.data[0]


# ─── Download (stream) ─────────────────────────────────────────────

@router.get("/{slug}/files/{file_id}/download")
async def download_file(slug: str, file_id: str, user: RequireUser) -> FileResponse:
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_read_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    f = (
        client.table("project_files").select("*")
        .eq("id", file_id).eq("project_id", project.data["id"])
        .maybe_single().execute()
    )
    if not f or not f.data:
        raise HTTPException(status_code=404, detail="file_not_found")

    try:
        path = store.absolute_path(project.data["id"], f.data["storage_path"])
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="file_missing_on_disk")

    return FileResponse(
        path=str(path),
        filename=f.data["name"],
        media_type="application/octet-stream",
    )


# ─── Bulk download as archive ────────────────────────────────────

@router.post("/{slug}/files/bulk-download")
async def bulk_download_files(
    slug: str,
    payload: dict[str, Any],
    user: RequireUser,
):
    """Stream a zip or tar.gz archive containing the requested files.

    payload: {file_ids: [...], format: 'zip' | 'tar.gz'}
    Filenames in the archive are the display names (post-rename). Collisions
    are deduplicated by appending '_2', '_3', etc.
    """
    from fastapi.responses import StreamingResponse

    file_ids = payload.get("file_ids") or []
    archive_format = (payload.get("format") or "zip").lower()
    if not file_ids:
        raise HTTPException(status_code=400, detail="no_file_ids")
    if archive_format not in ("zip", "tar.gz", "tar"):
        raise HTTPException(status_code=400, detail="invalid_format")

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_read_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    # Fetch all requested files; constrain to this project for safety.
    rows_res = (
        client.table("project_files").select("*")
        .eq("project_id", project.data["id"])
        .in_("id", file_ids)
        .execute()
    )
    rows = rows_res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="no_files_found")

    # Resolve absolute paths + dedupe display names.
    seen_names: dict[str, int] = {}
    entries: list[tuple[str, str]] = []   # (arcname, abs_path)
    for r in rows:
        try:
            abs_path = str(store.absolute_path(project.data["id"], r["storage_path"]))
        except FileNotFoundError:
            continue
        if not os.path.exists(abs_path):
            continue

        base = r["name"]
        if base in seen_names:
            seen_names[base] += 1
            stem, _, ext = base.rpartition(".")
            if stem:
                arc = f"{stem}_{seen_names[base]}.{ext}"
            else:
                arc = f"{base}_{seen_names[base]}"
        else:
            seen_names[base] = 1
            arc = base
        entries.append((arc, abs_path))

    if not entries:
        raise HTTPException(status_code=410, detail="all_files_missing_on_disk")

    project_slug = slug
    archive_name = f"{project_slug}_files.{archive_format}"

    if archive_format == "zip":
        import zipfile, io

        def zip_stream():
            # Stream chunks via a BytesIO buffer that we drain after each entry.
            buf = io.BytesIO()
            zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED)
            try:
                for arcname, abs_path in entries:
                    zf.write(abs_path, arcname=arcname)
                    # Drain buffer to keep memory bounded for the next entry.
                    chunk = buf.getvalue()
                    if chunk:
                        yield chunk
                        buf.seek(0)
                        buf.truncate(0)
            finally:
                zf.close()
            final = buf.getvalue()
            if final:
                yield final

        return StreamingResponse(
            zip_stream(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{archive_name}"'},
        )
    else:
        # tar.gz (or tar)
        import tarfile, io

        mode = "w|gz" if archive_format == "tar.gz" else "w|"

        def tar_stream():
            buf = io.BytesIO()
            tf = tarfile.open(fileobj=buf, mode=mode)
            try:
                for arcname, abs_path in entries:
                    tf.add(abs_path, arcname=arcname, recursive=False)
                    chunk = buf.getvalue()
                    if chunk:
                        yield chunk
                        buf.seek(0)
                        buf.truncate(0)
            finally:
                tf.close()
            final = buf.getvalue()
            if final:
                yield final

        return StreamingResponse(
            tar_stream(),
            media_type="application/gzip" if archive_format == "tar.gz" else "application/x-tar",
            headers={"Content-Disposition": f'attachment; filename="{archive_name}"'},
        )


# ─── Inline text (for tools, viewers) ─────────────────────────────

@router.get("/{slug}/files/{file_id}/text")
async def get_file_text(slug: str, file_id: str, user: RequireUser) -> dict[str, Any]:
    """Return file content as text. Capped at 100MB."""
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_read_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    f = (
        client.table("project_files").select("*")
        .eq("id", file_id).eq("project_id", project.data["id"])
        .maybe_single().execute()
    )
    if not f or not f.data:
        raise HTTPException(status_code=404, detail="file_not_found")

    try:
        text = store.read_text(project.data["id"], f.data["storage_path"])
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="file_missing_on_disk")
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))

    return {
        "id": f.data["id"],
        "name": f.data["name"],
        "size": f.data["size"],
        "mime_hint": f.data["mime_hint"],
        "text": text,
    }


# ─── Delete ────────────────────────────────────────────────────────

@router.patch("/{slug}/files/{file_id}")
async def rename_file(
    slug: str,
    file_id: str,
    payload: dict[str, Any],
    user: RequireUser,
) -> dict[str, Any]:
    """Rename a project file — updates both the DB name AND the on-disk filename.

    The storage path is updated so everything stays in sync. Tool references
    by file ID continue to work transparently."""
    import os
    import re
    from pathlib import Path

    new_name = (payload.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="name_required")
    if len(new_name) > 200:
        raise HTTPException(status_code=400, detail="name_too_long")

    # Sanitize filename: only alphanumerics, dots, underscores, hyphens
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", new_name)
    if not sanitized:
        raise HTTPException(status_code=400, detail="invalid_filename")

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    file_row = (
        client.table("project_files")
        .select("*")
        .eq("id", file_id)
        .eq("project_id", project.data["id"])
        .maybe_single().execute()
    )
    if not file_row or not file_row.data:
        raise HTTPException(status_code=404, detail="file_not_found")

    old_path = file_row.data["storage_path"]
    old_name = file_row.data["name"]

    # If the name hasn't actually changed, save the I/O
    if sanitized == old_name:
        return file_row.data

    # Construct new storage path: keep directory, swap filename
    path_obj = Path(old_path)
    new_path = str(path_obj.parent / sanitized)

    # Rename on disk
    files_dir = os.environ.get("SENTINEL_FILES_DIR", "/tmp/sentinel_files")
    old_full = os.path.join(files_dir, old_path)
    new_full = os.path.join(files_dir, new_path)

    try:
        if not os.path.exists(old_full):
            # File row points to a path that no longer exists on disk. This
            # happens for old uploads that pre-date directory namespacing, or
            # if files were manually moved/deleted. Update DB-only — the
            # storage_path stays stale but the display name updates so tools
            # referencing this file by ID continue working.
            update_result = (
                client.table("project_files")
                .update({"name": sanitized})
                .eq("id", file_id)
                .execute()
            )
            if not update_result.data:
                raise HTTPException(status_code=500, detail="db_rename_failed")
            r = update_result.data[0]
            r["_warning"] = (
                "renamed in DB only; file is missing on disk and could not "
                "be moved"
            )
            return r
        os.rename(old_full, new_full)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"rename_failed: {str(e)}"
        )

    # Update DB
    result = (
        client.table("project_files")
        .update({"name": sanitized, "storage_path": new_path})
        .eq("id", file_id)
        .execute()
    )
    if not result.data:
        # Rename failed in DB; try to rollback the filesystem rename
        try:
            os.rename(new_full, old_full)
        except Exception:
            pass  # Best effort; user is already getting an error
        raise HTTPException(status_code=500, detail="db_rename_failed")

    return result.data[0]


@router.delete("/{slug}/files/{file_id}")
async def delete_file(slug: str, file_id: str, user: RequireUser) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    f = (
        client.table("project_files").select("*")
        .eq("id", file_id).eq("project_id", project.data["id"])
        .maybe_single().execute()
    )
    if not f or not f.data:
        raise HTTPException(status_code=404, detail="file_not_found")

    try:
        store.delete(project.data["id"], f.data["storage_path"])
    except Exception:
        pass  # File missing on disk — still delete the row
    client.table("project_files").delete().eq("id", file_id).execute()
    return {"status": "deleted"}


# ─── Server-side NCBI assembly fetch (async via tool_runs) ────────

class NcbiFetchRequest(BaseModel):
    accession: str
    taxid: int
    species_id: str | None = None


@router.post("/{slug}/files/fetch-ncbi-assembly")
async def fetch_ncbi_assembly(
    slug: str,
    payload: NcbiFetchRequest,
    user: RequireUser,
) -> dict[str, Any]:
    """Enqueue an async download of an assembly from NCBI.

    Returns a tool_runs row (status='queued'). The frontend polls runs to
    see when it completes. Multiple downloads can run concurrently in the
    tool worker.

    Previously this was synchronous and blocked one uvicorn worker for
    minutes; now it returns immediately."""
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    # Dedupe: if we already have this accession in project, return the existing file.
    existing = (
        client.table("project_files")
        .select("*")
        .eq("project_id", project.data["id"])
        .eq("source", "ncbi")
        .execute()
    )
    for f in (existing.data or []):
        meta = f.get("source_metadata") or {}
        if meta.get("accession") == payload.accession and meta.get("kind") != "genomic_region":
            return {
                "status": "exists",
                "file": f,
                "accession": payload.accession,
                "message": f"Assembly {payload.accession} already in project — skipping download.",
            }

    row = {
        "project_id": project.data["id"],
        "owner_id": user.id,
        "tool": "ncbi_fetch_assembly",
        "label": f"Fetch {payload.accession}",
        "status": "queued",
        "progress": 0,
        "inputs": {
            "species_id": payload.species_id,
            "owner_id": user.id,
        },
        "params": {
            "accession": payload.accession,
            "taxid": payload.taxid,
            "species_id": payload.species_id,
            "owner_id": user.id,
        },
        "created_at": _now(),
        "updated_at": _now(),
    }
    result = client.table("tool_runs").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="enqueue_failed")
    return {
        "status": "queued",
        "run_id": result.data[0]["id"],
        "tool": "ncbi_fetch_assembly",
        "accession": payload.accession,
    }


# ─── Gene fetch from NCBI ───────────────────────────────────────────

class GeneFetchRequest(BaseModel):
    gene_symbol: str
    taxid: int
    species_id: str | None = None
    # All outputs are optional — user picks what they want, at least one:
    save_mrna: bool = False
    save_genomic_region: bool = False
    genomic_flank_bp: int = 2000
    extract_cds: bool = False
    save_protein: bool = False
    save_genbank: bool = False


def _existing_ncbi_file(
    client, project_id: str, gene: str, taxid: int, kind: str,
) -> dict | None:
    """Return existing project_file with matching gene/taxid/kind, or None.
    Used to dedupe NCBI fetches — saves bandwidth + NCBI API calls."""
    res = (
        client.table("project_files")
        .select("*")
        .eq("project_id", project_id)
        .eq("source", "ncbi")
        .execute()
    )
    for f in (res.data or []):
        meta = f.get("source_metadata") or {}
        if (
            meta.get("gene") == gene
            and meta.get("taxid") == taxid
            and meta.get("kind") == kind
        ):
            return f
    return None


def _save_file(
    client, project_id: str, owner_id: str, species_id: str | None,
    *, filename: str, content: bytes, mime_hint: str, source: str,
    source_metadata: dict,
) -> dict:
    """Helper: write a file to disk + insert project_files row."""
    meta = store.write_bytes(
        project_id, subdir="ncbi", filename=filename, content=content,
    )
    row = {
        "project_id": project_id,
        "owner_id": owner_id,
        "name": meta["name"],
        "storage_path": meta["storage_path"],
        "size": meta["size"],
        "sha256": meta["sha256"],
        "mime_hint": mime_hint,
        "source": source,
        "source_metadata": source_metadata,
        "species_id": species_id,
        "created_at": _now(),
    }
    result = client.table("project_files").insert(row).execute()
    if not result.data:
        # Rollback the file write
        try:
            store.delete(project_id, meta["storage_path"])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="file_insert_failed")
    return result.data[0]


def _extract_cds_from_genbank(genbank_text: str) -> dict[str, Any] | None:
    """Parse a GenBank record and return the first CDS feature with its
    spliced nucleotide sequence + translation.

    Returns: {accession, cds_seq, protein_seq, cds_coords, gene}
    or None if no CDS found.

    Uses Biopython for the parse — robust for joined CDS (multi-exon)."""
    from io import StringIO
    from Bio import SeqIO

    try:
        record = next(SeqIO.parse(StringIO(genbank_text), "genbank"))
    except (StopIteration, Exception) as e:
        raise ValueError(f"GenBank parse failed: {e}")

    cds_feature = None
    for f in record.features:
        if f.type == "CDS":
            cds_feature = f
            break
    if cds_feature is None:
        return None

    # cds_feature.location.extract() handles joined locations (splicing) and
    # strand orientation correctly. This IS the spliced CDS as it appears
    # in mRNA, in the coding direction.
    cds_seq_obj = cds_feature.location.extract(record.seq)
    cds_seq = str(cds_seq_obj).upper()

    # Translation: prefer the /translation qualifier (NCBI's canonical answer)
    # over translating ourselves — they've already handled alternative starts,
    # selenocysteine, etc.
    qual_translation = cds_feature.qualifiers.get("translation", [None])[0]
    if qual_translation:
        protein_seq = qual_translation
    else:
        protein_seq = str(cds_seq_obj.translate(to_stop=True))

    gene = cds_feature.qualifiers.get("gene", [None])[0]
    product = cds_feature.qualifiers.get("product", [None])[0]

    return {
        "accession": record.id,
        "cds_seq": cds_seq,
        "protein_seq": protein_seq,
        "cds_coords": str(cds_feature.location),
        "gene": gene,
        "product": product,
    }


@router.post("/{slug}/files/fetch-ncbi-gene")
async def fetch_ncbi_gene(
    slug: str,
    payload: GeneFetchRequest,
    user: RequireUser,
) -> dict[str, Any]:
    """Fetch gene artifacts from NCBI. All outputs are optional — user picks.
    At least one must be selected."""
    from app.sources.ncbi import (
        fetch_gene_fasta, fetch_gene_genbank, fetch_gene_genomic_region,
        _find_best_mrna_accession,
    )

    if not any([
        payload.save_mrna, payload.save_genomic_region,
        payload.extract_cds, payload.save_protein, payload.save_genbank,
    ]):
        raise HTTPException(
            status_code=400,
            detail="select at least one output type",
        )

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if not can_edit_project_sync(project.data, user.id):
        raise HTTPException(status_code=403, detail="access_denied")

    proj_id = project.data["id"]
    sym = payload.gene_symbol
    tx = payload.taxid
    saved_files: list[dict] = []
    warnings: list[str] = []

    # ── Genomic DNA region ──────────────────────────────────────────
    if payload.save_genomic_region:
        existing = _existing_ncbi_file(client, proj_id, sym, tx, "genomic_region")
        if existing:
            saved_files.append(existing)
            warnings.append(
                f"Genomic region for {sym} already in project — using existing file."
            )
        else:
            region = fetch_gene_genomic_region(tx, sym, flank_bp=payload.genomic_flank_bp)
            if not region:
                warnings.append(
                    f"Genomic region for {sym} (taxid {tx}) not found — "
                    "NCBI may lack a chromosomal assembly for this species."
                )
            else:
                saved_files.append(_save_file(
                    client, proj_id, user.id, payload.species_id,
                    filename=f"{sym}_{tx}.genomic.fasta",
                    content=region["fasta"].encode("utf-8"),
                    mime_hint="fasta",
                    source="ncbi",
                    source_metadata={
                        "gene": sym, "taxid": tx, "kind": "genomic_region",
                        "category": "gene_fasta",
                        "accession": region["accession"],
                        "gene_start": region["gene_start"],
                        "gene_stop": region["gene_stop"],
                        "flank_bp": region["flank_bp"],
                        "strand": region["strand"],
                    },
                ))

    # ── mRNA, CDS, protein, GenBank ────────────────────────────────
    need_mrna_record = any([
        payload.save_mrna, payload.extract_cds,
        payload.save_protein, payload.save_genbank,
    ])

    # If anything needs the mRNA record, resolve which accession we'll use upfront
    # so we can tell the user what was selected.
    selected_accession: str | None = None
    selected_title: str | None = None
    if need_mrna_record:
        hit = _find_best_mrna_accession(tx, sym)
        if hit:
            selected_accession, selected_title = hit
        else:
            warnings.append(
                f"No mRNA record found for {sym} (taxid {tx}) — "
                "mRNA / CDS / protein / GenBank outputs skipped."
            )
            need_mrna_record = False

    if need_mrna_record and selected_accession:
        mrna_text: str | None = None
        genbank_text: str | None = None
        parsed: dict[str, Any] | None = None

        mrna_text = fetch_gene_fasta(tx, sym)
        if not mrna_text:
            warnings.append(
                f"mRNA record for {sym} (taxid {tx}) not found — "
                "mRNA / CDS / protein / GenBank outputs skipped."
            )
        else:
            need_annotation = payload.extract_cds or payload.save_protein or payload.save_genbank
            if need_annotation:
                genbank_text = fetch_gene_genbank(tx, sym)
                if not genbank_text:
                    warnings.append("GenBank record unavailable — CDS/protein outputs skipped.")
                else:
                    try:
                        parsed = _extract_cds_from_genbank(genbank_text)
                    except ValueError as e:
                        warnings.append(f"GenBank parse failed: {e}")
                    if parsed is None:
                        warnings.append(
                            "No CDS feature found (may be non-coding) — CDS/protein skipped."
                        )

            if payload.save_mrna:
                ex = _existing_ncbi_file(client, proj_id, sym, tx, "mrna")
                if ex:
                    saved_files.append(ex)
                    warnings.append(f"mRNA for {sym} already in project — using existing file.")
                else:
                    saved_files.append(_save_file(
                        client, proj_id, user.id, payload.species_id,
                        filename=f"{sym}_{tx}.mrna.fasta",
                        content=mrna_text.encode("utf-8"),
                        mime_hint="fasta",
                        source="ncbi",
                        source_metadata={
                            "gene": sym, "taxid": tx,
                            "kind": "mrna", "category": "gene_fasta",
                        },
                    ))

            if payload.save_genbank and genbank_text:
                ex = _existing_ncbi_file(client, proj_id, sym, tx, "genbank")
                if ex:
                    saved_files.append(ex)
                    warnings.append(f"GenBank for {sym} already in project — using existing file.")
                else:
                    saved_files.append(_save_file(
                        client, proj_id, user.id, payload.species_id,
                        filename=f"{sym}_{tx}.gb",
                        content=genbank_text.encode("utf-8"),
                        mime_hint="genbank",
                        source="ncbi",
                        source_metadata={
                            "gene": sym, "taxid": tx,
                            "kind": "genbank", "category": "annotation",
                        },
                    ))

            if payload.extract_cds and parsed:
                ex = _existing_ncbi_file(client, proj_id, sym, tx, "cds")
                if ex:
                    saved_files.append(ex)
                    warnings.append(f"CDS for {sym} already in project — using existing file.")
                else:
                    cds_header = (
                        f">{sym}_{tx}_CDS "
                        f"accession={parsed['accession']} "
                        f"coords={parsed['cds_coords']} "
                        f"length={len(parsed['cds_seq'])}bp\n"
                    )
                    cds_body = "\n".join(
                        parsed["cds_seq"][i:i + 60]
                        for i in range(0, len(parsed["cds_seq"]), 60)
                    )
                    saved_files.append(_save_file(
                        client, proj_id, user.id, payload.species_id,
                        filename=f"{sym}_{tx}.cds.fasta",
                        content=(cds_header + cds_body + "\n").encode("utf-8"),
                        mime_hint="fasta",
                        source="ncbi",
                        source_metadata={
                            "gene": sym, "taxid": tx,
                            "kind": "cds", "category": "cds_fasta",
                            "accession": parsed["accession"],
                            "cds_coords": parsed["cds_coords"],
                        },
                    ))

            if payload.save_protein and parsed:
                ex = _existing_ncbi_file(client, proj_id, sym, tx, "protein")
                if ex:
                    saved_files.append(ex)
                    warnings.append(f"Protein for {sym} already in project — using existing file.")
                else:
                    prot_header = (
                        f">{sym}_{tx}_protein "
                        f"accession={parsed['accession']} "
                        f"length={len(parsed['protein_seq'])}aa "
                        f"product=\"{parsed.get('product') or ''}\"\n"
                    )
                    prot_body = "\n".join(
                        parsed["protein_seq"][i:i + 60]
                        for i in range(0, len(parsed["protein_seq"]), 60)
                    )
                    saved_files.append(_save_file(
                        client, proj_id, user.id, payload.species_id,
                        filename=f"{sym}_{tx}.protein.fasta",
                        content=(prot_header + prot_body + "\n").encode("utf-8"),
                        mime_hint="fasta",
                        source="ncbi",
                        source_metadata={
                            "gene": sym, "taxid": tx,
                            "kind": "protein", "category": "protein_fasta",
                            "accession": parsed["accession"],
                        },
                    ))

    return {
        "saved_files": saved_files,
        "warnings": warnings,
        "gene": sym,
        "taxid": tx,
        "n_saved": len(saved_files),
        "selected_accession": selected_accession,
        "selected_title": selected_title,
    }
