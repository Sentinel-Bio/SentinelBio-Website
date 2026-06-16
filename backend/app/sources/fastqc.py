"""FastQC integration — runs FastQC on a FASTQ file, parses results."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

def run_fastqc(fastq_bytes: bytes, filename: str, extra_args: str = "") -> dict[str, Any]:
    """Run FastQC on FASTQ bytes."""
    import shlex
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        in_path = tmp / filename
        in_path.write_bytes(fastq_bytes)
        out_dir = tmp / "out"
        out_dir.mkdir()

        cmd = ["fastqc", str(in_path), "--outdir", str(out_dir), "--quiet", "--extract"]
        if extra_args.strip():
            cmd.extend(shlex.split(extra_args.strip()))

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"fastqc failed: {result.stderr}")

        # FastQC creates <stem>_fastqc/ directory inside outdir.
        stem = in_path.stem
        if filename.endswith((".gz", ".bz2")):
            stem = Path(filename).with_suffix("").stem
        report_dir = out_dir / f"{stem}_fastqc"
        if not report_dir.exists():
            # Find any *_fastqc directory produced.
            candidates = list(out_dir.glob("*_fastqc"))
            if not candidates:
                raise RuntimeError("fastqc produced no output directory")
            report_dir = candidates[0]

        data_file = report_dir / "fastqc_data.txt"
        summary_file = report_dir / "summary.txt"
        html_file = report_dir / "fastqc_report.html"

        return {
            "summary": _parse_summary(summary_file.read_text()),
            "stats": _parse_basic_stats(data_file.read_text()),
            "modules": _parse_modules(data_file.read_text()),
            "report_html": html_file.read_text() if html_file.exists() else "",
            "raw_data": data_file.read_text() if data_file.exists() else "",
        }


def _parse_summary(text: str) -> list[dict[str, str]]:
    """summary.txt is tab-separated: PASS/WARN/FAIL\tmodule_name\tfilename"""
    rows = []
    for line in text.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append({"status": parts[0], "module": parts[1]})
    return rows


def _parse_basic_stats(text: str) -> dict[str, Any]:
    """Extract the 'Basic Statistics' block from fastqc_data.txt."""
    in_block = False
    stats: dict[str, Any] = {}
    for line in text.splitlines():
        if line.startswith(">>Basic Statistics"):
            in_block = True
            continue
        if in_block and line.startswith(">>END_MODULE"):
            break
        if in_block and not line.startswith("#") and "\t" in line:
            k, v = line.split("\t", 1)
            stats[k] = v
    return stats


def _parse_modules(text: str) -> dict[str, dict[str, Any]]:
    """Extract per-module status + first data row count for visualization."""
    modules: dict[str, dict[str, Any]] = {}
    current: str | None = None
    current_data: list[list[str]] = []
    current_status: str = ""
    current_headers: list[str] = []

    for line in text.splitlines():
        if line.startswith(">>") and not line.startswith(">>END_MODULE"):
            # New module: ">>Module Name\tstatus"
            parts = line[2:].split("\t")
            current = parts[0]
            current_status = parts[1] if len(parts) > 1 else ""
            current_data = []
            current_headers = []
        elif line.startswith(">>END_MODULE"):
            if current:
                modules[current] = {
                    "status": current_status,
                    "headers": current_headers,
                    "rows": current_data[:200],  # cap, just for display
                }
            current = None
        elif current and line.startswith("#"):
            current_headers = line[1:].split("\t")
        elif current and "\t" in line:
            current_data.append(line.split("\t"))
    return modules
