"""Translate DNA / RNA → protein.

Three modes:

1. **auto** (default): Try to find a CDS associated with the input.
   - If input is GenBank (.gb) → use the /CDS feature and its /translation.
   - If input is a CDS FASTA (from our `fetch-ncbi-gene extract_cds=true`,
     or from `annotate_gene`) → translate frame +1 from position 1.
   - If input is mRNA/raw FASTA → translate longest ORF in 6 frames.
     Reports clearly which strategy was used.

2. **longest_orf**: Force longest-ORF search across all 6 frames.

3. **fixed_frame**: User specifies frame (+1, +2, +3, -1, -2, -3).
   Translates straight through, stopping at first in-frame stop codon
   (or running to the end if `stop_at_first` is false).

Output: one .protein.fasta per input record. If input has N FASTA records,
output has N translated records (each with the chosen mode noted in the header).
"""
from __future__ import annotations

import asyncio
import re
from io import StringIO
from typing import Any

from app.files import store as filestore
from app.supabase_client import service_client
from app.tools.registry import ToolDef, ParamDef, register


# Standard genetic code (table 1). For mitochondrial / alternative codes,
# Biopython has CodonTable; we stick to standard for nuclear DNA which is
# what Shiba's analysis needs.
_CODONS = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}

_COMPLEMENT = str.maketrans("ACGTNacgtn", "TGCANtgcan")


