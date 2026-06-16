"""Extract a region from a whole-genome FASTA.

Takes one FASTA file (typically a large assembly) and a region spec, produces
a new gene-sized FASTA containing just that region. Output is then suitable
for MAFFT etc.

Region spec accepts two forms:

  1. Coordinates:  `NC_000017.11:7565097-7590856`
     Sequence ID + start-end (1-based, inclusive on both ends).

  2. Gene symbol:  `TP53`
     Resolves via NCBI: find the gene in the species (using the assembly's
     taxid from source_metadata), get its coordinates on the matching contig,
     then extract.

Two implementations of the extraction itself:

  - If `samtools` is available, use `samtools faidx`. Fastest, indexed.
  - Fall back to a pure-Python linear scan.

Single-input tool (input_kind='fasta_upload' would force multipart upload;
we use the same pattern as MAFFT's 'aligned_fasta' but enforce a single file).
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register


# ─── Region resolution ──────────────────────────────────────────────

_COORD_RE = re.compile(r"^([^\s:]+):(\d+)-(\d+)$")


def _parse_coord_spec(spec: str) -> tuple[str, int, int] | None:
    m = _COORD_RE.match(spec.strip())
    if not m:
        return None
    seq_id, start, end = m.group(1), int(m.group(2)), int(m.group(3))
    if start < 1 or end < start:
        raise ValueError(f"bad coords: start={start} end={end}")
    return seq_id, start, end


def _resolve_gene_to_coords(gene_symbol: str, taxid: int) -> tuple[str, int, int]:
    """Find a gene's chromosome + coordinates via NCBI Entrez."""
    from Bio import Entrez

    Entrez.email = "sentinel-bio@example.com"
    Entrez.tool = "sentinel-bio"

    # Find the gene ID.
    handle = Entrez.esearch(
        db="gene",
        term=f"{gene_symbol}[Gene Name] AND txid{taxid}[Organism:noexp]",
        retmax=5,
    )
    record = Entrez.read(handle)
    handle.close()
    gene_ids = record.get("IdList", [])
    if not gene_ids:
        raise ValueError(f"gene {gene_symbol} not found for taxid {taxid}")

    # Get gene summary, which has genomic location.
    handle = Entrez.esummary(db="gene", id=gene_ids[0])
    summary = Entrez.read(handle)
    handle.close()

    docs = summary.get("DocumentSummarySet", {}).get("DocumentSummary", [])
    if not docs:
        raise ValueError(f"no summary for gene {gene_symbol}")

    g = docs[0]
    # GenomicInfo has list of locations across assemblies.
    gi_list = g.get("GenomicInfo") or []
    if not gi_list:
        raise ValueError(
            f"gene {gene_symbol} has no genomic coordinates in NCBI (annotation not yet linked)"
        )
    gi = gi_list[0]  # primary assembly
    chr_acc = gi.get("ChrAccVer", "")
    start = int(gi.get("ChrStart", 0))
    stop = int(gi.get("ChrStop", 0))
    if not chr_acc or start == stop == 0:
        raise ValueError(f"gene {gene_symbol}: incomplete coordinates")
    # NCBI ChrStart/ChrStop are 0-based; convert to 1-based inclusive.
    lo, hi = sorted([start, stop])
    return chr_acc, lo + 1, hi + 1


# ─── Extraction ─────────────────────────────────────────────────────

def _has_samtools() -> bool:
    return shutil.which("samtools") is not None


def _extract_with_samtools(fasta_path: str, seq_id: str, start: int, end: int) -> str:
    """Use samtools faidx. Builds .fai index on first use."""
    fai = fasta_path + ".fai"
    if not os.path.exists(fai):
        # samtools faidx <file> builds the index
        r = subprocess.run(
            ["samtools", "faidx", fasta_path],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"samtools index failed: {r.stderr}")

    region = f"{seq_id}:{start}-{end}"
    r = subprocess.run(
        ["samtools", "faidx", fasta_path, region],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"samtools faidx failed: {r.stderr}")
    if not r.stdout.strip():
        raise RuntimeError(f"samtools returned empty for {region}")
    return r.stdout


def _extract_pure_python(fasta_path: str, seq_id: str, start: int, end: int) -> str:
    """Linear scan, no dependencies. Slow but always works.
    For 2 GB files this takes ~30s on an SSD; samtools is preferred."""
    if start < 1 or end < start:
        raise ValueError(f"bad range {start}-{end}")
    length = end - start + 1

    seq_buf: list[str] = []
    in_target = False
    consumed = 0  # bases already iterated through within target
    with open(fasta_path, "r") as f:
        for line in f:
            if line.startswith(">"):
                if in_target:
                    break  # finished target sequence
                # Match by exact ID (first token after >)
                header_id = line[1:].split(None, 1)[0]
                if header_id == seq_id:
                    in_target = True
                continue
            if not in_target:
                continue

            chunk = line.strip()
            if not chunk:
                continue
            chunk_start = consumed + 1
            chunk_end = consumed + len(chunk)

            if chunk_end < start:
                consumed += len(chunk)
                continue
            if chunk_start > end:
                break

            # Slice
            local_start = max(0, start - chunk_start)
            local_end = len(chunk) if chunk_end <= end else end - chunk_start + 1
            seq_buf.append(chunk[local_start:local_end])
            consumed += len(chunk)

    if not seq_buf:
        raise RuntimeError(f"sequence '{seq_id}' not found or range out of bounds")

    seq = "".join(seq_buf)
    if len(seq) < length:
        # Either truncated end or seq shorter than expected. Note it.
        pass
    # Re-wrap to 60 cols (standard).
    wrapped = "\n".join(seq[i:i + 60] for i in range(0, len(seq), 60))
    return f">{seq_id}:{start}-{end}\n{wrapped}\n"


