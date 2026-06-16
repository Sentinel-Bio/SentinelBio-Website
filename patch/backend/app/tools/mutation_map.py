"""Map mutation hotspots from a reference sequence onto every other sequence
in a multiple alignment.

Use case: you have a list of cancer-relevant mutation positions in human
TP53 (e.g. R175, R248, R273, R282) and want to know which residue sits at
the corresponding position in Arctocephalus australis, Zalophus californianus,
A. gazella, A. townsendi, etc. This tool resolves that mapping through the
alignment columns, accounting for gaps.

Input:
  - An aligned FASTA (use the standard tool input — pick the MAFFT output)
  - A reference sequence name parameter (substring of a FASTA header)
  - A hotspot list (see HOTSPOT FORMATS below). Pair this with the
    `cancer_hotspots` tool which emits literature-curated lists ready to paste.

Output:
  - A TSV table: one row per hotspot × sequence with the mapped position
    and residue
  - JSON keyed by sequence name with the position list — ready for ingestion
    into ConservationPanel as a hotspot list

HOTSPOT FORMATS
---------------
Comma- or whitespace-separated list. Each entry can be:
  - A bare position: "175"  → residue at position 175 in the reference
  - WT + position: "R175"   → asserts the reference has R at position 175;
    if not, the row is still mapped but flagged as a WT mismatch
  - Full mutation: "R175H"  → wild-type R, mutant H, at position 175.
    Only the position and WT are used for mapping.

Positions are 1-indexed and refer to the *ungapped* reference sequence.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register


# Matches "R175H", "R175", or "175" — case-insensitive on residues.
_HOTSPOT_RE = re.compile(r"^([A-Za-z])?(\d+)([A-Za-z*]+)?$")


def _parse_fasta(text: str) -> list[tuple[str, str]]:
    """Parse FASTA; preserve gaps; first whitespace-delimited token is the name."""
    records: list[tuple[str, str]] = []
    name: str | None = None
    chunks: list[str] = []

    def flush():
        if name is not None:
            records.append((name, "".join(chunks).upper()))

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith(">"):
            flush()
            name = line[1:].strip().split()[0] if len(line) > 1 else ""
            chunks = []
        else:
            chunks.append(line.strip())
    flush()
    return records


def _parse_hotspots(spec: str) -> list[dict[str, Any]]:
    if not spec.strip():
        return []
    out: list[dict[str, Any]] = []
    tokens = re.split(r"[,\s]+", spec.strip())
    for tok in tokens:
        if not tok:
            continue
        m = _HOTSPOT_RE.match(tok)
        if not m:
            raise ValueError(f"could not parse hotspot token {tok!r}")
        wt, pos, mut = m.group(1), int(m.group(2)), m.group(3)
        out.append({
            "raw": tok,
            "position": pos,
            "wt": wt.upper() if wt else None,
            "mut": mut.upper() if mut else None,
        })
    return out


def _build_ungapped_to_aligned_index(seq: str) -> list[int]:
    """Position-i (1-indexed, ungapped) → column j (0-indexed, aligned)."""
    idx: list[int] = []
    for col, ch in enumerate(seq):
        if ch not in "-.":
            idx.append(col)
    return idx


def _build_aligned_to_ungapped_index(seq: str) -> list[int | None]:
    """Column j (0-indexed, aligned) → position (1-indexed, ungapped) or None for gap."""
    out: list[int | None] = []
    counter = 0
    for ch in seq:
        if ch not in "-.":
            counter += 1
            out.append(counter)
        else:
            out.append(None)
    return out


def _run_mutation_map(fasta_text: str, params: dict[str, Any], label: str) -> dict[str, Any]:
    if not fasta_text.strip():
        raise ValueError("empty alignment input")

    records = _parse_fasta(fasta_text)
    if len(records) < 2:
        raise ValueError("alignment must contain at least two sequences")

    lengths = {len(s) for _, s in records}
    if len(lengths) > 1:
        raise ValueError(
            f"sequences have different lengths {sorted(lengths)} — "
            "input must be aligned (run MAFFT first)"
        )

    ref_name = (params.get("reference") or "").strip()
    if not ref_name:
        raise ValueError("reference sequence name is required")

    # Exact match first, then substring fallback (since headers may be
    # rewritten with species prefixes by _inputs._rewrite_fasta_headers).
    ref_idx = None
    for i, (name, _) in enumerate(records):
        if name == ref_name:
            ref_idx = i
            break
    if ref_idx is None:
        for i, (name, _) in enumerate(records):
            if name.startswith(ref_name) or ref_name in name:
                ref_idx = i
                break
    if ref_idx is None:
        available = ", ".join(n for n, _ in records[:10])
        raise ValueError(
            f"reference {ref_name!r} not found in alignment. Available: {available}"
        )

    ref_name_resolved, ref_seq = records[ref_idx]
    hotspots = _parse_hotspots(params.get("hotspots", ""))
    if not hotspots:
        raise ValueError(
            "no hotspots provided. Use the cancer_hotspots tool to fetch a "
            "literature-curated list for TP53 / SF3B1 / NOTCH1."
        )

    ref_ungapped_to_aligned = _build_ungapped_to_aligned_index(ref_seq)
    ref_ungapped_length = len(ref_ungapped_to_aligned)

    seq_maps = {name: _build_aligned_to_ungapped_index(seq) for name, seq in records}

    rows: list[dict[str, Any]] = []
    by_sequence: dict[str, list[int]] = {name: [] for name, _ in records}
    by_sequence_detail: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in records}
    conservation_summary: list[dict[str, Any]] = []

    for h in hotspots:
        pos_ref = h["position"]
        wt_asserted = h["wt"]
        if pos_ref < 1 or pos_ref > ref_ungapped_length:
            rows.append({
                "hotspot": h["raw"],
                "reference_position": pos_ref,
                "error": (
                    f"position {pos_ref} out of range for {ref_name_resolved} "
                    f"(ungapped length {ref_ungapped_length})"
                ),
            })
            continue

        col = ref_ungapped_to_aligned[pos_ref - 1]
        ref_residue = ref_seq[col]

        residues_at_col: list[str] = []
        for name, seq in records:
            mapped_pos = seq_maps[name][col]
            mapped_residue = seq[col]
            is_gap = mapped_residue in "-."
            residues_at_col.append("" if is_gap else mapped_residue)
            entry = {
                "hotspot": h["raw"],
                "reference_position": pos_ref,
                "alignment_column": col + 1,
                "reference_residue": ref_residue,
                "sequence": name,
                "sequence_position": mapped_pos,
                "sequence_residue": mapped_residue if not is_gap else None,
                "is_gap": is_gap,
                "matches_reference": (not is_gap) and (mapped_residue == ref_residue),
            }
            if wt_asserted and ref_residue != wt_asserted:
                entry["wt_mismatch"] = (
                    f"asserted wild-type {wt_asserted} but reference has {ref_residue}"
                )
            rows.append(entry)
            if not is_gap and mapped_pos is not None:
                by_sequence[name].append(mapped_pos)
                by_sequence_detail[name].append({
                    "position": mapped_pos,
                    "residue": mapped_residue,
                    "hotspot": h["raw"],
                })

        non_gap = [r for r in residues_at_col if r]
        if non_gap:
            same_as_ref = sum(1 for r in non_gap if r == ref_residue)
            unique_residues = sorted(set(non_gap))
            conservation_summary.append({
                "hotspot": h["raw"],
                "reference_position": pos_ref,
                "alignment_column": col + 1,
                "reference_residue": ref_residue,
                "n_sequences_with_residue": len(non_gap),
                "n_matching_reference": same_as_ref,
                "fraction_conserved": same_as_ref / len(non_gap),
                "distinct_residues": unique_residues,
            })

    summary = {
        "reference": ref_name_resolved,
        "n_sequences": len(records),
        "alignment_length": len(ref_seq),
        "reference_ungapped_length": ref_ungapped_length,
        "n_hotspots": len(hotspots),
    }

    # TSV table for the supplementary.
    tsv_lines = [
        "hotspot\treference_position\talignment_column\treference_residue\t"
        "sequence\tsequence_position\tsequence_residue\tis_gap\tmatches_reference"
    ]
    for r in rows:
        if "error" in r:
            continue
        tsv_lines.append("\t".join([
            r["hotspot"],
            str(r["reference_position"]),
            str(r["alignment_column"]),
            r["reference_residue"],
            r["sequence"],
            "" if r["sequence_position"] is None else str(r["sequence_position"]),
            r["sequence_residue"] or "-",
            "1" if r["is_gap"] else "0",
            "1" if r["matches_reference"] else "0",
        ]))
    tsv_text = "\n".join(tsv_lines) + "\n"

    return {
        "summary": summary,
        "rows": rows,
        "conservation_summary": conservation_summary,
        "by_sequence": by_sequence,
        "output_files": [
            {"name": f"{label}.tsv", "content": tsv_text,
             "kind": "text", "size": len(tsv_text)},
            {"name": f"{label}.hotspots.json",
             "content": json.dumps(by_sequence, indent=2),
             "kind": "text", "size": 0},
            {"name": f"{label}.hotspots_detailed.json",
             "content": json.dumps(by_sequence_detail, indent=2),
             "kind": "text", "size": 0},
            {"name": f"{label}.conservation.json",
             "content": json.dumps(conservation_summary, indent=2),
             "kind": "text", "size": 0},
        ],
    }


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    from app.tools._inputs import get_input_text, get_input_metadata, build_output_label
    fasta_text = get_input_text(inputs, project_id)
    metadata = get_input_metadata(inputs, project_id)
    label = (
        build_output_label(metadata, "mutation_map", "").rstrip(".")
        if metadata else "mutation_map"
    )
    return await asyncio.to_thread(_run_mutation_map, fasta_text, params, label)


register(ToolDef(
    id="mutation_map",
    label="Mutation hotspot mapping",
    description=(
        "Project a list of mutation positions from a reference sequence onto "
        "every other sequence in an alignment. Useful for transferring known "
        "cancer hotspots (e.g. human TP53 R175, R248, R273) onto orthologues "
        "in non-model species. Pair with the cancer_hotspots tool for "
        "literature-curated hotspot lists."
    ),
    input_kind="aligned_fasta",
    params=[
        ParamDef(
            name="reference",
            type="string",
            label="Reference sequence name",
            default="",
            help=(
                "The sequence in the alignment that the hotspot positions are "
                "given in. Substring match against the FASTA header is allowed. "
                "Example: Homo_sapiens_TP53"
            ),
        ),
        ParamDef(
            name="hotspots",
            type="text_long",
            label="Hotspots",
            default="",
            help=(
                "Comma- or space-separated. Each entry: bare position (175), "
                "WT+position (R175), or full mutation (R175H). Run the "
                "cancer_hotspots tool first to get the curated list for TP53."
            ),
        ),
    ],
    run=run,
))