def _revcomp(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def _translate_frame(seq: str, stop_at_first: bool = True) -> tuple[str, bool]:
    """Translate frame +1 from position 0. Returns (protein, hit_stop)."""
    seq = re.sub(r"[^ACGTNacgtn]", "", seq).upper()
    out: list[str] = []
    hit_stop = False
    for i in range(0, len(seq) - 2, 3):
        codon = seq[i:i + 3]
        if "N" in codon:
            out.append("X")
            continue
        aa = _CODONS.get(codon, "X")
        if aa == "*":
            hit_stop = True
            if stop_at_first:
                break
            out.append("*")
            continue
        out.append(aa)
    return "".join(out), hit_stop


def _find_longest_orf(seq: str) -> dict[str, Any]:
    """Across all 6 frames, find the longest ORF (ATG..stop). Returns
    {frame, start, end, length_aa, protein}."""
    seq = re.sub(r"[^ACGTNacgtn]", "", seq).upper()
    rc = _revcomp(seq)

    best = {"frame": 0, "start": 0, "end": 0, "length_aa": 0, "protein": ""}

    for strand_idx, s in enumerate((seq, rc)):
        for offset in range(3):
            i = offset
            while i < len(s) - 2:
                codon = s[i:i + 3]
                if codon == "ATG":
                    # Found start. Walk forward to next stop.
                    protein_chars: list[str] = []
                    j = i
                    while j < len(s) - 2:
                        c = s[j:j + 3]
                        if "N" in c:
                            protein_chars.append("X")
                        else:
                            aa = _CODONS.get(c, "X")
                            if aa == "*":
                                break
                            protein_chars.append(aa)
                        j += 3
                    if len(protein_chars) > best["length_aa"]:
                        frame = offset + 1
                        if strand_idx == 1:
                            frame = -frame
                        best = {
                            "frame": frame,
                            "start": i + 1,  # 1-based
                            "end": j + 3,
                            "length_aa": len(protein_chars),
                            "protein": "".join(protein_chars),
                        }
                    i = j + 3
                else:
                    i += 3
    return best


def _translate_fixed_frame(seq: str, frame: int, stop_at_first: bool) -> str:
    """frame in {+1, +2, +3, -1, -2, -3}."""
    if frame not in (1, 2, 3, -1, -2, -3):
        raise ValueError(f"invalid frame: {frame}")
    seq = re.sub(r"[^ACGTNacgtn]", "", seq).upper()
    if frame < 0:
        seq = _revcomp(seq)
        offset = -frame - 1
    else:
        offset = frame - 1
    protein, _ = _translate_frame(seq[offset:], stop_at_first=stop_at_first)
    return protein


def _parse_fasta(text: str) -> list[dict[str, str]]:
    """Returns [{header, seq}]. Header is the part after '>' (without newline)."""
    records: list[dict[str, str]] = []
    cur_h, cur_s = None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if cur_h is not None:
                records.append({"header": cur_h, "seq": "".join(cur_s)})
            cur_h = line[1:].strip()
            cur_s = []
        elif line.strip():
            cur_s.append(line.strip())
    if cur_h is not None:
        records.append({"header": cur_h, "seq": "".join(cur_s)})
    return records


def _load_input_file(file_id: str, project_id: str) -> dict[str, Any]:
    """Returns {name, mime_hint, source_metadata, text}."""
    client = service_client()
    fres = (
        client.table("project_files")
        .select("id, storage_path, name, mime_hint, source_metadata")
        .eq("id", file_id)
        .maybe_single()
        .execute()
    )
    if not fres or not fres.data:
        raise ValueError(f"file {file_id} not found")
    f = fres.data
    text = filestore.read_text(project_id, f["storage_path"])
    return {
        "name": f["name"],
        "mime_hint": f["mime_hint"],
        "source_metadata": f.get("source_metadata") or {},
        "text": text,
    }


def _extract_cds_from_genbank_text(text: str) -> list[dict[str, str]]:
    """Reuse files.py logic for parsing CDS from .gb. Returns list of
    {header, protein, cds_seq, accession}."""
    from io import StringIO
    from Bio import SeqIO

    out = []
    for record in SeqIO.parse(StringIO(text), "genbank"):
        for f in record.features:
            if f.type != "CDS":
                continue
            cds_obj = f.location.extract(record.seq)
            translation = f.qualifiers.get("translation", [None])[0]
            if not translation:
                translation = str(cds_obj.translate(to_stop=True))
            gene = f.qualifiers.get("gene", [None])[0] or "gene"
            product = f.qualifiers.get("product", [""])[0]
            out.append({
                "header": f"{record.id}_{gene} accession={record.id} product=\"{product}\"",
                "protein": translation,
                "cds_seq": str(cds_obj),
                "accession": record.id,
            })
            break  # one CDS per record; mRNAs have one
    return out


def _format_protein_fasta(records: list[dict[str, str]]) -> str:
    """Build a multi-FASTA from translated records."""
    lines = []
    for r in records:
        lines.append(f">{r['header']}")
        seq = r["protein"]
        for i in range(0, len(seq), 60):
            lines.append(seq[i:i + 60])
    return "\n".join(lines) + "\n"


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    file_id = (params.get("input_file_id") or "").strip()
    mode = (params.get("mode") or "auto").strip()
    frame_param = int(params.get("frame", 1))
    stop_at_first = bool(params.get("stop_at_first_stop", True))

    if not file_id:
        raise ValueError("input_file_id required")
    if mode not in ("auto", "longest_orf", "fixed_frame"):
        raise ValueError(f"invalid mode: {mode}")

    input_file = await asyncio.to_thread(_load_input_file, file_id, project_id)
    mime = input_file["mime_hint"]
    meta = input_file["source_metadata"] or {}
    text = input_file["text"]

    out_records: list[dict[str, str]] = []
    strategy_used: str
    warnings: list[str] = []

    # ─── Auto mode: route by input type ────────────────────────────

    if mode == "auto":
        if mime == "genbank":
            translated = _extract_cds_from_genbank_text(text)
            if not translated:
                raise ValueError("GenBank record has no CDS feature")
            out_records = translated
            strategy_used = "genbank_cds_translation"
        elif mime == "fasta":
            kind = meta.get("kind")
            if kind == "cds" or meta.get("category") == "cds_fasta":
                # Already a CDS — translate frame +1
                for rec in _parse_fasta(text):
                    p, _ = _translate_frame(rec["seq"], stop_at_first=stop_at_first)
                    out_records.append({
                        "header": f"{rec['header'].split()[0]}_protein translated_from=cds",
                        "protein": p,
                    })
                strategy_used = "cds_frame1"
            else:
                # mRNA or unknown FASTA — find ORF
                fasta_records = _parse_fasta(text)
                if not fasta_records:
                    raise ValueError("input FASTA has no records")

                # If first record starts with ATG and is divisible by 3,
                # try CDS-from-start; otherwise ORF search.
                for rec in fasta_records:
                    s = re.sub(r"[^ACGTN]", "", rec["seq"].upper())
                    starts_atg = s.startswith("ATG")
                    is_codon_aligned = len(s) % 3 == 0
                    if starts_atg and is_codon_aligned and len(s) >= 90:
                        # Strong signal this is already a CDS
                        p, hit_stop = _translate_frame(s, stop_at_first=stop_at_first)
                        if len(p) >= 30 and (hit_stop or len(p) >= 100):
                            out_records.append({
                                "header": f"{rec['header'].split()[0]}_protein "
                                          f"translated_from=auto_cds_inferred length={len(p)}aa",
                                "protein": p,
                            })
                            continue

                    # Fall back to ORF search
                    orf = _find_longest_orf(rec["seq"])
                    if orf["length_aa"] < 30:
                        warnings.append(
                            f"{rec['header'].split()[0]}: longest ORF only "
                            f"{orf['length_aa']} aa (frame {orf['frame']}). "
                            f"Likely not protein-coding."
                        )
                    out_records.append({
                        "header": (
                            f"{rec['header'].split()[0]}_protein "
                            f"translated_from=longest_orf "
                            f"frame={orf['frame']} length={orf['length_aa']}aa "
                            f"orf_coords={orf['start']}-{orf['end']}"
                        ),
                        "protein": orf["protein"],
                    })
                strategy_used = "auto_mixed"
        else:
            raise ValueError(
                f"can't auto-translate file with mime_hint='{mime}'. "
                "Supported: fasta, genbank."
            )

    # ─── longest_orf: force ORF search ─────────────────────────────

    elif mode == "longest_orf":
        for rec in _parse_fasta(text):
            orf = _find_longest_orf(rec["seq"])
            out_records.append({
                "header": (
                    f"{rec['header'].split()[0]}_protein "
                    f"translated_from=longest_orf "
                    f"frame={orf['frame']} length={orf['length_aa']}aa "
                    f"orf_coords={orf['start']}-{orf['end']}"
                ),
                "protein": orf["protein"],
            })
        strategy_used = "longest_orf"

    # ─── fixed_frame: user-specified frame ─────────────────────────

    else:  # mode == "fixed_frame"
        for rec in _parse_fasta(text):
            p = _translate_fixed_frame(rec["seq"], frame_param, stop_at_first)
            out_records.append({
                "header": (
                    f"{rec['header'].split()[0]}_protein "
                    f"translated_from=fixed_frame "
                    f"frame={frame_param:+d} length={len(p)}aa"
                ),
                "protein": p,
            })
        strategy_used = f"fixed_frame_{frame_param:+d}"

    # Build output FASTA
    out_fasta = _format_protein_fasta(out_records)
    stem = re.sub(r"\.(fasta|fa|gb|genbank)$", "", input_file["name"], flags=re.IGNORECASE)
    out_name = f"{stem}.protein.fasta"

    return {
        "input": input_file["name"],
        "input_mime": mime,
        "mode_requested": mode,
        "strategy_used": strategy_used,
        "n_records": len(out_records),
        "total_aa": sum(len(r["protein"]) for r in out_records),
        "per_record": [
            {"header": r["header"], "length_aa": len(r["protein"])} for r in out_records
        ],
        "warnings": warnings,
        "output_files": [
            {"name": out_name, "content": out_fasta, "kind": "fasta", "size": len(out_fasta)},
        ],
    }


register(ToolDef(
    id="translate_dna",
    label="Translate DNA → protein",
    description=(
        "Translate DNA/RNA to protein. 'auto' mode is smart: uses CDS feature "
        "if input is GenBank, translates directly if input is a CDS FASTA, "
        "or finds the longest ORF for mRNA/unknown sequence. "
        "Use 'fixed_frame' when you know the frame, 'longest_orf' to force ORF search."
    ),
    input_kind="none",
    params=[
        ParamDef(name="input_file_id", type="project_file",
                 label="Input file (FASTA or GenBank)",
                 default="",
                 help="The DNA file to translate. CDS / mRNA / GenBank — auto mode picks the right strategy."),
        ParamDef(name="mode", type="enum", label="Mode",
                 default="auto",
                 options=["auto", "longest_orf", "fixed_frame"],
                 help="auto: smart per-input. "
                      "longest_orf: search all 6 frames. "
                      "fixed_frame: translate the specified frame."),
        ParamDef(name="frame", type="int", label="Frame (fixed_frame only)",
                 default=1, min=-3, max=3,
                 help="+1/+2/+3 forward, -1/-2/-3 reverse-complement."),
        ParamDef(name="stop_at_first_stop", type="bool", label="Stop at first stop codon",
                 default=True,
                 help="If false, continues through stops (with '*' in output). "
                      "Usually you want True for clean protein."),
    ],
    run=run,
))
