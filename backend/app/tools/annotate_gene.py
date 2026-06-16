"""Annotate a gene in an unannotated genome by projecting a protein from a
related species onto it.

Workflow:
  1. tblastn the query protein against the target genome → find candidate region
  2. Extract that region with samtools/python (with generous flanks for introns)
  3. Run `exonerate --model protein2genome` on (query protein, region) → gene model
  4. Parse Exonerate's GFF + sequence output → CDS + translated protein
  5. Save as project_files: a `.cds.fasta`, a `.protein.fasta`, and the
     `.gff` annotation, all linked to the target species_id.

This is the standard non-model-organism gene-extraction recipe. Works well
when query and target diverged less than ~80 Mya (Carnivora is ~50 Mya, so
Z. californianus → A. gazella → A. townsendi all fine).

Requires `exonerate` and `blast+` installed:
  sudo pacman -S exonerate blast+ samtools
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

from app.files import store as filestore
from app.supabase_client import service_client
from app.tools.registry import ToolDef, ParamDef, register


def _has(name: str) -> bool:
    return shutil.which(name) is not None


def _require(name: str, install_hint: str) -> None:
    if not _has(name):
        raise RuntimeError(f"{name} not installed. {install_hint}")


# ─── Helpers ────────────────────────────────────────────────────────


def _read_project_file(file_id: str, project_id: str) -> tuple[str, str, str]:
    """Returns (storage_path_abs, name, raw_text). For huge files, raw_text
    may be the absolute path returned by absolute_path — we don't slurp it."""
    client = service_client()
    fres = (
        client.table("project_files")
        .select("id, storage_path, name, mime_hint, size")
        .eq("id", file_id)
        .maybe_single().execute()
    )
    if not fres or not fres.data:
        raise ValueError(f"file {file_id} not found")
    f = fres.data
    abs_path = str(filestore.absolute_path(project_id, f["storage_path"]))
    return abs_path, f["name"], f["storage_path"]