# ─── Tool entry ─────────────────────────────────────────────────────

def _run_extract(
    fasta_path: str,
    region_spec: str,
    taxid: int | None,
    species_name: str | None,
) -> dict[str, Any]:
    # Decide if spec is coords or a gene symbol.
    coords = _parse_coord_spec(region_spec)
    if coords:
        seq_id, start, end = coords
        gene_label = None
    else:
        if not taxid:
            raise ValueError(
                f"'{region_spec}' is not coords (CHR:start-end), and no taxid available "
                f"to resolve it as a gene symbol. Link the source file to a species first."
            )
        seq_id, start, end = _resolve_gene_to_coords(region_spec.strip(), taxid)
        gene_label = region_spec.strip()

    # Extract.
    if _has_samtools():
        raw = _extract_with_samtools(fasta_path, seq_id, start, end)
        extractor = "samtools"
    else:
        raw = _extract_pure_python(fasta_path, seq_id, start, end)
        extractor = "python"

    # Replace the header to include species + gene + coords (so downstream
    # tools see something meaningful and IQ-TREE leaves are interpretable).
    sp_part = re.sub(r"[\s(),;:]+", "_", (species_name or "").strip())
    parts = [sp_part] if sp_part else []
    if gene_label:
        parts.append(gene_label)
    parts.append(f"{seq_id}_{start}-{end}")
    new_id = "_".join(parts)

    # Replace first header line with our new ID; preserve rest as-is.
    lines = raw.splitlines()
    if lines and lines[0].startswith(">"):
        lines[0] = f">{new_id} extracted by sentinel-bio from {fasta_path.split('/')[-1]}"
    rewritten = "\n".join(lines) + "\n"

    region_length = end - start + 1
    out_name = (
        f"{(gene_label or 'region')}_{sp_part or 'unknown'}_{region_length}bp.fasta"
        .replace("__", "_")
    )

    return {
        "seq_id": seq_id,
        "start": start,
        "end": end,
        "length": region_length,
        "gene": gene_label,
        "extractor": extractor,
        "output_files": [
            {"name": out_name, "content": rewritten, "kind": "fasta", "size": len(rewritten)},
        ],
    }


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    """`inputs` should contain:
      - file_ids: [single ID of the source whole-genome FASTA]
      - source_files: list with that file's metadata (species_id, source_metadata.taxid, etc.)
    `params`:
      - region: 'TP53' or 'NC_000017.11:7565097-7590856'
    """
    from app.files import store as filestore
    from app.supabase_client import service_client

    region_spec = (params.get("region") or "").strip()
    if not region_spec:
        raise ValueError("region required: gene symbol (TP53) or coords (NC_000017:7565097-7590856)")

    file_ids = inputs.get("file_ids") or []
    if len(file_ids) != 1:
        raise ValueError("region-extract takes exactly 1 input FASTA")
    fid = file_ids[0]

    # Look up the file's storage path + linked species.
    client = service_client()
    fres = (
        client.table("project_files")
        .select("id, storage_path, name, species_id, source_metadata")
        .eq("id", fid)
        .maybe_single().execute()
    )
    if not fres or not fres.data:
        raise ValueError(f"input file not found: {fid}")
    f = fres.data
    fasta_abs = str(filestore.absolute_path(project_id, f["storage_path"]))

    taxid = (f.get("source_metadata") or {}).get("taxid")
    species_name: str | None = None
    if f.get("species_id"):
        sres = client.table("species").select("scientific_name, ncbi_tax_id").eq("id", f["species_id"]).maybe_single().execute()
        if sres and sres.data:
            species_name = sres.data.get("scientific_name")
            taxid = taxid or sres.data.get("ncbi_tax_id")

    return await asyncio.to_thread(_run_extract, fasta_abs, region_spec, taxid, species_name)


register(ToolDef(
    id="region_extract",
    label="Extract region (gene / coords)",
    description=(
        "Extract a gene region or coordinate range from a whole-genome FASTA. "
        "Output is a small FASTA suitable for MAFFT. "
        "Accepts gene symbols (TP53, SF3B1) — auto-resolved via NCBI."
    ),
    input_kind="aligned_fasta",  # single FASTA picker, reusing existing UI
    params=[
        ParamDef(
            name="region", type="string", label="Region",
            default="",
            help=(
                "Gene symbol (e.g. 'TP53') or coordinates 'CHR:start-end' "
                "(e.g. 'NC_000017.11:7565097-7590856'). "
                "Gene resolution needs the source file linked to a species."
            ),
        ),
    ],
    run=run,
))
