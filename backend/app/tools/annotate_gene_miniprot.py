"""Annotate a gene in an unannotated genome using miniprot.

miniprot (https://github.com/lh3/miniprot) is the modern protein-to-genome
aligner by Heng Li. Compared to Exonerate:
  - faster (uses minimap2-style indexing)
  - more accurate for divergent species
  - reports frame shifts honestly without papering over them
  - cleaner GFF output

Pipeline (same shape as annotate_gene Exonerate):
  1. tblastn the query protein against the target genome → find region
  2. Extract that region (with flanks) for miniprot to work on
  3. Run miniprot --gff to predict gene model
  4. Parse GFF, slice the target FASTA at CDS coordinates → predicted CDS
  5. Translate to protein

We reuse the BLAST + region-extraction helpers from annotate_gene since they
do the same thing.

Install: `pacman -S miniprot` (Arch) or `conda install -c bioconda miniprot`.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from app.tools.annotate_gene import (
    _has, _require, _read_project_file,
    _tblastn_locate, _extract_region,
    _parse_fasta_to_seq, _revcomp_dna, _translate_dna,
)
from app.tools.registry import ToolDef, ParamDef, register


def _sha256(data: bytes | str) -> str:
    """Short hash for diagnostic comparison."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _extract_cds_from_miniprot_gff(
    gff_lines: list[str],
    target_seqs: dict[str, str],
) -> tuple[str, list[dict[str, Any]]]:
    """miniprot GFF has 'CDS' features with target coordinates (1-based,
    inclusive). Build the CDS by slicing the target FASTA."""
    cds_spans: list[dict[str, Any]] = []
    for line in gff_lines:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        if parts[2].upper() != "CDS":
            continue
        seqid = parts[0]
        try:
            start = int(parts[3])
            end = int(parts[4])
        except ValueError:
            continue
        strand = parts[6] if len(parts) > 6 else "+"
        cds_spans.append({"seqid": seqid, "start": start, "end": end, "strand": strand})

    if not cds_spans:
        return "", []

    cds_spans.sort(key=lambda s: s["start"])
    strands = {s["strand"] for s in cds_spans}
    is_minus = ("-" in strands)

    chunks: list[str] = []
    for span in cds_spans:
        target_seq = target_seqs.get(span["seqid"])
        if target_seq is None and len(target_seqs) == 1:
            # miniprot sometimes uses the seqid as written; fall back to the
            # single available sequence.
            target_seq = next(iter(target_seqs.values()))
        if target_seq is None:
            continue
        slice_seq = target_seq[span["start"] - 1 : span["end"]]
        chunks.append(slice_seq)

    cds_full = "".join(chunks).upper()
    if is_minus:
        cds_full = _revcomp_dna(cds_full)
    return cds_full, cds_spans