def _build_blast_db(genome_path: str) -> str:
    """Build a BLAST DB next to the genome FASTA (idempotent)."""
    nhr = genome_path + ".nhr"
    if os.path.exists(nhr):
        return genome_path
    _require("makeblastdb", "Install: sudo pacman -S blast+")
    r = subprocess.run(
        ["makeblastdb", "-in", genome_path, "-dbtype", "nucl", "-out", genome_path],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        raise RuntimeError(f"makeblastdb failed: {r.stderr or r.stdout}")
    return genome_path


def _ensure_fai(genome_path: str) -> None:
    if os.path.exists(genome_path + ".fai"):
        return
    if not _has("samtools"):
        return  # we have a Python fallback in _extract_region
    r = subprocess.run(
        ["samtools", "faidx", genome_path],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"samtools faidx failed: {r.stderr}")


def _extract_region(genome_path: str, seq_id: str, start: int, end: int) -> str:
    """Pull a chunk of the genome. Falls back to Python if samtools missing."""
    if _has("samtools"):
        _ensure_fai(genome_path)
        r = subprocess.run(
            ["samtools", "faidx", genome_path, f"{seq_id}:{start}-{end}"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            raise RuntimeError(f"samtools faidx failed: {r.stderr}")
        return r.stdout
    # Python fallback — slow on huge files.
    buf: list[str] = []
    in_target = False
    consumed = 0
    with open(genome_path) as f:
        for line in f:
            if line.startswith(">"):
                if in_target:
                    break
                hid = line[1:].split(None, 1)[0]
                if hid == seq_id:
                    in_target = True
                continue
            if not in_target:
                continue
            chunk = line.strip()
            if not chunk:
                continue
            cs, ce = consumed + 1, consumed + len(chunk)
            if ce < start:
                consumed += len(chunk)
                continue
            if cs > end:
                break
            lo = max(0, start - cs)
            hi = len(chunk) if ce <= end else end - cs + 1
            buf.append(chunk[lo:hi])
            consumed += len(chunk)
    if not buf:
        raise RuntimeError(f"sequence '{seq_id}' not found")
    seq = "".join(buf)
    wrapped = "\n".join(seq[i:i + 60] for i in range(0, len(seq), 60))
    return f">{seq_id}:{start}-{end}\n{wrapped}\n"


# ─── BLAST: protein → genome to locate region ───────────────────────


def _tblastn_locate(query_protein_fasta: str, genome_path: str, evalue: float, flank_bp: int) -> dict[str, Any]:
    """Run tblastn and return the spanning region across all good HSPs on
    the best-scoring contig.

    Strategy: take the contig with the highest total bit-score across all
    HSPs (handles multi-exon genes where each HSP is one exon). Then return
    min(HSP starts) - flank_bp .. max(HSP ends) + flank_bp on that contig.
    """
    _require("tblastn", "Install: sudo pacman -S blast+")
    _build_blast_db(genome_path)

    qfd, qpath = tempfile.mkstemp(prefix="exon_q_", suffix=".fasta")
    try:
        with os.fdopen(qfd, "w") as f:
            f.write(query_protein_fasta)
        # Tabular output for fast parsing
        outfmt = "6 sseqid sstart send evalue bitscore pident length qstart qend sstrand"
        r = subprocess.run(
            [
                "tblastn",
                "-query", qpath,
                "-db", genome_path,
                "-evalue", str(evalue),
                "-outfmt", outfmt,
                "-max_target_seqs", "5",
                "-num_threads", "4",
            ],
            capture_output=True, text=True, timeout=1800,
        )
        if r.returncode != 0:
            raise RuntimeError(f"tblastn failed: {r.stderr or r.stdout}")
        if not r.stdout.strip():
            raise RuntimeError(
                "tblastn returned 0 hits — protein and genome may be too divergent, "
                "or the gene is absent. Try lowering E-value cutoff."
            )

        # Parse and pick the contig with the best summed bit-score.
        by_contig: dict[str, list[dict[str, Any]]] = {}
        for line in r.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) < 10:
                continue
            sseqid, sstart, send, evalue_v, bitscore, pident, length, qstart, qend, sstrand = parts[:10]
            hsp = {
                "sseqid": sseqid,
                "sstart": int(sstart),
                "send": int(send),
                "evalue": float(evalue_v),
                "bitscore": float(bitscore),
                "pident": float(pident),
                "length": int(length),
                "qstart": int(qstart),
                "qend": int(qend),
                "sstrand": sstrand,
            }
            by_contig.setdefault(sseqid, []).append(hsp)

        contig_scores = {c: sum(h["bitscore"] for h in hsps) for c, hsps in by_contig.items()}
        best_contig = max(contig_scores, key=contig_scores.get)
        hsps = by_contig[best_contig]

        # Determine strand: majority vote across HSPs (most exons share strand)
        plus_score = sum(h["bitscore"] for h in hsps if h["sstart"] <= h["send"])
        minus_score = sum(h["bitscore"] for h in hsps if h["sstart"] > h["send"])
        strand = "+" if plus_score >= minus_score else "-"

        positions = []
        for h in hsps:
            positions.extend([h["sstart"], h["send"]])
        region_start = max(1, min(positions) - flank_bp)
        region_end = max(positions) + flank_bp

        return {
            "contig": best_contig,
            "region_start": region_start,
            "region_end": region_end,
            "strand": strand,
            "n_hsps": len(hsps),
            "best_bitscore": max(h["bitscore"] for h in hsps),
            "summed_bitscore": contig_scores[best_contig],
            "mean_pident": sum(h["pident"] for h in hsps) / len(hsps),
            "all_hsps": hsps,
        }
    finally:
        try:
            os.unlink(qpath)
        except Exception:
            pass


# ─── Exonerate: protein → genomic region → gene model ───────────────


def _parse_fasta_to_seq(fasta_text: str) -> dict[str, str]:
    """Parse a FASTA string into {seq_id: sequence}. Lowercase preserved
    here (the input is typically extracted via samtools and may be mixed case)."""
    out: dict[str, str] = {}
    cur_id, cur_buf = None, []
    for line in fasta_text.splitlines():
        if line.startswith(">"):
            if cur_id:
                out[cur_id] = "".join(cur_buf)
            # Take first whitespace-separated token as ID
            cur_id = line[1:].strip().split()[0] if line[1:].strip() else None
            cur_buf = []
        elif cur_id is not None:
            cur_buf.append(line.strip())
    if cur_id:
        out[cur_id] = "".join(cur_buf)
    return out


def _revcomp_dna(seq: str) -> str:
    return seq.translate(str.maketrans("ACGTNacgtn", "TGCANtgcan"))[::-1]


def _extract_cds_from_exonerate_gff(
    gff_lines: list[str],
    target_seqs: dict[str, str],
) -> tuple[str, list[dict[str, Any]]]:
    """Given Exonerate's GFF output and the target FASTA dict, build the
    predicted CDS by slicing the target sequence at each 'cds' feature span.

    Returns (cds_sequence_full, list_of_cds_spans).

    Exonerate's GFF spec: each 'cds' line has columns:
        seqid \\t source \\t feature \\t start \\t end \\t score \\t strand \\t phase \\t attributes
    """
    cds_spans: list[dict[str, Any]] = []
    for line in gff_lines:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        if parts[2].lower() != "cds":
            continue
        seqid = parts[0]
        try:
            start = int(parts[3])
            end = int(parts[4])
        except ValueError:
            continue
        strand = parts[6] if len(parts) > 6 else "+"
        cds_spans.append({
            "seqid": seqid,
            "start": start,
            "end": end,
            "strand": strand,
        })

    if not cds_spans:
        return "", []

    # Sort by start coordinate (forward strand order). For minus strand,
    # we'll reverse-complement the final concatenated sequence.
    cds_spans.sort(key=lambda s: s["start"])
    strands = {s["strand"] for s in cds_spans}
    is_minus = ("-" in strands)

    chunks: list[str] = []
    for span in cds_spans:
        target_seq = target_seqs.get(span["seqid"])
        if target_seq is None:
            # GFF references a seqid we don't have — Exonerate sometimes uses
            # a modified target ID. Try the only available sequence as fallback.
            if len(target_seqs) == 1:
                target_seq = next(iter(target_seqs.values()))
            else:
                continue
        # GFF coords are 1-based, inclusive on both ends.
        slice_seq = target_seq[span["start"] - 1 : span["end"]]
        chunks.append(slice_seq)

    cds_full = "".join(chunks).upper()
    if is_minus:
        cds_full = _revcomp_dna(cds_full)
    return cds_full, cds_spans


def _run_exonerate(query_protein_fasta: str, region_fasta: str) -> dict[str, Any]:
    """Run exonerate --model protein2genome.

    The CDS is extracted from the TARGET FASTA using GFF coordinates produced
    by Exonerate — never via %tcs (which is unreliable in protein2genome mode).
    """
    _require("exonerate", "Install: sudo pacman -S exonerate")

    # Parse the target FASTA upfront so we can slice it later.
    target_seqs = _parse_fasta_to_seq(region_fasta)
    if not target_seqs:
        raise RuntimeError("region FASTA is empty or malformed")

    qfd, qpath = tempfile.mkstemp(prefix="exon_q_", suffix=".fasta")
    tfd, tpath = tempfile.mkstemp(prefix="exon_t_", suffix=".fasta")
    try:
        with os.fdopen(qfd, "w") as f:
            f.write(query_protein_fasta)
        with os.fdopen(tfd, "w") as f:
            f.write(region_fasta)

        cmd = [
            "exonerate",
            "--model", "protein2genome",
            "--query", qpath,
            "--target", tpath,
            "--showtargetgff", "yes",
            "--showalignment", "no",
            "--showvulgar", "no",
            "--showcigar", "no",
            "--bestn", "1",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"exonerate failed: {r.stderr or r.stdout}")
        out = r.stdout

        # Pull GFF lines (Exonerate marks them with START/END OF GFF DUMP).
        gff_lines: list[str] = []
        in_gff = False
        for line in out.split("\n"):
            if "START OF GFF DUMP" in line:
                in_gff = True
                continue
            if "END OF GFF DUMP" in line:
                in_gff = False
                continue
            if in_gff and line and not line.startswith("#"):
                gff_lines.append(line)

        if not gff_lines:
            raise RuntimeError(
                "exonerate produced no GFF output. Genome region may not contain "
                "the gene, or the protein-target divergence is too high."
            )

        # Build CDS by slicing the target FASTA at GFF CDS coordinates.
        cds_seq, cds_spans = _extract_cds_from_exonerate_gff(gff_lines, target_seqs)
        if not cds_seq:
            raise RuntimeError(
                "exonerate's GFF had no 'cds' features. Check exonerate output "
                "format or try a different gene region."
            )

        # Build a metadata dict capturing the overall span for backwards compat.
        meta = {
            "query_id": "query_protein",
            "target_id": cds_spans[0]["seqid"] if cds_spans else "",
            "cds_start": min(s["start"] for s in cds_spans),
            "cds_end": max(s["end"] for s in cds_spans),
            "strand": cds_spans[0]["strand"] if cds_spans else "+",
            "n_cds_features": len(cds_spans),
        }

        gff_text = "##gff-version 3\n" + "\n".join(gff_lines) + "\n"

        # Translate the genuine target-derived CDS.
        protein = _translate_dna(cds_seq)

        return {
            "cds_seq": cds_seq,
            "protein_seq": protein,
            "cds_length": len(cds_seq),
            "protein_length": len(protein),
            "gff": gff_text,
            "exonerate_meta": meta,
        }
    finally:
        for p in (qpath, tpath):
            try:
                os.unlink(p)
            except Exception:
                pass


_CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L", "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M", "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S", "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T", "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*", "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K", "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W", "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R", "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


def _translate_dna(dna: str) -> str:
    seq = dna.upper().replace("\n", "").replace(" ", "")
    out = []
    for i in range(0, len(seq) - 2, 3):
        codon = seq[i:i + 3]
        if "N" in codon:
            out.append("X")
            continue
        aa = _CODON_TABLE.get(codon, "X")
        if aa == "*":
            break
        out.append(aa)
    return "".join(out)


# ─── Tool entry ─────────────────────────────────────────────────────


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    from app.tools._naming import (
        resolve_file_with_species, tool_output_name, species_short_code,
    )

    # Accept either single id (back-compat) or list of ids.
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
            "query_protein required: paste a FASTA-format protein from a "
            "related annotated species (Z. californianus SF3B1, e.g.)"
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

            locate = await asyncio.to_thread(
                _tblastn_locate, query_protein_text, genome_path, evalue, flank_bp,
            )
            region_fasta = await asyncio.to_thread(
                _extract_region, genome_path, locate["contig"],
                locate["region_start"], locate["region_end"],
            )
            annot = await asyncio.to_thread(_run_exonerate, query_protein_text, region_fasta)

            prot_filename = tool_output_name("exonerate", species_code, gene_label, "protein", "fasta")
            cds_filename = tool_output_name("exonerate", species_code, gene_label, "cds", "fasta")
            region_filename = tool_output_name("exonerate", species_code, gene_label, "region", "fasta")
            gff_filename = tool_output_name("exonerate", species_code, gene_label, "", "gff3")

            cds_fasta = (
                f">{species_code}_{gene_label}_CDS "
                f"contig={locate['contig']} "
                f"region={locate['region_start']}-{locate['region_end']} "
                f"strand={locate['strand']} "
                f"length={annot['cds_length']}bp source=exonerate\n"
            )
            for i in range(0, len(annot["cds_seq"]), 60):
                cds_fasta += annot["cds_seq"][i:i + 60] + "\n"

            prot_fasta = (
                f">{species_code}_{gene_label}_protein "
                f"contig={locate['contig']} "
                f"length={annot['protein_length']}aa source=exonerate\n"
            )
            for i in range(0, len(annot["protein_seq"]), 60):
                prot_fasta += annot["protein_seq"][i:i + 60] + "\n"

            base_meta = {
                "tool": "exonerate",
                "gene": gene_label,
                "species_id": species_id,
                "species_code": species_code,
                "source_genome_id": genome_file_id,
                "source_genome_name": genome_name,
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
                "contig": locate["contig"],
                "region_start": locate["region_start"],
                "region_end": locate["region_end"],
                "strand": locate["strand"],
                "n_hsps": locate["n_hsps"],
                "mean_blast_pident": locate["mean_pident"],
                "cds_length": annot["cds_length"],
                "protein_length": annot["protein_length"],
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
        "tool_used": "exonerate",
        "gene": gene_label,
        "n_genomes_processed": len(per_genome_results),
        "n_successful": n_ok,
        "n_failed": len(per_genome_results) - n_ok,
        "per_genome_results": per_genome_results,
        "output_files": all_output_files,
    }


register(ToolDef(
    id="annotate_gene",
    label="Annotate gene (Exonerate)",
    description=(
        "Project a protein from an annotated species onto one or more "
        "unannotated genomes via tblastn + Exonerate. Each output is tagged "
        "with the source species and named "
        "`exonerate.<SpCode>_<gene>_<kind>.fasta`. Select multiple genomes to "
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
                 label="Query protein (from related annotated species)",
                 default="",
                 help="FASTA-format protein sequence. Get from NCBI for Z. californianus SF3B1 / TP53."),
        ParamDef(name="gene_label", type="string", label="Gene label",
                 default="gene",
                 help="Short tag for output filenames (e.g. SF3B1, TP53)."),
        ParamDef(name="flank_bp", type="int", label="Flanking bp around BLAST hits",
                 default=5000, min=0, max=100000,
                 help="Extra bases beyond the outermost HSP — covers introns + UTRs. "
                      "5 kb is generous for most genes; bump to 20 kb for SF3B1 which is huge."),
        ParamDef(name="evalue", type="float", label="tblastn E-value cutoff",
                 default=1e-20, min=0.0, max=1.0,
                 help="Smaller = stricter. 1e-20 is conservative for confident gene location."),
    ],
    run=run,
))
