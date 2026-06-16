"""Trimming via cutadapt."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register


def _run_cutadapt(input_path: str, params: dict[str, Any]) -> dict[str, Any]:
    out_dir = tempfile.mkdtemp(prefix="cutadapt_")
    out_path = os.path.join(out_dir, "trimmed.fastq")
    json_path = os.path.join(out_dir, "stats.json")

    cmd = [
        "cutadapt",
        "-q", str(params.get("quality_cutoff", 20)),
        "-m", str(params.get("min_length", 30)),
        "--json", json_path,
        "-o", out_path,
    ]

    a3 = params.get("adapter_3prime", "").strip()
    a5 = params.get("adapter_5prime", "").strip()
    if a3:
        cmd.extend(["-a", a3])
    if a5:
        cmd.extend(["-g", a5])

    extra = params.get("extra_args", "").strip()
    if extra:
        cmd.extend(shlex.split(extra))

    cmd.append(input_path)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(f"cutadapt failed: {result.stderr}")

        out_text = ""
        if os.path.exists(out_path):
            out_text = Path(out_path).read_text()

        stats: dict[str, Any] = {}
        if os.path.exists(json_path):
            try:
                stats = json.loads(Path(json_path).read_text())
            except Exception:
                pass

        suggested_name = "trimmed.fastq"
        if "filename" in params:
            base = os.path.basename(params["filename"]).replace(".gz", "")
            suggested_name = f"{Path(base).stem}.trimmed.fastq"

        return {
            "stats": stats,
            "cmd": " ".join(cmd),
            "stdout": result.stdout[:4000],
            "output_files": [
                {
                    "name": suggested_name,
                    "content": out_text,
                    "kind": "fastq",
                    "size": len(out_text),
                }
            ] if out_text else [],
        }
    finally:
        try:
            for f in os.listdir(out_dir):
                os.unlink(os.path.join(out_dir, f))
            os.rmdir(out_dir)
        except Exception:
            pass


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    upload_path = inputs.get("upload_path")
    if not upload_path or not os.path.exists(upload_path):
        raise ValueError(f"upload not found at {upload_path}")

    # Pass filename through to params for output naming.
    params = {**params, "filename": inputs.get("filename", "input")}
    try:
        return await asyncio.to_thread(_run_cutadapt, upload_path, params)
    finally:
        try:
            os.unlink(upload_path)
        except Exception:
            pass


register(ToolDef(
    id="cutadapt",
    label="Cutadapt (trimming)",
    description="Quality + adapter trimming for sequencing reads.",
    input_kind="fastq_upload",
    params=[
        ParamDef(name="quality_cutoff", type="int", label="Quality cutoff (Phred)",
                 default=20, min=0, max=50,
                 help="Trim 3' end where quality falls below this score."),
        ParamDef(name="min_length", type="int", label="Minimum length",
                 default=30, min=1, max=1000,
                 help="Discard reads shorter than this after trimming."),
        ParamDef(name="adapter_3prime", type="string", label="3' adapter (optional)",
                 default="", help="Sequence to trim from 3' end."),
        ParamDef(name="adapter_5prime", type="string", label="5' adapter (optional)",
                 default="", help="Sequence to trim from 5' end."),
        ParamDef(name="extra_args", type="string", label="Extra CLI args",
                 default="",
                 help="Appended to the cutadapt command line as-is. Power user only. Example: --max-n 0.1"),
    ],
    run=run,
))
