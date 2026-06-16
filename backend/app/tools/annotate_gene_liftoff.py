"""Annotate genes in an unannotated genome by transferring annotations from
a reference using Liftoff.

Liftoff (https://github.com/agshumate/Liftoff) lifts gene annotations from a
reference genome to a target by aligning the genes with minimap2 and copying
coordinates. The CDS extracted is the literal target DNA at the lifted
coordinates — no protein-driven correction, no frame-shift papering.

Use this when:
  - You have a reference GFF (NCBI RefSeq for the reference species)
  - Reference and target are reasonably close (<50 Mya divergence works well)

Compared to miniprot/Exonerate:
  - Liftoff is reference-faithful — transfers coords, doesn't predict
  - Cannot find lineage-specific genes (only what's in the reference)
  - Catches multi-gene context (paralogs, synteny)

Inputs:
  - Target genome FASTA (unannotated)
  - Reference genome FASTA (e.g. Z. californianus)
  - Reference GFF (e.g. from NCBI RefSeq for Z. cal)
  - Optional: gene_filter (extract only specific genes from the lifted output)

Install: `pip install Liftoff` + `pacman -S minimap2`.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.files import store as filestore
from app.supabase_client import service_client
from app.tools.annotate_gene import (
    _has, _require, _read_project_file,
    _parse_fasta_to_seq, _revcomp_dna, _translate_dna,
)
from app.tools.registry import ToolDef, ParamDef, register


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _file_sha256(path: str) -> str:
    """Hash a file's contents. Used for diagnostic comparison."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return ""


def _read_text_file(file_id: str, project_id: str) -> tuple[str, str, str]:
    """Returns (abs_path, name, storage_path) for a project_file. For GFFs;
    similar to _read_project_file but doesn't expect FASTA."""
    return _read_project_file(file_id, project_id)


def _run_liftoff(
    target_genome_path: str,
    reference_genome_path: str,
    reference_gff_path: str,
    work_dir: str,
    extra_args: list[str] | None = None,
) -> tuple[str, str]:
    """Run Liftoff. Returns (output_gff_path, stdout)."""
    _require("liftoff", "Install: pip install Liftoff")
    _require("minimap2", "Install: pacman -S minimap2")

    out_gff = os.path.join(work_dir, "lifted.gff3")
    unmapped = os.path.join(work_dir, "unmapped.txt")

    cmd = [
        "liftoff",
        "-g", reference_gff_path,
        "-o", out_gff,
        "-u", unmapped,
        "-dir", os.path.join(work_dir, "intermediate"),
        "-polish",   # extra polishing pass for edge cases
        target_genome_path,
        reference_genome_path,
    ]
    if extra_args:
        cmd.extend(extra_args)

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if r.returncode != 0:
        raise RuntimeError(
            f"liftoff failed (rc={r.returncode}):\n"
            f"stdout tail: {r.stdout[-2000:]}\n"
            f"stderr tail: {r.stderr[-2000:]}"
        )
    if not os.path.exists(out_gff):
        raise RuntimeError(f"liftoff completed but {out_gff} missing")
    return out_gff, r.stdout[-3000:]


