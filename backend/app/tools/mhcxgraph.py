"""MHCXGraph wrapper — graph-based structural conservation detection.

MHCXGraph (https://github.com/cnpem/MHCXGraph) detects structurally conserved
surface regions across protein structures via a triad-based geometric and
physicochemical comparison framework. Originally built for pMHC complexes but
the underlying machinery is general — works on any set of PDB/CIF structures.

For SF3B1 (or any non-MHC protein) we use a generic surface-residue selector:
all chains, all secondary structures, RSA-exposed residues only. This finds
structurally conserved surface patches across pinniped SF3B1 predictions,
which is a *3D* conservation signal distinct from per-residue sequence
conservation.

Modes:
  - multiple: find frames (conserved residue sets) common to ALL inputs
  - pairwise: per-pair conserved sets; useful for asymmetric comparisons

Install: `pip install MHCXGraph` (or via pixi from pypi).

Outputs (saved as project files):
  - The MHCXGraph output directory tarred up (contains JSON frames, dashboards)
  - A simplified frames.json with the residue lists per frame for downstream use

Citation: Santos Simões, C. D. M. et al. (2026). MHCXGraph: A Graph-Based
approach to detecting T cell receptor cross-reactivity.
https://doi.org/10.64898/2026.04.07.717034
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from app.files import store as filestore
from app.supabase_client import service_client
from app.tools.registry import ToolDef, ParamDef, register


def _has(name: str) -> bool:
    return shutil.which(name) is not None


def _resolve_pdb_files(file_ids: list[str], project_id: str) -> list[dict[str, str]]:
    """Returns list of {id, name, abs_path} for the requested project files."""
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
                "MHCXGraph needs PDB or CIF structures"
            )
        abs_path = str(filestore.absolute_path(project_id, r["storage_path"]))
        out.append({"id": r["id"], "name": r["name"], "abs_path": abs_path})
    if len(out) < 2:
        raise ValueError("MHCXGraph needs at least 2 structures to compare")
    return out


def _build_manifest(
    input_dir: str,
    out_dir: str,
    run_name: str,
    run_mode: str,
    edge_threshold: float,
    node_granularity: str,
    rsa_filter: float,
    selector_preset: str,
    custom_selector_json: str | None,
) -> dict[str, Any]:
    """Build a MHCXGraph manifest dict. For non-MHC proteins (our SF3B1 case)
    we use a generic 'protein_surface' selector."""

    # Selector presets for non-MHC use.
    if selector_preset == "custom" and custom_selector_json:
        selector_block = json.loads(custom_selector_json)
    elif selector_preset == "all_surface":
        # All chains, all residues — relies on rsa_filter to keep only surface.
        # Works because MHCXGraph filters by RSA before building triads.
        # The empty residues dict means "use all residues".
        # We don't constrain chains; this picks up whatever's in the structure.
        # Note: still needs at least one chain ID — we use 'A' which is the
        # default for AlphaFold predictions (single chain). For multi-chain
        # PDBs, use 'custom' preset.
        selector_block = {
            "protein_surface": {
                "chains": ["A"],
                "structures": {},
                "residues": {},
            },
        }
    elif selector_preset == "helix_only":
        # Restrict to helical surface (good for HEAT repeats in SF3B1).
        selector_block = {
            "protein_surface": {
                "chains": ["A"],
                "structures": ["helix"],
                "residues": {},
            },
        }
    else:
        raise ValueError(f"unknown selector_preset: {selector_preset}")

    return {
        "settings": {
            "run_name": run_name,
            "run_mode": run_mode,
            "output_path": out_dir,
            "edge_threshold": edge_threshold,
            "node_granularity": node_granularity,
            "triad_rsa": False,
            "rsa_filter": rsa_filter,
            "global_distance_diff_threshold": 2.0,
            "local_distance_diff_threshold": 1.0,
            "distance_bin_width": 2,
        },
        "inputs": [
            {
                "path": input_dir,
                "extensions": [".pdb", ".cif"],
                "selectors": [{"name": "protein_surface"}],
            }
        ],
        "selectors": selector_block,
    }


def _run_mhcxgraph(manifest_path: str) -> str:
    """Invoke MHCXGraph. Returns stdout tail."""
    if not _has("MHCXGraph"):
        raise RuntimeError(
            "MHCXGraph not installed. Install via pip: pip install MHCXGraph"
        )
    r = subprocess.run(
        ["MHCXGraph", "run", manifest_path],
        capture_output=True, text=True, timeout=7200,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"MHCXGraph failed:\n"
            f"stdout: {r.stdout[-3000:]}\n"
            f"stderr: {r.stderr[-2000:]}"
        )
    return r.stdout[-3000:]


def _collect_frame_summary(output_dir: str) -> dict[str, Any]:
    """Walk the output dir, pull frame JSONs, build a concise summary.
    
    MHCXGraph produces several JSONs; we capture the frame nodes / residue
    summaries as they appear. We try a few known filename patterns and fall
    back to "everything that looks like JSON."
    """
    output_path = Path(output_dir)
    if not output_path.exists():
        return {"frames": [], "note": "output_dir missing"}

    # Search for any *.json under the output
    json_files = sorted(output_path.rglob("*.json"))
    if not json_files:
        return {
            "frames": [],
            "note": "no JSON output found",
            "files_seen": sorted(str(p.relative_to(output_path)) for p in output_path.rglob("*"))[:50],
        }

    # Try to locate the canonical frames file. Patterns we look for, in order:
    candidates = (
        list(output_path.rglob("frames*.json"))
        + list(output_path.rglob("*frame_nodes*.json"))
        + list(output_path.rglob("*evaluation*.json"))
    )
    if not candidates:
        candidates = json_files[:3]  # take first few JSONs as best guess

    summaries = []
    for cand in candidates[:5]:
        try:
            with cand.open() as f:
                data = json.load(f)
            preview: Any
            if isinstance(data, dict):
                # If it's a frame dict, summarize keys + counts
                preview = {
                    "keys": list(data.keys())[:20],
                    "n_keys": len(data),
                }
                # Pull out residue lists if present
                for k in ("frames", "frame_nodes", "residues", "nodes"):
                    if k in data:
                        v = data[k]
                        if isinstance(v, dict):
                            preview[k + "_keys"] = list(v.keys())[:50]
                            preview[k + "_n"] = len(v)
                        elif isinstance(v, list):
                            preview[k + "_n"] = len(v)
                            preview[k + "_sample"] = v[:10]
            elif isinstance(data, list):
                preview = {"length": len(data), "sample": data[:5]}
            else:
                preview = {"type": type(data).__name__}
            summaries.append({
                "file": str(cand.relative_to(output_path)),
                "preview": preview,
            })
        except Exception as e:
            summaries.append({
                "file": str(cand.relative_to(output_path)),
                "error": str(e),
            })

    return {
        "frame_files": summaries,
        "total_json_files": len(json_files),
    }


def _tar_output(output_dir: str) -> bytes:
    """Tar+gzip the output dir for downloading as one project file."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(output_dir, arcname=os.path.basename(output_dir))
    return buf.getvalue()


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    structure_file_ids = params.get("structure_file_ids", "")
    if isinstance(structure_file_ids, str):
        # CSV string from the frontend (we add a multi-file picker later;
        # for now accept comma-separated IDs).
        structure_file_ids = [s.strip() for s in structure_file_ids.split(",") if s.strip()]

    run_name = (params.get("run_name") or "structural-conservation").strip()
    run_mode = (params.get("run_mode") or "multiple").strip()
    edge_threshold = float(params.get("edge_threshold", 8.5))
    node_granularity = (params.get("node_granularity") or "all_atoms").strip()
    rsa_filter = float(params.get("rsa_filter", 0.1))
    selector_preset = (params.get("selector_preset") or "all_surface").strip()
    custom_selector_json = params.get("custom_selector_json") or None

    if run_mode not in ("pairwise", "multiple", "screening"):
        raise ValueError(f"invalid run_mode: {run_mode}")

    files = await asyncio.to_thread(_resolve_pdb_files, structure_file_ids, project_id)

    work_dir = tempfile.mkdtemp(prefix="mhcxg_")
    try:
        input_dir = os.path.join(work_dir, "input")
        os.makedirs(input_dir)
        # MHCXGraph reads structures from a directory. Copy our files in.
        for f in files:
            shutil.copy2(f["abs_path"], os.path.join(input_dir, f["name"]))

        output_dir = os.path.join(work_dir, "output")
        os.makedirs(output_dir)

        manifest = _build_manifest(
            input_dir, output_dir, run_name, run_mode,
            edge_threshold, node_granularity, rsa_filter,
            selector_preset, custom_selector_json,
        )
        manifest_path = os.path.join(work_dir, "manifest.json")
        Path(manifest_path).write_text(json.dumps(manifest, indent=2))

        stdout_tail = await asyncio.to_thread(_run_mhcxgraph, manifest_path)
        summary = _collect_frame_summary(output_dir)
        tar_bytes = await asyncio.to_thread(_tar_output, output_dir)

        return {
            "run_mode": run_mode,
            "n_structures": len(files),
            "structures": [{"id": f["id"], "name": f["name"]} for f in files],
            "summary": summary,
            "stdout_tail": stdout_tail,
            "output_files": [
                {
                    "name": f"{run_name}_mhcxgraph.tar.gz",
                    "content": tar_bytes,  # bytes — worker will pass through
                    "kind": "other",
                    "size": len(tar_bytes),
                },
                {
                    "name": f"{run_name}_summary.json",
                    "content": json.dumps(summary, indent=2),
                    "kind": "text",
                    "size": 0,  # filled by worker
                },
            ],
        }
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


