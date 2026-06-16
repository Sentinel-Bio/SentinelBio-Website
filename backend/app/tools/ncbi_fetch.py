"""Asynchronous NCBI assembly fetch.

Pulls a whole-genome FASTA from NCBI to server storage. Runs in the tool
worker (not the HTTP request handler) so it can take minutes without
blocking a uvicorn worker, and multiple downloads can run concurrently.

The created project_files row is written by this tool itself (not by the
generic worker output-file pipeline) because the file is too big to round-trip
through `output_files: [{content: ...}]` — we'd OOM. Instead we set
`output_files = []` and insert the row directly here.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.files import store as filestore
from app.supabase_client import service_client
from app.tools.registry import ToolDef, ParamDef, register


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _download_assembly(
    project_id: str,
    owner_id: str,
    accession: str,
    taxid: int,
    species_id: str | None,
) -> dict[str, Any]:
    """Stream the assembly from NCBI to disk, decompressing gzip on the fly.
    Returns a dict with metadata and the inserted project_files row's id.

    Two paths:
      - GCF_/GCA_ accessions  → Assembly FTP (gzipped genomic FASTA, streamed).
      - anything else (NC_, etc.) → nuccore efetch (viral/organelle genomes that
        have no Assembly FTP entry). Fetched whole; these are small (kb–Mb).
    """
    from app.sources.ncbi import fetch_assembly_fasta_url, fetch_nuccore_fasta

    target_dir = filestore.project_dir(project_id) / "ncbi"
    target_dir.mkdir(parents=True, exist_ok=True)

    is_assembly = accession.startswith(("GCF_", "GCA_"))

    sha = hashlib.sha256()
    size = 0

    if is_assembly:
        url = fetch_assembly_fasta_url(accession)
        if not url:
            raise RuntimeError(f"could not resolve NCBI URL for {accession}")
        filename = f"{accession}_genomic.fna"
        target = target_dir / filename
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sentinel-bio/1.0"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                with gzip.GzipFile(fileobj=resp) as gz:
                    with target.open("wb") as out:
                        while True:
                            chunk = gz.read(1024 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                            sha.update(chunk)
                            size += len(chunk)
        except Exception:
            if target.exists():
                target.unlink()
            raise
    else:
        # nuccore record (viral/organelle). Fetch whole via efetch.
        fasta = fetch_nuccore_fasta(accession)
        if not fasta:
            raise RuntimeError(f"nuccore efetch returned nothing for {accession}")
        filename = f"{accession}_genomic.fna"
        target = target_dir / filename
        content_bytes = fasta.encode("utf-8")
        target.write_bytes(content_bytes)
        sha.update(content_bytes)
        size = len(content_bytes)

    # Insert project_files row directly. We bypass the worker's generic
    # output_files pipeline because we don't want to send 2 GB through it.
    client = service_client()
    row = {
        "project_id": project_id,
        "owner_id": owner_id,
        "name": filename,
        "storage_path": str(target.relative_to(filestore.project_dir(project_id))),
        "size": size,
        "sha256": sha.hexdigest(),
        "mime_hint": "fasta",
        "source": "ncbi",
        "source_metadata": {"accession": accession, "taxid": taxid},
        "species_id": species_id,
        "created_at": _now(),
    }
    inserted = client.table("project_files").insert(row).execute()
    file_id = inserted.data[0]["id"] if inserted.data else None
    if not file_id:
        if target.exists():
            target.unlink()
        raise RuntimeError("project_files insert failed")

    return {
        "accession": accession,
        "taxid": taxid,
        "filename": filename,
        "size_bytes": size,
        "sha256": sha.hexdigest(),
        # Stash the file_id so frontend can navigate to it after job completes.
        # We set output_file_ids manually in run() since output_files=[] would
        # leave that array empty.
        "file_id": file_id,
    }


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    # Worker passes job dict in; we want owner_id from there.
    owner_id = inputs.get("owner_id") or params.get("owner_id")
    accession = (params.get("accession") or "").strip()
    taxid = int(params.get("taxid") or 0)
    species_id = inputs.get("species_id") or params.get("species_id")

    if not accession:
        raise ValueError("accession required")
    if not taxid:
        raise ValueError("taxid required")
    if not owner_id:
        raise ValueError("owner_id required")

    result = await asyncio.to_thread(
        _download_assembly, project_id, owner_id, accession, taxid, species_id,
    )
    # Surface the file_id under output_file_ids so the frontend's existing
    # "view saved files" UI picks it up.
    return {
        **result,
        "output_files": [],          # bypass worker's content-roundtrip
        "output_file_ids": [result["file_id"]],
    }


register(ToolDef(
    id="ncbi_fetch_assembly",
    label="Fetch NCBI assembly",
    description=(
        "Download a whole-genome FASTA from NCBI to project storage. "
        "Runs asynchronously — you can queue several at once."
    ),
    input_kind="none",   # frontend collects params, no file input needed
    params=[
        ParamDef(name="accession", type="string", label="Assembly accession",
                 default="", help="e.g. GCF_020975775.1"),
        ParamDef(name="taxid", type="int", label="NCBI TaxID",
                 default=0, help="The species TaxID this assembly belongs to."),
    ],
    run=run,
))
