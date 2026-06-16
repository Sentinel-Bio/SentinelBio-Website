"""Shared input loaders for tools.

Tools receive `inputs` with either:
  - `upload_path`: a server tmp path (legacy multipart upload)
  - `file_ids`: list of project_files IDs to read

This module unifies access so each tool doesn't have to.

It also rewrites FASTA headers to prepend the species name. NCBI gene FASTAs
have headers like `>NM_001126112.3 Homo sapiens tumor protein p53 (TP53)...`
which IQ-TREE truncates to just `NM_001126112.3` in the tree — losing species
identity. By rewriting to `>Homo_sapiens_NM_001126112.3 ...` the species name
survives to the tree.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.files import store as filestore
from app.supabase_client import service_client


def _safe_name(s: str) -> str:
    """Sanitize a string for use in FASTA headers / tree leaves.
    Whitespace and IQ-TREE/Newick-meaningful characters become underscores."""
    # Newick reserves: ( ) , ; : space tab
    # Plus we ban quotes/brackets to keep things simple.
    return re.sub(r"[\s(),;:\[\]'\"]+", "_", s.strip()).strip("_")


def _fetch_files_with_species(file_ids: list[str]) -> list[dict[str, Any]]:
    """Get project_files rows plus species scientific_name (when linked)."""
    client = service_client()
    files_res = (
        client.table("project_files")
        .select("id, storage_path, name, species_id, source, source_metadata")
        .in_("id", file_ids)
        .execute()
    )
    rows = files_res.data or []

    species_ids = [r["species_id"] for r in rows if r.get("species_id")]
    name_by_id: dict[str, str] = {}
    if species_ids:
        sp_res = (
            client.table("species")
            .select("id, scientific_name")
            .in_("id", species_ids)
            .execute()
        )
        for s in (sp_res.data or []):
            name_by_id[s["id"]] = s["scientific_name"]

    for r in rows:
        r["species_name"] = name_by_id.get(r.get("species_id") or "")
    return rows


def _rewrite_fasta_headers(text: str, species_name: str | None, fallback: str) -> str:
    """For each '>HEADER...' line, prepend the species name (if known) to the
    sequence ID so downstream tools (MAFFT, IQ-TREE) keep species identity in
    their output labels.

    Examples:
      >NM_001126112.3 Homo sapiens tumor protein p53 ...
      ->
      >Homo_sapiens_NM_001126112.3 tumor protein p53 ...

      >XR_001234.1 PREDICTED ... (no species link in DB, no obvious species in header)
      ->
      >SF3B1_9643_XR_001234.1 PREDICTED ...
    """
    if not text.strip():
        return text

    label_prefix = _safe_name(species_name) if species_name else _safe_name(fallback)
    out_lines = []
    for line in text.splitlines():
        if not line.startswith(">"):
            out_lines.append(line)
            continue
        body = line[1:].strip()
        if not body:
            out_lines.append(f">{label_prefix}")
            continue
        # Split into accession + rest of description.
        first, _, rest = body.partition(" ")
        # Avoid double-prefixing if user already labeled.
        if first.startswith(label_prefix + "_"):
            out_lines.append(line)
            continue
        new_id = f"{label_prefix}_{_safe_name(first)}"
        out_lines.append(f">{new_id} {rest}".rstrip())
    return "\n".join(out_lines) + "\n"


def get_input_text(inputs: dict[str, Any], project_id: str) -> str:
    """Concatenated input text. Rewrites FASTA headers to include species names
    so phylogenetic tree leaves and alignment column labels are interpretable.

    Pass `inputs['rewrite_headers'] = False` to disable.
    """
    rewrite = inputs.get("rewrite_headers", True)

    file_ids = inputs.get("file_ids") or []
    if file_ids:
        rows = _fetch_files_with_species(file_ids)
        # Preserve client-side order, not DB order.
        rows_by_id = {r["id"]: r for r in rows}
        ordered = [rows_by_id[fid] for fid in file_ids if fid in rows_by_id]

        parts: list[str] = []
        for row in ordered:
            raw = filestore.read_text(project_id, row["storage_path"]).strip()
            if not raw:
                continue
            # Skip header rewriting for tool outputs — they've already been
            # processed once and headers carry the species labels we want.
            if rewrite and row.get("source") != "tool":
                fallback = Path(row["name"]).stem  # e.g. SF3B1_9643
                raw = _rewrite_fasta_headers(raw, row.get("species_name"), fallback)
            parts.append(raw.strip())
        return "\n".join(parts) + "\n"

    # Legacy inline text.
    if "fasta_text" in inputs:
        return inputs["fasta_text"]

    # Legacy upload_path (cutadapt, fastqc).
    upload_path = inputs.get("upload_path")
    if upload_path:
        return Path(upload_path).read_text()

    raise ValueError("no input found: expected file_ids, fasta_text, or upload_path")


def get_input_bytes(inputs: dict[str, Any], project_id: str) -> bytes:
    """Binary input (FastQ etc). No header rewriting."""
    file_ids = inputs.get("file_ids") or []
    if file_ids:
        client = service_client()
        result = (
            client.table("project_files").select("storage_path")
            .in_("id", file_ids).execute()
        )
        parts = []
        for row in (result.data or []):
            parts.append(filestore.read_bytes(project_id, row["storage_path"]))
        return b"".join(parts)

    upload_path = inputs.get("upload_path")
    if upload_path:
        return Path(upload_path).read_bytes()

    raise ValueError("no binary input found")


def get_input_metadata(inputs: dict[str, Any], project_id: str) -> list[dict[str, Any]]:
    """Source-file metadata for inputs, for descriptive output naming.
    Returns a list of {id, name, species_name, species_id} dicts in input order."""
    file_ids = inputs.get("file_ids") or []
    if not file_ids:
        return []
    rows = _fetch_files_with_species(file_ids)
    rows_by_id = {r["id"]: r for r in rows}
    out = []
    for fid in file_ids:
        r = rows_by_id.get(fid)
        if not r:
            continue
        out.append({
            "id": r["id"],
            "name": r["name"],
            "species_name": r.get("species_name"),
            "species_id": r.get("species_id"),
            "source_metadata": r.get("source_metadata") or {},
        })
    return out


def build_output_label(
    metadata: list[dict[str, Any]],
    tool_prefix: str,
    extension: str = "fasta",
) -> str:
    """Construct a descriptive output filename.
    
    Examples:
        mafft_TP53_3species.fasta    (when all inputs share a gene)
        mafft_SF3B1_Hsap_Mmus.fasta  (when 2 species, distinct)
        mafft_5files.fasta           (fallback)
    """
    # Try to infer a shared gene from source_metadata.
    genes = {
        m["source_metadata"].get("gene")
        for m in metadata
        if m.get("source_metadata", {}).get("gene")
    }
    genes.discard(None)
    gene_part = next(iter(genes)) if len(genes) == 1 else None

    # Try to make a short species summary.
    species_names = [m.get("species_name") for m in metadata if m.get("species_name")]
    if len(species_names) > 0 and len(species_names) <= 4:
        # Use 4-letter abbreviations: "Homo sapiens" -> "Hsap"
        abbrevs = []
        for sn in species_names:
            tokens = sn.split()
            if len(tokens) >= 2:
                abbrevs.append((tokens[0][:1] + tokens[1][:3]).capitalize())
            else:
                abbrevs.append(_safe_name(sn)[:6])
        species_part = "_".join(abbrevs)
    else:
        species_part = f"{len(metadata)}species"

    if gene_part:
        return f"{tool_prefix}_{gene_part}_{species_part}.{extension}"
    return f"{tool_prefix}_{species_part}.{extension}"
