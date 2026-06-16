"""MAFFT multiple sequence alignment."""
from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register
from app.tools._subprocess import stream_subprocess as _stream_subprocess


STRATEGIES = {
    "auto": ["--auto"],
    "fft-ns-2": ["--retree", "2"],
    "fft-ns-i": ["--retree", "2", "--maxiterate", "1000"],
    "l-ins-i": ["--localpair", "--maxiterate", "1000"],
    "g-ins-i": ["--globalpair", "--maxiterate", "1000"],
    "e-ins-i": ["--genafpair", "--maxiterate", "1000"],
}


def _run_mafft(fasta_text: str, params: dict[str, Any], output_name: str, log=None) -> dict[str, Any]:
    def _emit(msg: str) -> None:
        if log:
            log(msg)

    if not fasta_text.strip():
        raise ValueError("empty input fasta")

    n_in = fasta_text.count(">")
    _emit(f"input: {n_in} sequence(s), {len(fasta_text)} bytes")

    in_fd, in_path = tempfile.mkstemp(prefix="mafft_in_", suffix=".fasta")
    try:
        with os.fdopen(in_fd, "w") as f:
            f.write(fasta_text)

        strategy = params.get("strategy", "auto")
        strategy_args = STRATEGIES.get(strategy, ["--auto"])
        op = float(params.get("gap_open_penalty", 1.53))
        ep = float(params.get("gap_extension_penalty", 0.0))

        cmd = [
            "mafft",
            *strategy_args,
            "--op", str(op),
            "--ep", str(ep),
        ]
        extra = params.get("extra_args", "").strip()
        if extra:
            cmd.extend(shlex.split(extra))
        cmd.append(in_path)

        _emit(f"strategy={strategy}  op={op}  ep={ep}")
        _emit("running: " + " ".join(cmd))

        # Stream stderr live (MAFFT prints progress there) while capturing stdout
        # (the alignment) to a buffer. subprocess.run would block until exit and
        # hide all of MAFFT's progress chatter — which is exactly the "frozen at
        # 5%" experience. Popen + line reads give a live console.
        aligned_text, stderr_tail = _stream_subprocess(cmd, _emit, timeout=1800)

        if not aligned_text.strip():
            raise RuntimeError("mafft produced empty output")

        n_seqs = aligned_text.count(">")
        first_len = 0
        seen_header = False
        for line in aligned_text.splitlines():
            if line.startswith(">"):
                if seen_header:
                    break
                seen_header = True
            elif seen_header:
                first_len += len(line.strip())

        _emit(f"aligned {n_seqs} sequences, length {first_len}")

        return {
            "num_sequences": n_seqs,
            "alignment_length": first_len,
            "strategy": strategy,
            "cmd": " ".join(cmd),
            "stderr_tail": stderr_tail[-1000:] if stderr_tail else "",
            "output_files": [
                {
                    "name": output_name,
                    "content": aligned_text,
                    "kind": "aligned_fasta",
                    "size": len(aligned_text),
                }
            ],
        }
    finally:
        try:
            os.unlink(in_path)
        except Exception:
            pass


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str, log=None) -> dict[str, Any]:
    from app.tools._inputs import get_input_text, get_input_metadata, build_output_label
    if log:
        log("resolving input FASTA…")
    fasta_text = get_input_text(inputs, project_id)
    metadata = get_input_metadata(inputs, project_id)
    output_name = build_output_label(metadata, "mafft", "fasta") if metadata else "alignment.fasta"
    return await asyncio.to_thread(_run_mafft, fasta_text, params, output_name, log)

register(ToolDef(
    id="mafft",
    label="MAFFT (alignment)",
    description="Multiple sequence alignment.",
    inputs={"fasta_files": "many", "species": "optional"},
    input_kind="multi_fasta",
    params=[
        ParamDef(name="strategy", type="enum", label="Strategy",
                 default="auto", options=list(STRATEGIES.keys()),
                 help="auto picks for you. l-ins-i is most accurate for <200 seqs."),
        ParamDef(name="gap_open_penalty", type="float", label="Gap open penalty",
                 default=1.53, min=0.0, max=10.0, help="MAFFT --op."),
        ParamDef(name="gap_extension_penalty", type="float", label="Gap extension penalty",
                 default=0.0, min=0.0, max=10.0, help="MAFFT --ep."),
        ParamDef(name="extra_args", type="string", label="Extra CLI args",
                 default="", help="Appended to mafft command line. Example: --thread 4"),
    ],
    run=run,
))
