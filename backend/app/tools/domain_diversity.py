"""Per-region nucleotide / amino-acid diversity within an alignment.

Given an alignment and a list of region coordinates (e.g. domain boundaries
in the reference sequence), this tool reports diversity statistics for each
region: pairwise identity, number of polymorphic columns, number of singleton
variants, fraction of fully-conserved columns, and Shannon entropy.

Useful for the standard comparative-oncology claim that a domain (e.g. TP53
DBD, SF3B1 HEAT repeats) is more conserved than its flanking regions —
quantified with actual numbers instead of hand-waving.

The tool also reports statistics for the OUTSIDE_DOMAINS complement (every
reference position not covered by any named region), giving an immediate
inside-vs-outside comparison.

REGION FORMAT
-------------
Comma-separated entries, each as "name:start-end" using 1-indexed positions
on the *ungapped* reference sequence. Example:
  TAD:1-42, DBD:94-312, OD:323-356, CTD:363-393

If no regions are provided, the tool reports whole-alignment statistics only,
which is still useful as a global baseline.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register


_REGION_RE = re.compile(r"^([^:]+):(\d+)-(\d+)$")


def _parse_fasta(text: str) -> list[tuple[str, str]]:
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


def _parse_regions(spec: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    if not spec.strip():
        return out
    for tok in [t.strip() for t in spec.split(",") if t.strip()]:
        m = _REGION_RE.match(tok)
        if not m:
            raise ValueError(f"could not parse region {tok!r}; expected name:start-end")
        name, start, end = m.group(1).strip(), int(m.group(2)), int(m.group(3))
        if start < 1 or end < start:
            raise ValueError(f"invalid coordinates in region {tok!r}")
        out.append((name, start, end))
    return out


def _ungapped_to_aligned(seq: str) -> list[int]:
    idx: list[int] = []
    for col, ch in enumerate(seq):
        if ch not in "-.":
            idx.append(col)
    return idx


def _column_stats(records: list[tuple[str, str]], cols: list[int]) -> dict[str, Any]:
    """Stats over an explicit list of alignment column indices."""
    n_cols = len(cols)
    if n_cols == 0:
        return {
            "n_columns": 0,
            "n_polymorphic": 0,
            "n_singleton": 0,
            "fraction_conserved": None,
            "mean_entropy": None,
            "pairwise_identity": None,
        }

    polymorphic = 0
    singleton = 0
    fully_conserved = 0
    entropies: list[float] = []

    for col in cols:
        residues = [seq[col] for _, seq in records if seq[col] not in "-."]
        if not residues:
            continue
        counts: dict[str, int] = {}
        for r in residues:
            counts[r] = counts.get(r, 0) + 1
        if len(counts) == 1:
            fully_conserved += 1
        else:
            polymorphic += 1
            if min(counts.values()) == 1 and len(residues) > 2:
                singleton += 1
        total = sum(counts.values())
        h = -sum((c / total) * math.log(c / total) for c in counts.values() if c > 0)
        entropies.append(h)

    # Pairwise identity across the column subset.
    pairwise_ids: list[float] = []
    seqs = [[s[c] for c in cols] for _, s in records]
    for i in range(len(seqs)):
        for j in range(i + 1, len(seqs)):
            same = 0
            comparable = 0
            for a, b in zip(seqs[i], seqs[j]):
                if a in "-." or b in "-.":
                    continue
                comparable += 1
                if a == b:
                    same += 1
            if comparable > 0:
                pairwise_ids.append(same / comparable)

    return {
        "n_columns": n_cols,
        "n_polymorphic": polymorphic,
        "n_singleton": singleton,
        "fraction_conserved": fully_conserved / n_cols if n_cols else None,
        "mean_entropy": sum(entropies) / len(entropies) if entropies else None,
        "pairwise_identity": (
            sum(pairwise_ids) / len(pairwise_ids) if pairwise_ids else None
        ),
    }


def _run_domain_diversity(fasta_text: str, params: dict[str, Any], label: str) -> dict[str, Any]:
    if not fasta_text.strip():
        raise ValueError("empty alignment")
    records = _parse_fasta(fasta_text)
    if len(records) < 2:
        raise ValueError("need at least two sequences")
    lengths = {len(s) for _, s in records}
    if len(lengths) > 1:
        raise ValueError(
            f"sequences have different lengths {sorted(lengths)} — must be aligned"
        )

    ref_name = (params.get("reference") or "").strip()
    regions = _parse_regions(params.get("regions", ""))

    ref_idx = None
    if ref_name:
        for i, (n, _) in enumerate(records):
            if n == ref_name or n.startswith(ref_name) or ref_name in n:
                ref_idx = i
                break
        if ref_idx is None:
            available = ", ".join(n for n, _ in records[:10])
            raise ValueError(f"reference {ref_name!r} not found; available: {available}")
    elif regions:
        raise ValueError("regions specified but no reference sequence given")

    aln_len = len(records[0][1])

    region_results: list[dict[str, Any]] = []

    # Whole alignment baseline.
    region_results.append({
        "region": "WHOLE",
        "start": 1,
        "end": aln_len,
        "reference_based": False,
        **_column_stats(records, list(range(aln_len))),
    })

    if ref_idx is not None:
        ref_seq = records[ref_idx][1]
        ref_map = _ungapped_to_aligned(ref_seq)
        ref_ungapped_len = len(ref_map)

        for name, start, end in regions:
            if start > ref_ungapped_len:
                region_results.append({
                    "region": name,
                    "start": start, "end": end,
                    "error": f"start {start} exceeds reference length {ref_ungapped_len}",
                })
                continue
            end_clamped = min(end, ref_ungapped_len)
            cols = [ref_map[p - 1] for p in range(start, end_clamped + 1)]
            region_results.append({
                "region": name,
                "start": start,
                "end": end_clamped,
                "reference_based": True,
                "reference": records[ref_idx][0],
                "aln_col_start": cols[0] + 1 if cols else None,
                "aln_col_end": cols[-1] + 1 if cols else None,
                **_column_stats(records, cols),
            })

        # OUTSIDE_DOMAINS: the complement.
        if regions:
            covered: set[int] = set()
            for _, s, e in regions:
                for p in range(s, min(e, ref_ungapped_len) + 1):
                    covered.add(p)
            outside_positions = sorted(set(range(1, ref_ungapped_len + 1)) - covered)
            if outside_positions:
                outside_cols = [ref_map[p - 1] for p in outside_positions]
                region_results.append({
                    "region": "OUTSIDE_DOMAINS",
                    "start": None, "end": None,
                    "reference_based": True,
                    "reference": records[ref_idx][0],
                    **_column_stats(records, outside_cols),
                })

    # TSV table.
    tsv_lines = [
        "region\tstart\tend\tn_columns\tn_polymorphic\tn_singleton\t"
        "fraction_conserved\tmean_entropy\tpairwise_identity"
    ]
    for r in region_results:
        if "error" in r:
            continue
        tsv_lines.append("\t".join([
            r["region"],
            "" if r.get("start") is None else str(r["start"]),
            "" if r.get("end") is None else str(r["end"]),
            str(r.get("n_columns", "")),
            str(r.get("n_polymorphic", "")),
            str(r.get("n_singleton", "")),
            ("" if r.get("fraction_conserved") is None
             else f"{r['fraction_conserved']:.4f}"),
            ("" if r.get("mean_entropy") is None
             else f"{r['mean_entropy']:.4f}"),
            ("" if r.get("pairwise_identity") is None
             else f"{r['pairwise_identity']:.4f}"),
        ]))
    tsv_text = "\n".join(tsv_lines) + "\n"

    return {
        "summary": {
            "n_sequences": len(records),
            "alignment_length": aln_len,
            "reference": records[ref_idx][0] if ref_idx is not None else None,
            "n_regions": len(regions),
        },
        "regions": region_results,
        "output_files": [
            {"name": f"{label}.regions.tsv", "content": tsv_text,
             "kind": "text", "size": len(tsv_text)},
            {"name": f"{label}.regions.json",
             "content": json.dumps(region_results, indent=2),
             "kind": "text", "size": 0},
        ],
    }


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    from app.tools._inputs import get_input_text, get_input_metadata, build_output_label
    fasta_text = get_input_text(inputs, project_id)
    metadata = get_input_metadata(inputs, project_id)
    label = (
        build_output_label(metadata, "domain_diversity", "").rstrip(".")
        if metadata else "domain_diversity"
    )
    return await asyncio.to_thread(_run_domain_diversity, fasta_text, params, label)


register(ToolDef(
    id="domain_diversity",
    label="Region-wise diversity",
    description=(
        "Per-region diversity statistics (pairwise identity, polymorphic "
        "columns, Shannon entropy) within an alignment. Useful for quantifying "
        "domain-vs-linker conservation (e.g. TP53 DBD vs the rest of the "
        "protein, or SF3B1 HEAT repeats vs flanking regions). Pair with the "
        "cancer_hotspots tool which emits ready-to-paste domain coordinate "
        "strings."
    ),
    input_kind="aligned_fasta",
    params=[
        ParamDef(
            name="reference",
            type="string",
            label="Reference sequence name",
            default="",
            help=(
                "Sequence whose 1-indexed coordinates are used by `regions`. "
                "Substring match against the FASTA header is allowed. "
                "Leave blank to only report whole-alignment statistics."
            ),
        ),
        ParamDef(
            name="regions",
            type="text_long",
            label="Regions",
            default="",
            help=(
                "Comma-separated regions in `name:start-end` format, "
                "1-indexed against the reference's ungapped sequence. Example: "
                "TAD:1-42, DBD:94-312, OD:323-356, CTD:363-393"
            ),
        ),
    ],
    run=run,
))
