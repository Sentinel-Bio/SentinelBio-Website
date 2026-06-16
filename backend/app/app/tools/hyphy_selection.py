"""HyPhy selection analysis (FEL primary, MEME optional).

FEL (Fixed Effects Likelihood) estimates per-site dN/dS (ω) by fitting a
codon model at each alignment column. Output:
  - per-codon ω values
  - p-values for selection
  - classification: negative (purifying) / neutral / positive

For each codon you get a quantitative answer to "is this site under purifying
selection?" which is exactly the prior you need to predict deleteriousness.

MEME (Mixed Effects Model of Evolution) detects episodic positive selection —
sites that experienced positive selection on a subset of branches. More
sensitive than FEL for positive selection, complementary to it.

Inputs:
  - A codon-level multiple alignment (MAFFT aligned CDS, or AA alignment
    back-translated with PAL2NAL — we just trust that the file is codon-aligned)
  - A phylogenetic tree in Newick format (from IQ-TREE)

Tool wraps `hyphy CPU=auto fel ...` as subprocess. HyPhy outputs JSON which
we parse into a structured per-site result. Saved as a `<gene>_fel.json`
project file plus a TSV summary.

Install: pixi adds it from bioconda (`hyphy >=2.5`).
"""
from __future__ import annotations

import asyncio
import json
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


def _read_project_file_text(file_id: str, project_id: str) -> tuple[str, str]:
    """(text, name) of a project file."""
    client = service_client()
    fres = (
        client.table("project_files")
        .select("storage_path, name")
        .eq("id", file_id).maybe_single().execute()
    )
    if not fres or not fres.data:
        raise ValueError(f"file {file_id} not found")
    text = filestore.read_text(project_id, fres.data["storage_path"])
    return text, fres.data["name"]


def _validate_codon_alignment(fasta: str) -> tuple[int, int]:
    """Returns (n_sequences, alignment_length). Errors if length not divisible
    by 3 or sequences have different lengths."""
    seqs: dict[str, str] = {}
    cur_name = None
    cur_buf: list[str] = []
    for line in fasta.splitlines():
        if line.startswith(">"):
            if cur_name is not None:
                seqs[cur_name] = "".join(cur_buf)
            cur_name = line[1:].split()[0]
            cur_buf = []
        else:
            cur_buf.append(line.strip())
    if cur_name is not None:
        seqs[cur_name] = "".join(cur_buf)

    if len(seqs) < 4:
        raise ValueError(
            f"HyPhy FEL needs at least 4 sequences (got {len(seqs)}). "
            "Selection analysis on 3 species is statistically meaningless."
        )

    lengths = {len(s) for s in seqs.values()}
    if len(lengths) != 1:
        raise ValueError(
            f"Alignment sequences have different lengths {sorted(lengths)}. "
            "Re-align with MAFFT before running HyPhy."
        )

    L = lengths.pop()
    if L % 3 != 0:
        raise ValueError(
            f"Alignment length {L} is not divisible by 3. "
            "HyPhy FEL needs a codon-aware alignment. "
            "Align CDS sequences (not proteins) with MAFFT, "
            "or align proteins then back-translate with PAL2NAL."
        )
    return len(seqs), L


def _run_hyphy(method: str, alignment: str, tree: str, srv: str) -> dict[str, Any]:
    """Run hyphy <method> on alignment + tree. Returns parsed JSON result."""
    if not _has("hyphy"):
        raise RuntimeError(
            "hyphy not installed. Add to pixi.toml from bioconda channel: hyphy >=2.5"
        )

    work_dir = tempfile.mkdtemp(prefix=f"hyphy_{method}_")
    aln_path = os.path.join(work_dir, "alignment.fasta")
    tree_path = os.path.join(work_dir, "tree.nwk")
    out_path = os.path.join(work_dir, f"{method}.json")

    try:
        Path(aln_path).write_text(alignment)
        Path(tree_path).write_text(tree.strip() + "\n")

        cmd = [
            "hyphy", "CPU=auto", method,
            "--alignment", aln_path,
            "--tree", tree_path,
            "--output", out_path,
        ]
        if method == "fel":
            cmd.extend(["--srv", srv])

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if r.returncode != 0:
            raise RuntimeError(
                f"hyphy {method} failed:\n"
                f"stdout: {r.stdout[-2000:]}\n"
                f"stderr: {r.stderr[-2000:]}"
            )
        if not os.path.exists(out_path):
            raise RuntimeError(
                f"hyphy {method} completed but produced no JSON. "
                f"Output tail: {r.stdout[-1000:]}"
            )
        with open(out_path) as f:
            return json.load(f)
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


