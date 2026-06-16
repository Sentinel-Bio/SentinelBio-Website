"""Population genetics summary stats via DendroPy:
   - Nucleotide diversity (π)
   - Number of segregating sites (S)
   - Tajima's D
"""
from __future__ import annotations

import asyncio
import io
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register


def _compute_stats(fasta_text: str) -> dict[str, Any]:
    if not fasta_text.strip():
        raise ValueError("empty input alignment")

    import dendropy
    from dendropy.calculate import popgenstat

    # Load as DNA character matrix.
    char_mat = dendropy.DnaCharacterMatrix.get(
        data=fasta_text,
        schema="fasta",
    )

    n_seqs = len(char_mat)
    if n_seqs < 2:
        raise ValueError("need at least 2 sequences for stats")

    aln_length = char_mat.max_sequence_size if hasattr(char_mat, 'max_sequence_size') else len(next(iter(char_mat.values())))

    # Compute the basic stats. dendropy's popgenstat works on a list of sequences.
    seqs = [str(taxon_seq) for taxon_seq in char_mat.values()]

    # Number of segregating sites
    try:
        S = popgenstat.num_segregating_sites(char_mat)
    except Exception as e:
        S = None

    # Nucleotide diversity (π)
    try:
        pi = popgenstat.nucleotide_diversity(char_mat)
    except Exception:
        pi = None

    # Tajima's D
    try:
        tajimas_d = popgenstat.tajimas_d(char_mat)
    except Exception:
        tajimas_d = None

    # Pairwise distances (uncorrected p-distance)
    pairwise = []
    taxa = list(char_mat.taxon_namespace)
    for i, t1 in enumerate(taxa):
        for t2 in taxa[i + 1:]:
            s1 = str(char_mat[t1])
            s2 = str(char_mat[t2])
            diffs = 0
            comparable = 0
            for a, b in zip(s1, s2):
                if a in '-?N' or b in '-?N':
                    continue
                comparable += 1
                if a.upper() != b.upper():
                    diffs += 1
            if comparable > 0:
                pairwise.append({
                    "a": t1.label,
                    "b": t2.label,
                    "p_distance": diffs / comparable,
                    "differences": diffs,
                    "comparable_sites": comparable,
                })

    return {
        "num_sequences": n_seqs,
        "alignment_length": aln_length,
        "segregating_sites": S,
        "nucleotide_diversity_pi": pi,
        "tajimas_d": tajimas_d,
        "pairwise_distances": pairwise,
    }

async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    from app.tools._inputs import get_input_text
    fasta_text = get_input_text(inputs, project_id)
    return await asyncio.to_thread(_compute_stats, fasta_text)  # or _compute_stats for popstats

register(ToolDef(
    id="popstats",
    label="Population stats (DendroPy)",
    description="π (nucleotide diversity), S (segregating sites), Tajima's D, pairwise p-distances.",
    input_kind="aligned_fasta",
    params=[],
    run=run,
))