def _parse_lifted_gff_for_gene(
    gff_path: str,
    gene_symbol: str,
) -> dict[str, Any] | None:
    """Find the requested gene in the lifted GFF. Returns:
      {seqid, gene_start, gene_end, strand, mrna_id, cds_spans: [{start, end, strand}]}
    or None if gene not present.
    """
    gene_target_re = re.compile(
        rf'\b(?:Name|gene|gene_name)=["\']?{re.escape(gene_symbol)}["\']?(?:;|$|\b)',
        re.IGNORECASE,
    )

    gene_feature: dict[str, Any] | None = None
    transcripts: dict[str, dict[str, Any]] = {}
    cds_by_transcript: dict[str, list[dict[str, Any]]] = {}

    with open(gff_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            seqid, source, feature, start, end, score, strand, phase, attrs = parts[:9]
            try:
                start_i = int(start)
                end_i = int(end)
            except ValueError:
                continue

            attr_dict: dict[str, str] = {}
            for kv in attrs.split(";"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    attr_dict[k.strip()] = v.strip().strip('"').strip("'")

            f_type = feature.lower()

            if f_type == "gene":
                if not gene_target_re.search(attrs):
                    continue
                # First-match wins; if there are paralogs / multiple lifts,
                # we take the first (usually best-scored).
                if gene_feature is not None:
                    continue
                gene_feature = {
                    "id": attr_dict.get("ID", ""),
                    "seqid": seqid,
                    "start": start_i,
                    "end": end_i,
                    "strand": strand,
                    "name": attr_dict.get("Name") or attr_dict.get("gene"),
                }
            elif f_type in ("mrna", "transcript"):
                parent = attr_dict.get("Parent", "")
                if gene_feature and parent == gene_feature["id"]:
                    tx_id = attr_dict.get("ID", "")
                    transcripts[tx_id] = {
                        "id": tx_id,
                        "seqid": seqid,
                        "start": start_i,
                        "end": end_i,
                        "strand": strand,
                    }
            elif f_type == "cds":
                parent = attr_dict.get("Parent", "")
                cds_by_transcript.setdefault(parent, []).append({
                    "seqid": seqid,
                    "start": start_i,
                    "end": end_i,
                    "strand": strand,
                })

    if gene_feature is None:
        return None

    # Pick the longest transcript (most complete annotation).
    best_tx_id = ""
    best_total_cds = 0
    for tx_id, spans in cds_by_transcript.items():
        if tx_id not in transcripts:
            continue
        total = sum(s["end"] - s["start"] + 1 for s in spans)
        if total > best_total_cds:
            best_total_cds = total
            best_tx_id = tx_id

    if not best_tx_id:
        # No CDS associated with any transcript of this gene.
        return {
            **gene_feature,
            "mrna_id": "",
            "cds_spans": [],
        }

    return {
        **gene_feature,
        "mrna_id": best_tx_id,
        "cds_spans": cds_by_transcript[best_tx_id],
    }


def _build_cds_from_target(
    target_genome_path: str,
    seqid: str,
    cds_spans: list[dict[str, Any]],
) -> str:
    """Read target FASTA chunks at each CDS coordinate. Reverse-complement if minus."""
    if not cds_spans:
        return ""

    cds_spans = sorted(cds_spans, key=lambda s: s["start"])
    strands = {s["strand"] for s in cds_spans}
    is_minus = ("-" in strands)

    chunks: list[str] = []
    if _has("samtools"):
        # samtools faidx is the fastest way to do random-access reads.
        fai = target_genome_path + ".fai"
        if not os.path.exists(fai):
            r = subprocess.run(
                ["samtools", "faidx", target_genome_path],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                raise RuntimeError(f"samtools faidx failed: {r.stderr}")
        for span in cds_spans:
            region = f"{seqid}:{span['start']}-{span['end']}"
            r = subprocess.run(
                ["samtools", "faidx", target_genome_path, region],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode != 0:
                raise RuntimeError(f"samtools faidx failed: {r.stderr}")
            # Strip header, concatenate sequence lines
            lines = r.stdout.splitlines()
            seq = "".join(ln for ln in lines if not ln.startswith(">"))
            chunks.append(seq)
    else:
        # Python fallback. Slow on big genomes but works.
        seqs = _parse_fasta_to_seq(open(target_genome_path).read())
        target_seq = seqs.get(seqid)
        if target_seq is None:
            raise RuntimeError(f"sequence '{seqid}' not in target genome")
        for span in cds_spans:
            chunks.append(target_seq[span["start"] - 1 : span["end"]])

    cds_full = "".join(chunks).upper()
    if is_minus:
        cds_full = _revcomp_dna(cds_full)
    return cds_full


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    target_genome_id = (params.get("target_genome_id") or "").strip()
    reference_genome_id = (params.get("reference_genome_id") or "").strip()
    reference_gff_id = (params.get("reference_gff_id") or "").strip()
    gene_symbol = (params.get("gene_symbol") or "").strip()

    if not all([target_genome_id, reference_genome_id, reference_gff_id, gene_symbol]):
        raise ValueError(
            "Need all of: target_genome_id, reference_genome_id, "
            "reference_gff_id, gene_symbol"
        )

    target_path, target_name, _ = await asyncio.to_thread(
        _read_project_file, target_genome_id, project_id,
    )
    reference_path, reference_name, _ = await asyncio.to_thread(
        _read_project_file, reference_genome_id, project_id,
    )
    ref_gff_path, ref_gff_name, _ = await asyncio.to_thread(
        _read_text_file, reference_gff_id, project_id,
    )

    target_sha = await asyncio.to_thread(_file_sha256, target_path)

    work_dir = tempfile.mkdtemp(prefix="liftoff_")
    try:
        lifted_gff_path, stdout_tail = await asyncio.to_thread(
            _run_liftoff, target_path, reference_path, ref_gff_path, work_dir,
        )
        with open(lifted_gff_path) as f:
            lifted_gff_text = f.read()

        gene_data = _parse_lifted_gff_for_gene(lifted_gff_path, gene_symbol)
        if gene_data is None:
            raise RuntimeError(
                f"Gene '{gene_symbol}' not found in the lifted annotation. "
                "The reference GFF may not contain this gene under that name, "
                "or Liftoff couldn't map it to the target."
            )
        if not gene_data["cds_spans"]:
            raise RuntimeError(
                f"Gene '{gene_symbol}' was found but has no CDS features in "
                "the lifted GFF. May be a non-coding gene or lift failed."
            )

        cds_seq = await asyncio.to_thread(
            _build_cds_from_target,
            target_path, gene_data["seqid"], gene_data["cds_spans"],
        )
        if not cds_seq:
            raise RuntimeError(f"failed to extract CDS bases from target")

        protein = _translate_dna(cds_seq)

        species_short = re.sub(r"[^A-Za-z0-9._-]+", "_",
                              os.path.splitext(target_name)[0])[:32]
        cds_filename = f"{species_short}_{gene_symbol}.cds.fasta"
        prot_filename = f"{species_short}_{gene_symbol}.protein.fasta"
        gff_filename = f"{species_short}_{gene_symbol}.gff3"

        # Subset the lifted GFF to just this gene + its features
        gene_lines = []
        wanted_ids = {gene_data["id"], gene_data["mrna_id"]}
        with open(lifted_gff_path) as f:
            for line in f:
                if line.startswith("#"):
                    gene_lines.append(line.rstrip("\n"))
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 9:
                    continue
                attrs = parts[8]
                if any(f"ID={wid}" in attrs or f"Parent={wid}" in attrs
                       for wid in wanted_ids if wid):
                    gene_lines.append(line.rstrip("\n"))
        gene_gff = "\n".join(gene_lines) + "\n"

        cds_header = (
            f">{species_short}_{gene_symbol}_CDS "
            f"contig={gene_data['seqid']} "
            f"region={gene_data['start']}-{gene_data['end']} "
            f"strand={gene_data['strand']} "
            f"length={len(cds_seq)}bp source=liftoff\n"
        )
        cds_body = "\n".join(cds_seq[i:i + 60] for i in range(0, len(cds_seq), 60))
        cds_fasta = cds_header + cds_body + "\n"

        prot_header = (
            f">{species_short}_{gene_symbol}_protein "
            f"contig={gene_data['seqid']} "
            f"length={len(protein)}aa source=liftoff\n"
        )
        prot_body = "\n".join(protein[i:i + 60] for i in range(0, len(protein), 60))
        prot_fasta = prot_header + prot_body + "\n"

        return {
            "tool_used": "liftoff",
            "gene": gene_symbol,
            "source_genome": target_name,
            "target_genome_sha256_prefix": target_sha,
            "reference_genome": reference_name,
            "reference_gff": ref_gff_name,
            "lifted_seqid": gene_data["seqid"],
            "lifted_start": gene_data["start"],
            "lifted_end": gene_data["end"],
            "strand": gene_data["strand"],
            "n_cds_features": len(gene_data["cds_spans"]),
            "cds_length": len(cds_seq),
            "cds_sha256_prefix": _sha256(cds_seq),
            "protein_length": len(protein),
            "protein_sha256_prefix": _sha256(protein),
            "protein_first_30aa": protein[:30],
            "protein_last_30aa": protein[-30:] if len(protein) > 30 else "",
            "cds_first_60bp": cds_seq[:60],
            "stdout_tail": stdout_tail,
            "output_files": [
                {"name": prot_filename, "content": prot_fasta, "kind": "fasta", "size": len(prot_fasta)},
                {"name": cds_filename, "content": cds_fasta, "kind": "fasta", "size": len(cds_fasta)},
                {"name": gff_filename, "content": gene_gff, "kind": "text", "size": len(gene_gff)},
            ],
        }
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


register(ToolDef(
    id="annotate_gene_liftoff",
    label="Annotate gene (Liftoff)",
    description=(
        "Transfer a gene's annotation from a reference to a target genome "
        "via Liftoff. CDS is extracted from the target's actual DNA at "
        "lifted coordinates — no protein-driven sequence correction. "
        "Requires a reference GFF (from NCBI RefSeq). "
        "Best for closely-related species (<50 Mya divergence). "
        "Install: pip install Liftoff && pacman -S minimap2."
    ),
    input_kind="none",
    params=[
        ParamDef(name="target_genome_id", type="project_file_fasta",
                 label="Target genome (unannotated)",
                 default="",
                 help="The assembly to annotate (e.g. A. gazella, A. townsendi)."),
        ParamDef(name="reference_genome_id", type="project_file_fasta",
                 label="Reference genome FASTA",
                 default="",
                 help="A related species' genome with a known GFF (e.g. Z. californianus)."),
        ParamDef(name="reference_gff_id", type="project_file",
                 label="Reference GFF",
                 default="",
                 help="The annotation file for the reference. Get from NCBI "
                      "RefSeq (look for GCF_<accession>_genomic.gff.gz). Upload "
                      "it to the project Files tab."),
        ParamDef(name="gene_symbol", type="string", label="Gene symbol to extract",
                 default="",
                 help="Exact gene symbol as in the reference GFF (e.g. SF3B1, TP53). "
                      "Case-sensitive; matched against Name= and gene= GFF attributes."),
    ],
    run=run,
))