register(ToolDef(
    id="mhcxgraph",
    label="Structural conservation (MHCXGraph)",
    description=(
        "Find structurally conserved surface regions across multiple protein "
        "structures via graph-based triad comparison. Outputs 'frames' of "
        "residues that share local 3D geometry across all (or each pair of) "
        "input structures. Complements sequence-based conservation by adding "
        "a 3D dimension."
    ),
    input_kind="none",
    params=[
        ParamDef(name="structure_file_ids", type="project_files_multi",
                 label="Structures (PDB/CIF)",
                 default="",
                 kind="pdb,cif",
                 min=2, max=20,
                 help="Pick 2-20 PDB/CIF files to compare."),
        ParamDef(name="run_name", type="string", label="Run name",
                 default="structural-conservation",
                 help="Used for output filenames."),
        ParamDef(name="run_mode", type="enum", label="Mode",
                 default="multiple", options=["multiple", "pairwise"],
                 help="multiple = frames conserved across ALL structures. "
                      "pairwise = conserved per pair."),
        ParamDef(name="selector_preset", type="enum", label="Residue selector preset",
                 default="all_surface",
                 options=["all_surface", "helix_only", "custom"],
                 help="all_surface = all RSA-exposed residues on chain A "
                      "(works for AlphaFold predictions). "
                      "helix_only = HEAT-repeat appropriate. "
                      "custom = paste your own selector JSON."),
        ParamDef(name="custom_selector_json", type="text_long",
                 label="Custom selector JSON (only used if preset=custom)",
                 default="",
                 help="A JSON object matching MHCXGraph's selectors block."),
        ParamDef(name="edge_threshold", type="float",
                 label="Edge threshold (Å)",
                 default=8.5, min=4.0, max=15.0,
                 help="Distance cutoff for edges in the residue graph."),
        ParamDef(name="rsa_filter", type="float",
                 label="RSA filter",
                 default=0.1, min=0.0, max=1.0,
                 help="Min relative solvent accessibility (0-1). "
                      "0.1 = at least 10% of residue surface exposed."),
        ParamDef(name="node_granularity", type="enum",
                 label="Node atomic representation",
                 default="all_atoms",
                 options=["all_atoms", "ca_only", "backbone", "sidechain"],
                 help="What atoms represent each residue in the graph."),
    ],
    run=run,
))
