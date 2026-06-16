"""Phylogenetic inference via IQ-TREE."""
from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register
from app.tools._subprocess import stream_subprocess


def _run_iqtree(fasta_text: str, params: dict[str, Any], label: str, log=None) -> dict[str, Any]:
    def _emit(m: str) -> None:
        if log:
            log(m)

    if not fasta_text.strip():
        raise ValueError("empty input alignment")

    work_dir = tempfile.mkdtemp(prefix="iqtree_")
    in_path = os.path.join(work_dir, "input.fasta")
    Path(in_path).write_text(fasta_text)
    _emit(f"input: {fasta_text.count('>')} sequence(s)")

    model = params.get("model", "AUTO").strip() or "AUTO"
    # AUTO was never a real IQ-TREE flag — it tries to read a file called
    # "AUTO". MFP (ModelFinder Plus) is the actual auto-selection mode, and
    # it's also what IQ-TREE uses if -m is omitted entirely.
    if model.upper() == "AUTO":
        model = "MFP"

    bootstrap = int(params.get("bootstrap", 1000))
    seed = int(params.get("seed", 0)) or None
    outgroup = (params.get("outgroup") or "").strip()

    # IQ-TREE 2 uses different flags than v1; we target v2 syntax.
    cmd = [
        "iqtree2",
        "-s", in_path,
        "-m", model,
        "--prefix", os.path.join(work_dir, "out"),
        "-T", "AUTO",
    ]
    if bootstrap > 0:
        cmd.extend(["-B", str(bootstrap)])
    if seed:
        cmd.extend(["--seed", str(seed)])
    if outgroup:
        # -o accepts one or more taxon names separated by commas. The tree
        # remains an ML tree but is drawn rooted on these taxa, which fixes
        # display-only weirdness when the unrooted tree happens to render
        # with an in-group taxon at the root.
        cmd.extend(["-o", outgroup])

    extra = params.get("extra_args", "").strip()
    if extra:
        cmd.extend(shlex.split(extra))

    try:
        # IQ-TREE prints its progress to STDOUT (not stderr), so stream_subprocess
        # would forward stderr only. Tee both by reading stdout too: simplest is
        # to keep stream_subprocess (stderr live) and additionally surface the
        # IQ-TREE .log tail at the end. For live feedback we run with stdout going
        # to the log via a small wrapper: IQ-TREE supports -redo and writes a .log
        # file we tail. To keep it live, we forward stdout lines through stderr by
        # not separating them — but stream_subprocess already captures stdout. So
        # here we log a heartbeat before/after and the full .log tail after.
        _emit("running: " + " ".join(cmd))
        try:
            stdout_text, _stderr = stream_subprocess(cmd, _emit, timeout=3600)
        except FileNotFoundError:
            cmd[0] = "iqtree"
            _emit("iqtree2 not found; trying iqtree")
            stdout_text, _stderr = stream_subprocess(cmd, _emit, timeout=3600)

        # IQ-TREE's real progress is in its .log file; surface the tail so the
        # console shows model selection / bootstrap progress even though most of
        # it went to stdout.
        prefix = os.path.join(work_dir, "out")
        tree_path = prefix + ".treefile"
        report_path = prefix + ".iqtree"
        log_path = prefix + ".log"

        if os.path.exists(log_path):
            for line in Path(log_path).read_text().splitlines()[-40:]:
                if line.strip():
                    _emit(line)

        newick = Path(tree_path).read_text() if os.path.exists(tree_path) else ""
        report = Path(report_path).read_text() if os.path.exists(report_path) else ""
        log_text = Path(log_path).read_text()[-4000:] if os.path.exists(log_path) else ""

        if not newick:
            raise RuntimeError("iqtree produced no tree file")
        _emit("tree inference complete")

        return {
            "newick": newick,
            "report": report,
            "log_tail": log_text,
            "model": model,
            "cmd": " ".join(cmd),
            "output_files": [
                {"name": f"{label}.newick", "content": newick, "kind": "newick", "size": len(newick)},
                {"name": f"{label}.report.txt", "content": report, "kind": "text", "size": len(report)},
            ] if newick else [],
        }
    finally:
        try:
            for f in os.listdir(work_dir):
                os.unlink(os.path.join(work_dir, f))
            os.rmdir(work_dir)
        except Exception:
            pass

async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str, log=None) -> dict[str, Any]:
    from app.tools._inputs import get_input_text, get_input_metadata, build_output_label
    fasta_text = get_input_text(inputs, project_id)
    metadata = get_input_metadata(inputs, project_id)
    # Build a label like "iqtree_TP53_3species" — extension is added per output.
    label = (
        build_output_label(metadata, "iqtree", "").rstrip(".")
        if metadata else "iqtree_tree"
    )
    return await asyncio.to_thread(_run_iqtree, fasta_text, params, label, log)

register(ToolDef(
    id="iqtree",
    label="IQ-TREE (phylogeny)",
    description="Maximum-likelihood phylogenetic tree from an alignment. Auto model selection by default.",
    input_kind="aligned_fasta",
    params=[
        ParamDef(name="model", type="string", label="Substitution model",
                 default="AUTO",
                 help="AUTO = ModelFinder Plus picks the best model. Or specify (GTR+G, JTT+G, etc.)."),
        ParamDef(name="bootstrap", type="int", label="UFBoot replicates",
                 default=1000, min=0, max=10000,
                 help="Ultrafast bootstrap (-B). 0 disables. 1000 is standard."),
        ParamDef(name="seed", type="int", label="Random seed",
                 default=0, min=0, max=2**31 - 1,
                 help="0 = random. Set for reproducibility."),
        ParamDef(name="outgroup", type="string", label="Outgroup taxon",
                 default="",
                 help="Taxon name (must match a FASTA header exactly) to root "
                      "the tree on for display. Comma-separate multiple taxa. "
                      "Doesn't change the ML inference, only the rooting. "
                      "Example: Homo_sapiens_TP53_9606_protein"),
        ParamDef(name="extra_args", type="string", label="Extra CLI args",
                 default="",
                 help="Appended to iqtree2 command. Example: -alrt 1000"),
    ],
    run=run,
))