def _run_miniprot(query_protein_fasta: str, region_fasta: str) -> dict[str, Any]:
    _require("miniprot", "Install: pacman -S miniprot (or conda install -c bioconda miniprot)")

    target_seqs = _parse_fasta_to_seq(region_fasta)
    if not target_seqs:
        raise RuntimeError("region FASTA is empty or malformed")

    qfd, qpath = tempfile.mkstemp(prefix="miniprot_q_", suffix=".fasta")
    tfd, tpath = tempfile.mkstemp(prefix="miniprot_t_", suffix=".fasta")
    try:
        with os.fdopen(qfd, "w") as f:
            f.write(query_protein_fasta)
        with os.fdopen(tfd, "w") as f:
            f.write(region_fasta)

        # miniprot --gff produces gene predictions in GFF3 format.
        # -t for threads, --outs 0.95 for stringent output filtering.
        cmd = [
            "miniprot",
            "--gff",
            "-t", "4",
            "-N", "1",     # report at most 1 alignment per protein
            tpath, qpath,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"miniprot failed: {r.stderr or r.stdout}")
        out = r.stdout

        # GFF lines: skip comments (# prefix). miniprot also emits ##PAF lines.
        gff_lines: list[str] = []
        for line in out.split("\n"):
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 8:
                gff_lines.append(line)

        if not gff_lines:
            raise RuntimeError(
                "miniprot produced no GFF output. The query and target may be "
                "too divergent, or the region doesn't contain the gene."
            )

        cds_seq, cds_spans = _extract_cds_from_miniprot_gff(gff_lines, target_seqs)
        if not cds_seq:
            raise RuntimeError("miniprot's GFF had no 'CDS' features.")

        meta = {
            "target_id": cds_spans[0]["seqid"] if cds_spans else "",
            "cds_start": min(s["start"] for s in cds_spans),
            "cds_end": max(s["end"] for s in cds_spans),
            "strand": cds_spans[0]["strand"] if cds_spans else "+",
            "n_cds_features": len(cds_spans),
        }
        gff_text = "##gff-version 3\n" + "\n".join(gff_lines) + "\n"
        protein = _translate_dna(cds_seq)

        return {
            "cds_seq": cds_seq,
            "protein_seq": protein,
            "cds_length": len(cds_seq),
            "protein_length": len(protein),
            "gff": gff_text,
            "miniprot_meta": meta,
        }
    finally:
        for p in (qpath, tpath):
            try:
                os.unlink(p)
            except Exception:
                pass


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    from app.tools._naming import (
        resolve_file_with_species, tool_output_name, species_short_code,
    )

    # Accept either a single id (back-compat) or a list. The frontend now uses
    # `genome_file_ids` for multi-select; older runs may have `genome_file_id`.
    raw_ids: list[str] = []
    multi = params.get("genome_file_ids")
    if isinstance(multi, list):
        raw_ids = [str(x).strip() for x in multi if str(x).strip()]
    elif isinstance(multi, str):
        raw_ids = [x.strip() for x in multi.split(",") if x.strip()]
    legacy = (params.get("genome_file_id") or "").strip()
    if legacy and legacy not in raw_ids:
        raw_ids.insert(0, legacy)

    if not raw_ids:
        raise ValueError("at least one target genome required (genome_file_ids)")

    query_protein_text = (params.get("query_protein") or "").strip()
    gene_label = (params.get("gene_label") or "gene").strip()
    flank_bp = int(params.get("flank_bp", 5000))
    evalue = float(params.get("evalue", 1e-20))

    if not query_protein_text:
        raise ValueError(
            "query_protein required: paste FASTA-format protein from a related "
            "annotated species."
        )
    if not query_protein_text.startswith(">"):
        query_protein_text = ">query_protein\n" + query_protein_text

    per_genome_results: list[dict[str, Any]] = []
    all_output_files: list[dict[str, Any]] = []

    for genome_file_id in raw_ids:
        try:
            file_info = await asyncio.to_thread(
                resolve_file_with_species, genome_file_id, project_id,
            )
            genome_path = file_info["abs_path"]
            genome_name = file_info["name"]
            species_id = file_info["species_id"]
            species_code = file_info["species_code"] or species_short_code(
                None, fallback=re.sub(r"[^A-Za-z0-9]+", "", genome_name)[:4] or "Sp",
            )

            # Diagnostic genome hash
            genome_sha = ""
            try:
                with open(genome_path, "rb") as f:
                    h = hashlib.sha256()
                    while chunk := f.read(1024 * 1024):
                        h.update(chunk)
                    genome_sha = h.hexdigest()[:16]
            except Exception:
                pass

            locate = await asyncio.to_thread(
                _tblastn_locate, query_protein_text, genome_path, evalue, flank_bp,
            )
            region_fasta = await asyncio.to_thread(
                _extract_region, genome_path, locate["contig"],
                locate["region_start"], locate["region_end"],
            )
            region_sha = _sha256(region_fasta)

            annot = await asyncio.to_thread(_run_miniprot, query_protein_text, region_fasta)

            # Standardized output names
            prot_filename = tool_output_name("miniprot", species_code, gene_label, "protein", "fasta")
            cds_filename = tool_output_name("miniprot", species_code, gene_label, "cds", "fasta")
            region_filename = tool_output_name("miniprot", species_code, gene_label, "region", "fasta")
            gff_filename = tool_output_name("miniprot", species_code, gene_label, "", "gff3")

            cds_fasta = (
                f">{species_code}_{gene_label}_CDS "
                f"contig={locate['contig']} "
                f"region={locate['region_start']}-{locate['region_end']} "
                f"strand={locate['strand']} "
                f"length={annot['cds_length']}bp source=miniprot\n"
            )
            for i in range(0, len(annot["cds_seq"]), 60):
                cds_fasta += annot["cds_seq"][i:i + 60] + "\n"

            prot_fasta = (
                f">{species_code}_{gene_label}_protein "
                f"contig={locate['contig']} "
                f"length={annot['protein_length']}aa source=miniprot\n"
            )
            for i in range(0, len(annot["protein_seq"]), 60):
                prot_fasta += annot["protein_seq"][i:i + 60] + "\n"

            # Metadata shared by all 4 outputs from this genome
            base_meta = {
                "tool": "miniprot",
                "gene": gene_label,
                "species_id": species_id,
                "species_code": species_code,
                "source_genome_id": genome_file_id,
                "source_genome_name": genome_name,
                "category": "gene_output",
            }

            all_output_files.extend([
                {"name": prot_filename, "content": prot_fasta, "kind": "fasta",
                 "size": len(prot_fasta), "species_id": species_id,
                 "source_metadata": {**base_meta, "kind": "protein", "category": "protein_fasta"}},
                {"name": cds_filename, "content": cds_fasta, "kind": "fasta",
                 "size": len(cds_fasta), "species_id": species_id,
                 "source_metadata": {**base_meta, "kind": "cds", "category": "cds_fasta"}},
                {"name": region_filename, "content": region_fasta, "kind": "fasta",
                 "size": len(region_fasta), "species_id": species_id,
                 "source_metadata": {**base_meta, "kind": "region", "category": "genomic_region"}},
                {"name": gff_filename, "content": annot["gff"], "kind": "gff3",
                 "size": len(annot["gff"]), "species_id": species_id,
                 "source_metadata": {**base_meta, "kind": "gff", "category": "annotation"}},
            ])

            per_genome_results.append({
                "status": "ok",
                "genome_file_id": genome_file_id,
                "genome": genome_name,
                "species_code": species_code,
                "species_id": species_id,
                "genome_sha256_prefix": genome_sha,
                "contig": locate["contig"],
                "region_start": locate["region_start"],
                "region_end": locate["region_end"],
                "region_sha256_prefix": region_sha,
                "strand": locate["strand"],
                "n_hsps": locate["n_hsps"],
                "mean_blast_pident": locate["mean_pident"],
                "cds_length": annot["cds_length"],
                "cds_sha256_prefix": _sha256(annot["cds_seq"]),
                "protein_length": annot["protein_length"],
                "protein_sha256_prefix": _sha256(annot["protein_seq"]),
                "protein_first_30aa": annot["protein_seq"][:30],
                "protein_last_30aa": annot["protein_seq"][-30:] if len(annot["protein_seq"]) > 30 else "",
                "cds_first_60bp": annot["cds_seq"][:60],
            })
        except Exception as e:
            per_genome_results.append({
                "status": "failed",
                "genome_file_id": genome_file_id,
                "error": str(e),
            })

    n_ok = sum(1 for r in per_genome_results if r["status"] == "ok")
    return {
        "tool_used": "miniprot",
        "gene": gene_label,
        "n_genomes_processed": len(per_genome_results),
        "n_successful": n_ok,
        "n_failed": len(per_genome_results) - n_ok,
        "per_genome_results": per_genome_results,
        "output_files": all_output_files,
    }


