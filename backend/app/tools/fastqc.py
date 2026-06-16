"""FastQC tool — reads from upload_path on disk."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from app.sources.fastqc import run_fastqc
from app.tools.registry import ToolDef, ParamDef, register

async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    upload_path = inputs.get("upload_path")
    filename = inputs.get("filename", "input.fastq")
    if not upload_path or not os.path.exists(upload_path):
        raise ValueError(f"upload not found at {upload_path}")
    try:
        fastq_bytes = Path(upload_path).read_bytes()
        result = await asyncio.to_thread(
            run_fastqc, fastq_bytes, filename, params.get("extra_args", "")
        )
        result["phred_max"] = int(params.get("phred_max", 42))
        return result
    finally:
        try:
            os.unlink(upload_path)
        except Exception:
            pass

register(ToolDef(
    id="fastqc",
    label="FastQC",
    description="Quality control for high-throughput sequence data. Pass / warn / fail per QC module.",
    input_kind="fastq_upload",
    params=[
        ParamDef(
            name="phred_max",
            type="int",
            label="Phred score axis max",
            default=42,
            min=20,
            max=93,
            help="Y-axis max for the per-base quality plot. Use 60+ for long reads (PacBio/Nanopore).",
        ),
        ParamDef(
            name="extra_args",
            type="string",
            label="Extra CLI args",
            default="",
            help="Appended to fastqc command line. Example: --kmers 7",
        ),
    ],
    run=run,
))
