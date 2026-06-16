"""Structure-first alignment via US-align (or TM-align).

This is the inverse of the sequence-guided path in struct_align: instead of
aligning sequences and pairing Cα atoms by sequence position, US-align superposes
on 3D GEOMETRY ALONE, then the residue↔residue correspondence (and therefore the
sequence alignment) is READ OFF from the structural superposition. That's the
right approach when sequences are too diverged to align reliably but the folds are
clearly homologous — e.g. MAYV vs CHIKV envelope proteins, where blastn finds
nothing yet the structures overlay well.

Engine: US-align (https://zhanggroup.org/US-align/), which supersedes TM-align and
also handles multi-chain / multimer alignment. We fall back to the `TMalign`
binary if `USalign` isn't on PATH.

Install (bioconda):  pixi add -c bioconda usalign   (or tmalign)

US-align outputs:
  - TM-score (normalized by each structure's length) and RMSD
  - The rotation matrix + a superposed structure (-o)
  - A structure-based sequence alignment (the aligned residues), which we parse
    into aligned sequence strings.

We run pairwise: structure[0] is the reference; every other structure is aligned
onto it. For >2 structures this yields N-1 pairwise superpositions onto a common
reference frame, so they can all be shown together.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any


def _which_usalign() -> tuple[str, str] | None:
    """Return (binary, flavor) where flavor is 'usalign' or 'tmalign'."""
    for name in ("USalign", "us-align", "USAlign"):
        p = shutil.which(name)
        if p:
            return p, "usalign"
    for name in ("TMalign", "tmalign"):
        p = shutil.which(name)
        if p:
            return p, "tmalign"
    return None


def usalign_available() -> bool:
    return _which_usalign() is not None


# US-align / TM-align text output blocks we parse:
#   "Aligned length= 123, RMSD=   2.34, Seq_ID=n_identical/n_aligned= 0.187"
#   "TM-score= 0.78564 (normalized by length of Chain_1: L=200)"
#   "TM-score= 0.71203 (normalized by length of Chain_2: L=220)"
# Followed by a 3-line alignment block:
#   <seq1 with gaps>
#   <match line of ":" "." " ">
#   <seq2 with gaps>

_RE_ALIGNED = re.compile(r"Aligned length\s*=\s*(\d+),\s*RMSD\s*=\s*([\d.]+),\s*"
                         r"Seq_ID\s*=.*?=\s*([\d.]+)")
_RE_TM1 = re.compile(r"TM-score\s*=\s*([\d.]+).*Chain_1", re.IGNORECASE)
_RE_TM2 = re.compile(r"TM-score\s*=\s*([\d.]+).*Chain_2", re.IGNORECASE)


def _parse_alignment_block(stdout: str) -> tuple[str, str, str]:
    """Extract (aligned_seq1, match_line, aligned_seq2) from US-align stdout.

    The alignment is the 3 consecutive lines made only of amino-acid letters,
    gaps '-', and the match symbols (':', '.', ' '). We find the match line
    (only :., and spaces) and take the line above and below it.
    """
    lines = stdout.splitlines()
    for i in range(1, len(lines) - 1):
        mid = lines[i]
        if mid and set(mid) <= {":", ".", " "} and (":" in mid or "." in mid):
            top = lines[i - 1]
            bot = lines[i + 1]
            # sanity: top/bot should be sequence-like (letters or gaps)
            if re.fullmatch(r"[A-Za-z\-\*]+", top.strip() or "") and \
               re.fullmatch(r"[A-Za-z\-\*]+", bot.strip() or ""):
                return top.strip(), mid, bot.strip()
    return "", "", ""


def run_usalign_pair(
    ref_path: str,
    mob_path: str,
    out_superposed_prefix: str,
    log=None,
) -> dict[str, Any]:
    """Align mob onto ref. Returns metrics + structure-based aligned sequences +
    the superposed structure path. Raises on binary failure."""
    found = _which_usalign()
    if not found:
        raise RuntimeError(
            "no structural aligner found on PATH. Install US-align or TM-align: "
            "`pixi add -c bioconda usalign` (or tmalign)."
        )
    binary, flavor = found

    def _emit(m: str) -> None:
        if log:
            log(m)

    # -o writes the superposed mobile structure; -outfmt 0 keeps the full text
    # block with the alignment. (TMalign uses the same -o convention.)
    cmd = [binary, mob_path, ref_path, "-o", out_superposed_prefix]
    _emit(f"{flavor}: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"{flavor} failed (exit {proc.returncode}): "
                           f"{(proc.stderr or proc.stdout)[-1500:]}")
    out = proc.stdout
    # Stream the useful lines into the log.
    for ln in out.splitlines():
        if any(k in ln for k in ("TM-score", "Aligned length", "RMSD")):
            _emit(ln.strip())

    aligned_len, rmsd, seq_id = 0, 0.0, 0.0
    m = _RE_ALIGNED.search(out)
    if m:
        aligned_len = int(m.group(1))
        rmsd = float(m.group(2))
        seq_id = float(m.group(3))
    tm1 = _RE_TM1.search(out)
    tm2 = _RE_TM2.search(out)
    tm_ref = float(tm1.group(1)) if tm1 else None
    tm_mob = float(tm2.group(1)) if tm2 else None

    aln_mob, match_line, aln_ref = _parse_alignment_block(out)
    # cmd order was (mob, ref): US-align prints Chain_1=first arg (mob),
    # Chain_2=second (ref). The alignment block top line is Chain_1 (mob).

    # US-align writes "<prefix>" and "<prefix>_atm" / "<prefix>.pdb" depending on
    # version; collect whatever superposed file appeared.
    superposed_path = None
    for cand in (out_superposed_prefix, out_superposed_prefix + ".pdb",
                 out_superposed_prefix + "_atm.pdb", out_superposed_prefix + "_all_atm"):
        if os.path.exists(cand):
            superposed_path = cand
            break

    return {
        "flavor": flavor,
        "tm_score_ref": tm_ref,
        "tm_score_mob": tm_mob,
        "rmsd": rmsd,
        "aligned_length": aligned_len,
        "seq_identity": seq_id,
        "aligned_ref": aln_ref,   # reference (Chain_2) aligned sequence
        "aligned_mob": aln_mob,   # mobile (Chain_1) aligned sequence
        "match_line": match_line,
        "superposed_path": superposed_path,
        "raw_tail": out[-2000:],
    }
