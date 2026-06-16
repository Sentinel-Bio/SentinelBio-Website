"""Project file management endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth import RequireUser
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
) -> list[dict[str, Any]]:
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id, visibility")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["visibility"] == "private" and project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="private")

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
    result = q.execute()
    # Decorate each row with the computed category so the frontend has it
    # without re-running the heuristic. Cheap — file is already in memory.
    rows = result.data or []
    for r in rows:
        # If insert added category to source_metadata (newer uploads), use it;
        # else recompute from filename+mime_hint.
        meta = r.get("source_metadata") or {}
        if not meta.get("category"):
            meta["category"] = _detect_category(r["name"], r["mime_hint"], None)
            r["source_metadata"] = meta
    if category:
        rows = [r for r in rows if (r.get("source_metadata") or {}).get("category") == category]
    return rows


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
    if project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

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
    if project.data["visibility"] == "private" and project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="private")

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
    if project.data["visibility"] == "private" and project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="private")

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

@router.delete("/{slug}/files/{file_id}")
async def delete_file(slug: str, file_id: str, user: RequireUser) -> dict[str, str]:
    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

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
    if project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

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


# ─── Gene FASTA fetch (small, fast) ────────────────────────────────

class GeneFetchRequest(BaseModel):
    gene_symbol: str
    taxid: int
    species_id: str | None = None


@router.post("/{slug}/files/fetch-ncbi-gene")
async def fetch_ncbi_gene(
    slug: str,
    payload: GeneFetchRequest,
    user: RequireUser,
) -> dict[str, Any]:
    from app.sources.ncbi import fetch_gene_fasta

    client = service_client()
    project = (
        client.table("projects").select("id, owner_id")
        .eq("slug", slug).maybe_single().execute()
    )
    if not project or not project.data:
        raise HTTPException(status_code=404, detail="project_not_found")
    if project.data["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="not_owner")

    fasta_text = fetch_gene_fasta(payload.taxid, payload.gene_symbol)
    filename = f"{payload.gene_symbol}_{payload.taxid}.fasta"

    meta = store.write_bytes(
        project.data["id"], subdir="ncbi", filename=filename,
        content=fasta_text.encode("utf-8"),
    )

    row = {
        "project_id": project.data["id"],
        "owner_id": user.id,
        "name": meta["name"],
        "storage_path": meta["storage_path"],
        "size": meta["size"],
        "sha256": meta["sha256"],
        "mime_hint": "fasta",
        "source": "ncbi",
        "source_metadata": {"gene": payload.gene_symbol, "taxid": payload.taxid},
        "species_id": payload.species_id,
        "created_at": _now(),
    }
    result = client.table("project_files").insert(row).execute()
    return result.data[0]
