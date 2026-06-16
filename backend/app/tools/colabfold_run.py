"""Local AlphaFold2 structure prediction via ColabFold.

Wraps `colabfold_batch` as a subprocess and saves the best-ranked PDB to
project files. Designed for sequential one-protein-at-a-time use on a
local GPU — A. gazella SF3B1 (1,304 aa) takes ~30-45 min on an RTX 5070
with `--num-models 1` settings.

Setup requirements (one-time):
  - Install ColabFold + JAX with CUDA support:
      pip install --user --break-system-packages \
          "colabfold[alphafold] @ git+https://github.com/sokrypton/ColabFold"
      pip install --user --break-system-packages \
          --upgrade "jax[cuda12_pip]" -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html
  - First run downloads AlphaFold params (~3 GB) to ~/.cache/colabfold
  - Verify: `colabfold_batch --version`

If GPU fails, ColabFold falls back to CPU — that takes ~6 hours for SF3B1,
so verify GPU detection before kicking off long runs:
  python -c "import jax; print(jax.devices())"  # should list a CUDA device

For Blackwell (5070), if JAX shows CPU only, you need:
  pip install --upgrade "jax[cuda12]==0.4.30" "jaxlib==0.4.30+cuda12.cudnn89"

Tool runs in the worker (set MAX_CONCURRENT=1 if you don't want overlapping
GPU jobs; ColabFold is not gentle about VRAM sharing).
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

from app.tools.registry import ToolDef, ParamDef, register


def _has(name: str) -> bool:
    return shutil.which(name) is not None


def _run_colabfold(
    sequence: str,
    name: str,
    num_models: int,
    num_recycles: int,
    use_amber: bool,
    msa_mode: str,
) -> dict[str, Any]:
    if not _has("colabfold_batch"):
        raise RuntimeError(
            "colabfold_batch not on PATH. Install ColabFold (see tool docs)."
        )

    work_dir = tempfile.mkdtemp(prefix="colabfold_")
    try:
        # Write input FASTA
        fasta_path = os.path.join(work_dir, "input.fasta")
        with open(fasta_path, "w") as f:
            if not sequence.startswith(">"):
                f.write(f">{name}\n")
            f.write(sequence)
            if not sequence.endswith("\n"):
                f.write("\n")

        out_dir = os.path.join(work_dir, "out")
        os.makedirs(out_dir, exist_ok=True)

        cmd = [
            "colabfold_batch",
            "--num-models", str(num_models),
            "--num-recycle", str(num_recycles),
            "--msa-mode", msa_mode,
            fasta_path,
            out_dir,
        ]
        if use_amber:
            cmd.append("--amber")
        else:
            # Default is to use AMBER if available; --num-relax 0 disables.
            cmd.extend(["--num-relax", "0"])

        # Run. ColabFold prints lots to stdout; we capture for the job log.
        # Long timeout: SF3B1 on a 5070 can take 40 min. CPU fallback up to 6 h.
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=8 * 3600,
            env={**os.environ, "TF_FORCE_UNIFIED_MEMORY": "1"},
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"colabfold_batch failed (rc={r.returncode}):\n"
                f"STDOUT tail: {r.stdout[-2000:]}\n"
                f"STDERR tail: {r.stderr[-2000:]}"
            )

        # Find the best-ranked output PDB. ColabFold names files like:
        #   <name>_unrelaxed_rank_001_alphafold2_ptm_model_<i>_seed_<s>.pdb
        # (relaxed_ if AMBER was applied)
        pdb_candidates = sorted(Path(out_dir).glob("*_rank_001_*.pdb"))
        if not pdb_candidates:
            # Fallback: any .pdb
            pdb_candidates = sorted(Path(out_dir).glob("*.pdb"))
        if not pdb_candidates:
            raise RuntimeError(
                f"colabfold completed but produced no .pdb in {out_dir}. "
                f"Last 500 chars of stdout: {r.stdout[-500:]}"
            )
        best_pdb = pdb_candidates[0]
        pdb_text = best_pdb.read_text()

        # Try to read the scores JSON for pLDDT / pTM.
        scores: dict[str, Any] = {}
        score_files = sorted(Path(out_dir).glob("*_rank_001_*_scores.json"))
        if score_files:
            try:
                import json
                with score_files[0].open() as f:
                    scores_raw = json.load(f)
                # Trim plddt array if too long
                if "plddt" in scores_raw and isinstance(scores_raw["plddt"], list):
                    plddt_arr = scores_raw["plddt"]
                    scores["plddt_mean"] = sum(plddt_arr) / len(plddt_arr)
                    scores["plddt_min"] = min(plddt_arr)
                    scores["plddt_max"] = max(plddt_arr)
                    # Keep per-residue for downstream coloring
                    scores["plddt_per_residue"] = plddt_arr
                if "ptm" in scores_raw:
                    scores["ptm"] = scores_raw["ptm"]
            except Exception as e:
                scores["scores_read_error"] = str(e)

        # Length of predicted structure
        n_residues = 0
        for line in pdb_text.splitlines():
            if line.startswith("ATOM") and line[12:16].strip() == "CA":
                n_residues += 1

        return {
            "pdb_text": pdb_text,
            "pdb_filename": best_pdb.name,
            "n_residues": n_residues,
            "scores": scores,
            "stdout_tail": r.stdout[-2000:],
        }
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    sequence = (params.get("sequence") or "").strip()
    name = (params.get("name") or "prediction").strip()
    num_models = int(params.get("num_models", 1))
    num_recycles = int(params.get("num_recycles", 3))
    use_amber = bool(params.get("use_amber", False))
    msa_mode = (params.get("msa_mode") or "mmseqs2_uniref_env").strip()

    if not sequence:
        raise ValueError("sequence required (protein FASTA or raw amino acid sequence)")

    # Sanitize: if sequence has FASTA header, keep it; else add one
    if not sequence.startswith(">"):
        # Validate that this looks like a protein sequence
        clean = re.sub(r"\s+", "", sequence)
        if not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYBXZJUO*-]+", clean, re.IGNORECASE):
            raise ValueError(
                "Input doesn't look like a protein sequence. If you have a DNA "
                "FASTA, translate it first (e.g. with the annotate_gene tool)."
            )

    name_safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:48] or "prediction"

    result = await asyncio.to_thread(
        _run_colabfold, sequence, name_safe, num_models, num_recycles, use_amber, msa_mode,
    )

    pdb_filename = f"{name_safe}_alphafold.pdb"
    return {
        "name": name_safe,
        "n_residues": result["n_residues"],
        "scores": result["scores"],
        "source_pdb_name": result["pdb_filename"],
        "stdout_tail": result["stdout_tail"],
        "output_files": [
            {
                "name": pdb_filename,
                "content": result["pdb_text"],
                "kind": "pdb",
                "size": len(result["pdb_text"]),
            }
        ],
    }


register(ToolDef(
    id="colabfold",
    label="Predict structure (ColabFold)",
    description=(
        "Run AlphaFold2 via ColabFold on a protein sequence. Local GPU. "
        "Use only for proteins NOT in the AlphaFold DB (e.g. A. gazella SF3B1 "
        "after Exonerate annotation). For human / Z. californianus / well-studied "
        "species, use 'AlphaFold DB fetch' instead — it's instant and higher quality."
    ),
    input_kind="none",
    params=[
        ParamDef(name="sequence", type="text_long", label="Protein sequence",
                 default="",
                 help="FASTA-format protein, OR raw amino acid string. "
                      "Run this on the .protein.fasta output of annotate_gene."),
        ParamDef(name="name", type="string", label="Prediction name",
                 default="prediction",
                 help="Used for filenames. e.g. 'A_gazella_SF3B1'."),
        ParamDef(name="num_models", type="int", label="Number of models",
                 default=1, min=1, max=5,
                 help="1 = fastest (~30 min for SF3B1). 5 = full pipeline (~3 h)."),
        ParamDef(name="num_recycles", type="int", label="Recycles per model",
                 default=3, min=1, max=12,
                 help="More recycles = sometimes better quality, always slower. 3 default."),
        ParamDef(name="use_amber", type="bool", label="AMBER relaxation",
                 default=False,
                 help="Final geometry relaxation. Adds ~3 min, marginal quality gain."),
        ParamDef(name="msa_mode", type="enum", label="MSA mode",
                 default="mmseqs2_uniref_env",
                 options=["mmseqs2_uniref_env", "mmseqs2_uniref", "single_sequence"],
                 help="single_sequence = no MSA (fast, low quality). "
                      "Others use MMseqs2 against UniRef. Default is best for accuracy."),
    ],
    run=run,
))