register(ToolDef(
    id="annotate_gene_miniprot",
    label="Annotate gene (miniprot)",
    description=(
        "Project a protein from an annotated species onto one or more "
        "unannotated genomes using miniprot (Heng Li). Each output is tagged "
        "with the source species and named "
        "`miniprot.<SpCode>_<gene>_<kind>.fasta`. Select multiple genomes to "
        "annotate them all in one run."
    ),
    input_kind="none",
    params=[
        ParamDef(name="genome_file_ids", type="project_files_multi",
                 label="Target genomes (unannotated)",
                 default=[], kind="fasta", category="assembly_genome",
                 help="Pick one or more unannotated assemblies. Each is processed "
                      "sequentially and its outputs tagged with the source species."),
        ParamDef(name="query_protein", type="text_long",
                 label="Query protein (related annotated species)",
                 default="",
                 help="FASTA-format protein from e.g. Z. californianus. Same query is used "
                      "across all selected genomes."),
        ParamDef(name="gene_label", type="string", label="Gene label",
                 default="gene", help="Short symbol for output filenames (SF3B1, TP53)."),
        ParamDef(name="flank_bp", type="int", label="Flanking bp around BLAST hits",
                 default=5000, min=0, max=100000,
                 help="Bump to 20000 for large genes like SF3B1."),
        ParamDef(name="evalue", type="float", label="tblastn E-value cutoff",
                 default=1e-20, min=0.0, max=1.0),
    ],
    run=run,
))
