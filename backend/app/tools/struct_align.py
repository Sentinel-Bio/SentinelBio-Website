"""Pairwise structural superposition with per-residue conservation scoring.

Takes 2+ protein structures (PDB or mmCIF) and:
  1. Extracts the protein sequence of each
  2. Globally aligns the sequences (Needleman–Wunsch via Bio.Align)
  3. Pairs CA atoms based on aligned positions
  4. Computes a rigid-body superposition (Kabsch / SVD via Bio.PDB.Superimposer)
  5. Emits per-residue distances after superposition

Output:
  - RMSD and sequence identity per pairwise comparison
  - Per-residue distance series for each structure relative to the first
    (the "reference"). Low distance → structurally conserved; high → divergent.
  - JSON of high-divergence residue positions, ready for ConservationPanel
  - The superposed structures as PDB text, so they can be loaded together
    in StructureViewer for a final figure

This is a sequence-guided superposition, which is appropriate for homologous
proteins where the sequence correspondence is reliable. For low-identity
divergent structures, a true structural aligner like TM-align would be
preferred — that's a future addition.

Dependencies: Bio.PDB and Bio.Align (BioPython).
Install: pip install biopython  (or `pixi add -c bioconda biopython`).
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
from typing import Any

from app.files import store as filestore
from app.supabase_client import service_client
from app.tools.registry import ToolDef, ParamDef, register
from app.tools import usalign as usalign_mod


# Three-letter to one-letter codes; unknown residues are skipped during
# sequence extraction so they don't pollute the alignment.
_THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M",  # selenomethionine (common in crystal structures)
    "SEC": "U", "PYL": "O",
}


# ── File resolution ─────────────────────────────────────────────────

def _resolve_structure_files(file_ids: list[str], project_id: str) -> list[dict[str, str]]:
    """Returns list of {id, name, text} for the requested project files,
    preserving input order. Validates that each file is PDB or CIF."""
    if not file_ids:
        raise ValueError("structure_file_ids required (at least 2 PDB/CIF files)")
    client = service_client()
    fres = (
        client.table("project_files")
        .select("id, storage_path, name, mime_hint")
        .in_("id", file_ids).execute()
    )
    rows = fres.data or []
    rows_by_id = {r["id"]: r for r in rows}
    out = []
    for fid in file_ids:
        r = rows_by_id.get(fid)
        if not r:
            raise ValueError(f"file {fid} not found")
        if r["mime_hint"] not in ("pdb", "cif"):
            raise ValueError(
                f"file {r['name']} has mime_hint='{r['mime_hint']}', "
                "struct_align needs PDB or CIF structures"
            )
        text = filestore.read_text(project_id, r["storage_path"])
        # Strip extension for a cleaner display name in output files.
        display_name = r["name"].rsplit(".", 1)[0]
        out.append({"id": r["id"], "name": display_name, "text": text})
    if len(out) < 2:
        raise ValueError("struct_align needs at least 2 structures to compare")
    return out


# ── Parsing & sequence extraction ───────────────────────────────────

def _structure_from_text(text: str, name: str) -> Any:
    """Parse a PDB or mmCIF text blob into a Bio.PDB Structure."""
    from Bio.PDB import PDBParser, MMCIFParser

    head = text.lstrip()[:4000]
    if head.startswith("data_") or "_atom_site." in head:
        parser = MMCIFParser(QUIET=True)
    else:
        parser = PDBParser(QUIET=True)
    return parser.get_structure(name, io.StringIO(text))


def _extract_chain(structure: Any, chain_id: str | None) -> tuple[str, list[Any], str]:
    """Return (chain_id_used, list_of_residues_with_CA, sequence_string)."""
    model = next(structure.get_models())
    chains = list(model.get_chains())
    if not chains:
        raise ValueError(f"structure {structure.id!r} has no chains")

    if chain_id:
        chain_id = chain_id.strip()
        chain = next((c for c in chains if c.id == chain_id), None)
        if chain is None:
            available = ",".join(c.id for c in chains)
            raise ValueError(
                f"chain {chain_id!r} not found in {structure.id!r}; available: {available}"
            )
    else:
        # Pick the chain with the most amino-acid residues having a CA atom.
        def _count_aa(c):
            return sum(
                1 for r in c
                if r.id[0] == " " and "CA" in r and r.get_resname() in _THREE_TO_ONE
            )
        chain = max(chains, key=_count_aa)

    residues: list[Any] = []
    seq_chars: list[str] = []
    for res in chain:
        # res.id is (hetflag, seqid, icode). Standard residues have hetflag = " ".
        if res.id[0] != " ":
            continue
        if "CA" not in res:
            continue
        resname = res.get_resname().strip().upper()
        if resname not in _THREE_TO_ONE:
            continue
        residues.append(res)
        seq_chars.append(_THREE_TO_ONE[resname])

    if not residues:
        raise ValueError(
            f"no standard amino-acid residues with CA atoms found in {structure.id!r}"
        )
    return chain.id, residues, "".join(seq_chars)


# ── Alignment & superposition ───────────────────────────────────────

def _pairwise_align(seq_a: str, seq_b: str) -> tuple[str, str]:
    """Global Needleman–Wunsch with BLOSUM62. Returns aligned strings."""
    from Bio.Align import PairwiseAligner, substitution_matrices

    aligner = PairwiseAligner()
    aligner.mode = "global"
    aligner.substitution_matrix = substitution_matrices.load("BLOSUM62")
    aligner.open_gap_score = -10
    aligner.extend_gap_score = -1

    alignments = aligner.align(seq_a, seq_b)
    best = alignments[0]

    # Build aligned strings from `best.aligned`, which gives matched index
    # ranges for each sequence.
    a_idx = 0
    b_idx = 0
    out_a: list[str] = []
    out_b: list[str] = []
    a_blocks, b_blocks = best.aligned
    for (a_start, a_end), (b_start, b_end) in zip(a_blocks, b_blocks):
        if b_start > b_idx:
            out_a.append("-" * (b_start - b_idx))
            out_b.append(seq_b[b_idx:b_start])
        if a_start > a_idx:
            out_a.append(seq_a[a_idx:a_start])
            out_b.append("-" * (a_start - a_idx))
        out_a.append(seq_a[a_start:a_end])
        out_b.append(seq_b[b_start:b_end])
        a_idx = a_end
        b_idx = b_end
    if a_idx < len(seq_a):
        out_a.append(seq_a[a_idx:])
        out_b.append("-" * (len(seq_a) - a_idx))
    if b_idx < len(seq_b):
        out_a.append("-" * (len(seq_b) - b_idx))
        out_b.append(seq_b[b_idx:])

    return "".join(out_a), "".join(out_b)


def _paired_ca_atoms(
    aligned_a: str, aligned_b: str,
    residues_a: list[Any], residues_b: list[Any],
) -> list[tuple[Any, Any, int, int]]:
    """Walk the alignment; yield (res_a, res_b, idx_a, idx_b) for each
    column where neither sequence has a gap."""
    paired = []
    ia, ib = 0, 0
    for ca, cb in zip(aligned_a, aligned_b):
        gap_a = ca == "-"
        gap_b = cb == "-"
        if not gap_a and not gap_b:
            paired.append((residues_a[ia], residues_b[ib], ia, ib))
        if not gap_a:
            ia += 1
        if not gap_b:
            ib += 1
    return paired


def _superimpose_and_score(
    residues_ref: list[Any], residues_mob: list[Any],
    aligned_ref: str, aligned_mob: str,
) -> dict[str, Any]:
    """Run Bio.PDB.Superimposer on paired CA atoms; return RMSD and per-residue
    distances after applying the transformation to the mobile structure."""
    from Bio.PDB import Superimposer

    pairs = _paired_ca_atoms(aligned_ref, aligned_mob, residues_ref, residues_mob)
    if len(pairs) < 4:
        raise ValueError(
            f"too few paired residues ({len(pairs)}) for a meaningful superposition"
        )

    ref_atoms = [p[0]["CA"] for p in pairs]
    mob_atoms = [p[1]["CA"] for p in pairs]

    sup = Superimposer()
    sup.set_atoms(ref_atoms, mob_atoms)
    rmsd = float(sup.rms)

    # Apply transformation to ALL atoms in the mobile structure so we can
    # emit a superposed PDB.
    mob_structure = residues_mob[0].get_parent().get_parent().get_parent()
    sup.apply(mob_structure.get_atoms())

    # Per-residue distance after superposition.
    per_residue: list[dict[str, Any]] = []
    for ref_res, mob_res, ia, ib in pairs:
        ref_ca = ref_res["CA"].get_coord()
        mob_ca = mob_res["CA"].get_coord()
        d = float(((ref_ca - mob_ca) ** 2).sum() ** 0.5)
        per_residue.append({
            "ref_index": ia + 1,
            "mob_index": ib + 1,
            "ref_residue_id": ref_res.id[1],
            "mob_residue_id": mob_res.id[1],
            "ref_resname": ref_res.get_resname(),
            "mob_resname": mob_res.get_resname(),
            "distance_after_sup": d,
        })

    return {
        "rmsd": rmsd,
        "n_paired": len(pairs),
        "per_residue": per_residue,
    }


def _structure_to_pdb_text(structure: Any) -> str:
    from Bio.PDB import PDBIO

    out = io.StringIO()
    io_writer = PDBIO()
    io_writer.set_structure(structure)
    io_writer.save(out)
    return out.getvalue()


def _seq_identity(a: str, b: str) -> float:
    """Fraction of identical aligned columns (excluding gap columns)."""
    if len(a) != len(b):
        return 0.0
    matches = 0
    aligned_cols = 0
    for ca, cb in zip(a, b):
        if ca == "-" or cb == "-":
            continue
        aligned_cols += 1
        if ca == cb:
            matches += 1
    return matches / aligned_cols if aligned_cols else 0.0


def _run_struct_align(
    structure_inputs: list[dict[str, str]],
    params: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    chain_id = (params.get("chain") or "").strip() or None
    high_distance_threshold = float(params.get("high_distance_threshold", 3.0))

    # Parse every structure and pull its sequence + residues.
    parsed: list[dict[str, Any]] = []
    for s in structure_inputs:
        if not s["text"].strip():
            raise ValueError(f"empty structure file: {s['name']}")
        structure = _structure_from_text(s["text"], s["name"])
        cid, residues, seq = _extract_chain(structure, chain_id)
        parsed.append({
            "name": s["name"],
            "structure": structure,
            "chain_used": cid,
            "residues": residues,
            "sequence": seq,
        })

    ref = parsed[0]
    pairwise: list[dict[str, Any]] = []
    per_residue_series: dict[str, list[dict[str, Any]]] = {}
    high_divergence: dict[str, list[int]] = {}

    output_files: list[dict[str, Any]] = []
    # The reference is written out as-is so users can load the whole set
    # together in the structure viewer.
    ref_pdb_text = _structure_to_pdb_text(ref["structure"])
    output_files.append({
        "name": f"{label}.{ref['name']}.ref.pdb",
        "content": ref_pdb_text,
        "kind": "pdb",
        "size": len(ref_pdb_text),
    })

    for mob in parsed[1:]:
        aligned_ref, aligned_mob = _pairwise_align(ref["sequence"], mob["sequence"])
        result = _superimpose_and_score(
            ref["residues"], mob["residues"], aligned_ref, aligned_mob
        )
        pairwise.append({
            "reference": ref["name"],
            "mobile": mob["name"],
            "ref_chain": ref["chain_used"],
            "mob_chain": mob["chain_used"],
            "rmsd": result["rmsd"],
            "n_paired_residues": result["n_paired"],
            "sequence_identity": _seq_identity(aligned_ref, aligned_mob),
        })
        per_residue_series[mob["name"]] = result["per_residue"]
        high_divergence[mob["name"]] = [
            r["ref_index"] for r in result["per_residue"]
            if r["distance_after_sup"] >= high_distance_threshold
        ]

        pdb_text = _structure_to_pdb_text(mob["structure"])
        output_files.append({
            "name": f"{label}.{mob['name']}.superposed.pdb",
            "content": pdb_text,
            "kind": "pdb",
            "size": len(pdb_text),
        })

    summary = {
        "reference": ref["name"],
        "n_structures": len(parsed),
        "chain_filter": chain_id,
        "high_distance_threshold_A": high_distance_threshold,
    }

    # TSV summary table.
    tsv_lines = [
        "reference\tmobile\tref_chain\tmob_chain\trmsd_A\tn_paired_residues\tsequence_identity"
    ]
    for p in pairwise:
        tsv_lines.append("\t".join([
            p["reference"], p["mobile"], p["ref_chain"], p["mob_chain"],
            f"{p['rmsd']:.4f}", str(p["n_paired_residues"]),
            f"{p['sequence_identity']:.4f}",
        ]))
    tsv_text = "\n".join(tsv_lines) + "\n"

    output_files.extend([
        {"name": f"{label}.pairwise.tsv", "content": tsv_text,
         "kind": "text", "size": len(tsv_text)},
        {"name": f"{label}.pairwise.json",
         "content": json.dumps(pairwise, indent=2),
         "kind": "text", "size": 0},
        {"name": f"{label}.per_residue.json",
         "content": json.dumps(per_residue_series, indent=2),
         "kind": "text", "size": 0},
        {"name": f"{label}.high_divergence.json",
         "content": json.dumps(high_divergence, indent=2),
         "kind": "text", "size": 0},
    ])

    return {
        "summary": summary,
        "pairwise": pairwise,
        "per_residue_series": per_residue_series,
        "high_divergence_residues": high_divergence,
        "output_files": output_files,
    }


def _run_struct_align_usalign(
    structure_inputs: list[dict[str, str]],
    params: dict[str, Any],
    label: str,
    log=None,
) -> dict[str, Any]:
    """Structure-FIRST alignment: superpose on geometry via US-align/TM-align,
    then read the residue correspondence (aligned sequences) off the result.

    structure[0] is the reference; every other is aligned onto it. Emits:
      - per-pair TM-score / RMSD / structure-based % identity
      - a structure-derived alignment FASTA (reference + each aligned mobile seq)
      - each superposed structure as PDB (all in the reference frame)
    """
    def _emit(m: str) -> None:
        if log:
            log(m)

    if not usalign_mod.usalign_available():
        raise RuntimeError(
            "Structure-first mode needs US-align or TM-align on PATH. Install via "
            "`pixi add -c bioconda usalign` (or tmalign), or use method='seq_guided'."
        )

    ref = structure_inputs[0]
    _emit(f"reference: {ref['name']}")

    work = tempfile.mkdtemp(prefix="usalign_")
    output_files: list[dict[str, Any]] = []
    pair_results: list[dict[str, Any]] = []
    # Alignment FASTA accumulates the reference once, then each mobile's
    # structure-aligned sequence. Because each pairwise alignment may gap the
    # reference differently, we keep them as separate pairwise records rather
    # than forcing one MSA column system (honest about what US-align gives).
    aln_fasta_parts: list[str] = []

    try:
        ref_path = os.path.join(work, "ref.pdb")
        # Convert ref to a clean PDB via Bio.PDB so US-align gets consistent input.
        ref_struct = _structure_from_text(ref["text"], ref["name"])
        with open(ref_path, "w") as fh:
            fh.write(_structure_to_pdb_text(ref_struct))

        for i, mob in enumerate(structure_inputs[1:], start=1):
            _emit(f"[{i}/{len(structure_inputs)-1}] aligning {mob['name']} → {ref['name']}")
            mob_path = os.path.join(work, f"mob_{i}.pdb")
            mob_struct = _structure_from_text(mob["text"], mob["name"])
            with open(mob_path, "w") as fh:
                fh.write(_structure_to_pdb_text(mob_struct))

            out_prefix = os.path.join(work, f"sup_{i}")
            res = usalign_mod.run_usalign_pair(ref_path, mob_path, out_prefix, log=log)

            pair_results.append({
                "reference": ref["name"],
                "mobile": mob["name"],
                "tm_score_ref": res["tm_score_ref"],
                "tm_score_mob": res["tm_score_mob"],
                "rmsd": res["rmsd"],
                "aligned_length": res["aligned_length"],
                "structure_seq_identity": res["seq_identity"],
                "engine": res["flavor"],
            })
            _emit(f"    TM-score(ref)={res['tm_score_ref']} "
                  f"RMSD={res['rmsd']}Å aligned={res['aligned_length']} "
                  f"structβ-ID={res['seq_identity']:.3f}")

            # Structure-derived aligned sequences for this pair.
            if res["aligned_ref"] and res["aligned_mob"]:
                if i == 1:
                    aln_fasta_parts.append(f">{ref['name']}\n{res['aligned_ref']}")
                aln_fasta_parts.append(f">{mob['name']}__vs__{ref['name']}\n{res['aligned_mob']}")

            # Superposed mobile structure (in reference frame).
            if res["superposed_path"] and os.path.exists(res["superposed_path"]):
                sup_text = open(res["superposed_path"]).read()
                output_files.append({
                    "name": f"{label}_{mob['name']}_superposed.pdb",
                    "content": sup_text,
                    "kind": "pdb",
                    "size": len(sup_text),
                    "source_metadata": {"reference": ref["name"], "mobile": mob["name"],
                                        "stage": "superposed"},
                })

        # Reference structure (frame origin) so the viewer can load everything.
        ref_pdb_text = open(ref_path).read()
        output_files.insert(0, {
            "name": f"{label}_{ref['name']}_reference.pdb",
            "content": ref_pdb_text,
            "kind": "pdb",
            "size": len(ref_pdb_text),
            "source_metadata": {"reference": ref["name"], "stage": "reference"},
        })

        if aln_fasta_parts:
            aln_text = "\n".join(aln_fasta_parts) + "\n"
            output_files.append({
                "name": f"{label}_structure_alignment.fasta",
                "content": aln_text,
                "kind": "aligned_fasta",
                "size": len(aln_text),
                "source_metadata": {"stage": "structure_derived_alignment"},
            })

        return {
            "method": "usalign",
            "engine": pair_results[0]["engine"] if pair_results else "unknown",
            "reference": ref["name"],
            "n_structures": len(structure_inputs),
            "pairwise": pair_results,
            "output_files": output_files,
            # Surface the structure-derived alignment for the viewer's linked panel.
            "structure_alignment": aln_fasta_parts,
        }
    finally:
        try:
            for f in os.listdir(work):
                os.unlink(os.path.join(work, f))
            os.rmdir(work)
        except Exception:
            pass


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str, log=None) -> dict[str, Any]:
    # Same pattern as mhcxgraph: structure files are passed via a comma-
    # separated string parameter (from the project_files_multi picker).
    raw = params.get("structure_file_ids", "")
    if isinstance(raw, str):
        file_ids = [s.strip() for s in raw.split(",") if s.strip()]
    elif isinstance(raw, list):
        file_ids = [str(s).strip() for s in raw if str(s).strip()]
    else:
        file_ids = []

    structure_inputs = await asyncio.to_thread(_resolve_structure_files, file_ids, project_id)

    # Output naming: derive a label from the first structure's name.
    if structure_inputs:
        label = f"struct_align_{structure_inputs[0]['name']}_vs_{len(structure_inputs) - 1}"
    else:
        label = "struct_align"

    method = (params.get("method") or "usalign").strip()
    if method == "usalign":
        return await asyncio.to_thread(
            _run_struct_align_usalign, structure_inputs, params, label, log
        )
    # seq_guided (legacy): sequence-aligned Cα superposition.
    return await asyncio.to_thread(_run_struct_align, structure_inputs, params, label)


register(ToolDef(
    id="struct_align",
    label="Structural alignment & superposition",
    description=(
        "Align 2+ protein structures (PDB/CIF) and superpose them. Default "
        "'usalign' mode aligns on 3D GEOMETRY (US-align/TM-align) then reads the "
        "sequence correspondence off the superposition — correct for diverged "
        "homologs (e.g. MAYV vs CHIKV) where sequence alignment fails. 'seq_guided' "
        "mode pairs Cα atoms by sequence alignment instead. Outputs TM-score/RMSD, "
        "the structure-derived sequence alignment, and superposed structures for "
        "the viewer."
    ),
    input_kind="none",
    params=[
        ParamDef(
            name="structure_file_ids",
            type="project_files_multi",
            label="Structures (PDB/CIF)",
            default="",
            kind="pdb,cif",
            min=2, max=20,
            help=(
                "Pick 2-20 structures. The first selected is the reference; all "
                "others are superposed onto it."
            ),
        ),
        ParamDef(
            name="method",
            type="enum",
            label="Alignment method",
            default="usalign",
            options=["usalign", "seq_guided"],
            help=(
                "usalign: structure-first (geometry), best for diverged proteins. "
                "seq_guided: pair Cα by sequence alignment (needs similar sequences)."
            ),
        ),
        ParamDef(
            name="chain",
            type="string",
            label="Chain ID (seq_guided only)",
            default="",
            help=(
                "Restrict seq_guided to a chain (e.g. A). Blank = the chain with "
                "the most standard residues. US-align handles chains automatically."
            ),
        ),
        ParamDef(
            name="high_distance_threshold",
            type="float",
            label="Divergence threshold (Å, seq_guided)",
            default=3.0,
            min=0.5, max=20.0,
            help=(
                "seq_guided only: residues with Cα-Cα distance above this after "
                "superposition are flagged structurally divergent."
            ),
        ),
    ],
    run=run,
))