def _summarize_fel(fel_json: dict[str, Any], p_threshold: float) -> dict[str, Any]:
    """Extract per-site rows: codon position, alpha (synonymous rate), beta
    (non-synonymous rate), omega = beta/alpha, p-value, classification."""
    mle = fel_json.get("MLE", {})
    headers = mle.get("headers", [])
    content = mle.get("content", {}).get("0", [])
    if not content:
        return {"sites": [], "n_sites": 0, "summary": {}}

    # Header indices we care about (FEL standard schema)
    def _col(name: str) -> int:
        for i, h in enumerate(headers):
            label = h[0] if isinstance(h, list) else h
            if label == name:
                return i
        return -1

    i_alpha = _col("alpha")
    i_beta = _col("beta")
    i_p = _col("p-value")
    i_lrt = _col("LRT")

    sites = []
    n_neg = n_pos = n_neutral = 0
    for codon_idx, row in enumerate(content, start=1):
        try:
            alpha = float(row[i_alpha]) if i_alpha >= 0 else 0.0
            beta = float(row[i_beta]) if i_beta >= 0 else 0.0
            pval = float(row[i_p]) if i_p >= 0 else 1.0
            lrt = float(row[i_lrt]) if i_lrt >= 0 else 0.0
        except (IndexError, ValueError, TypeError):
            continue

        # omega = beta / alpha (NaN if alpha == 0; treat as "uninformative")
        omega = beta / alpha if alpha > 0 else (float("inf") if beta > 0 else 0.0)

        cls = "neutral"
        if pval < p_threshold:
            if beta < alpha:
                cls = "negative"  # purifying selection
                n_neg += 1
            elif beta > alpha:
                cls = "positive"  # positive selection
                n_pos += 1
            else:
                n_neutral += 1
        else:
            n_neutral += 1

        sites.append({
            "codon": codon_idx,
            "alpha": alpha,
            "beta": beta,
            "omega": omega if omega != float("inf") else None,
            "lrt": lrt,
            "p_value": pval,
            "classification": cls,
        })

    return {
        "sites": sites,
        "n_sites": len(sites),
        "summary": {
            "p_threshold": p_threshold,
            "n_negative_selection": n_neg,
            "n_positive_selection": n_pos,
            "n_neutral": n_neutral,
        },
    }


def _sites_to_tsv(sites: list[dict[str, Any]]) -> str:
    lines = ["codon\talpha\tbeta\tomega\tlrt\tp_value\tclassification"]
    for s in sites:
        omega_str = f"{s['omega']:.4f}" if s["omega"] is not None else "inf"
        lines.append(
            f"{s['codon']}\t{s['alpha']:.4f}\t{s['beta']:.4f}\t"
            f"{omega_str}\t{s['lrt']:.4f}\t{s['p_value']:.4g}\t{s['classification']}"
        )
    return "\n".join(lines) + "\n"


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    alignment_file_id = (params.get("alignment_file_id") or "").strip()
    tree_file_id = (params.get("tree_file_id") or "").strip()
    method = (params.get("method") or "fel").strip().lower()
    p_threshold = float(params.get("p_threshold", 0.1))
    srv = (params.get("srv") or "Yes").strip()

    if method not in ("fel", "meme"):
        raise ValueError(f"unsupported method: {method} (use 'fel' or 'meme')")
    if not alignment_file_id:
        raise ValueError("alignment_file_id required (codon-aligned MAFFT output)")
    if not tree_file_id:
        raise ValueError("tree_file_id required (IQ-TREE newick output)")

    alignment, aln_name = await asyncio.to_thread(
        _read_project_file_text, alignment_file_id, project_id,
    )
    tree, _ = await asyncio.to_thread(
        _read_project_file_text, tree_file_id, project_id,
    )

    n_seqs, aln_len = _validate_codon_alignment(alignment)

    hyphy_json = await asyncio.to_thread(_run_hyphy, method, alignment, tree, srv)

    if method == "fel":
        parsed = _summarize_fel(hyphy_json, p_threshold)
    else:
        # MEME parsing is similar but uses different headers — we just keep the
        # raw JSON for now and let user inspect. Reserved for next iteration.
        parsed = {"sites": [], "n_sites": 0, "summary": {"raw_only": True}}

    aln_stem = re.sub(r"\.(fasta|fa|fna|aln|fas)$", "", aln_name, flags=re.IGNORECASE)
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", aln_stem)[:48]

    out_json = json.dumps(hyphy_json, indent=2)
    out_tsv = _sites_to_tsv(parsed["sites"]) if parsed["sites"] else ""

    output_files = [
        {"name": f"{label}.{method}.json", "content": out_json, "kind": "text", "size": len(out_json)},
    ]
    if out_tsv:
        output_files.append(
            {"name": f"{label}.{method}.tsv", "content": out_tsv, "kind": "text", "size": len(out_tsv)},
        )

    return {
        "method": method,
        "n_sequences": n_seqs,
        "alignment_length": aln_len,
        "n_codons": aln_len // 3,
        "summary": parsed["summary"],
        # We pass sites array back too so the viewer can color a structure
        # without re-fetching the JSON file.
        "sites": parsed["sites"],
        "output_files": output_files,
    }


register(ToolDef(
    id="hyphy_selection",
    label="HyPhy selection (FEL / dN-dS)",
    description=(
        "Per-codon dN/dS analysis to identify sites under purifying "
        "selection (conserved, deleterious-if-mutated) or positive selection. "
        "Needs a codon-aligned multi-FASTA (4+ species) and a phylogeny."
    ),
    input_kind="none",
    params=[
        ParamDef(name="alignment_file_id", type="project_file_fasta",
                 label="Codon-aligned MAFFT FASTA",
                 default="",
                 help="Align CDS sequences (not proteins) with MAFFT first. "
                      "Length must be divisible by 3."),
        ParamDef(name="tree_file_id", type="project_file",
                 label="Phylogenetic tree (Newick)",
                 default="",
                 help="From IQ-TREE on the same alignment. Pick the .newick file."),
        ParamDef(name="method", type="enum", label="Method",
                 default="fel", options=["fel", "meme"],
                 help="FEL = per-site dN/dS (your usual case). "
                      "MEME = episodic positive selection (rarer use case)."),
        ParamDef(name="p_threshold", type="float", label="Significance p-threshold",
                 default=0.1, min=0.001, max=0.5,
                 help="Standard is 0.1 for FEL (sensitive)."),
        ParamDef(name="srv", type="enum", label="Synonymous rate variation",
                 default="Yes", options=["Yes", "No"],
                 help="Allow synonymous rates to vary across sites. Recommended Yes."),
    ],
    run=run,
))
