"""Shared subprocess runner that streams output to a tool's log callback.

`subprocess.run(..., capture_output=True)` buffers everything until the process
exits, so a long tool (MAFFT, IQ-TREE, BLAST) shows nothing until it's done —
the "frozen at 5%" problem. `stream_subprocess` uses Popen and reads stderr
line-by-line, forwarding each line to the `log` callback as it arrives, while
collecting stdout (the actual result, e.g. an alignment) into a buffer.

Most CLI bioinformatics tools write progress/diagnostics to stderr and their
real output to stdout, which is exactly this split.
"""
from __future__ import annotations

import subprocess
import threading
from typing import Callable


def stream_subprocess(
    cmd: list[str],
    log: Callable[[str], None] | None = None,
    timeout: int = 1800,
    cwd: str | None = None,
) -> tuple[str, str]:
    """Run `cmd`, streaming stderr lines to `log` as they arrive.

    Returns (stdout_text, stderr_tail). Raises RuntimeError on nonzero exit
    (with the stderr tail) or TimeoutError-equivalent RuntimeError on timeout.
    """
    def _emit(msg: str) -> None:
        if log:
            log(msg)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered
        cwd=cwd,
    )

    stdout_chunks: list[str] = []
    stderr_lines: list[str] = []

    def _drain_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_chunks.append(line)

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            s = line.rstrip("\n")
            stderr_lines.append(s)
            if s.strip():
                _emit(s)

    t_out = threading.Thread(target=_drain_stdout, daemon=True)
    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        _emit(f"!!! timed out after {timeout}s; killed")
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s")

    # Make sure the reader threads have consumed everything.
    t_out.join(timeout=5)
    t_err.join(timeout=5)

    stdout_text = "".join(stdout_chunks)
    stderr_tail = "\n".join(stderr_lines)

    if proc.returncode != 0:
        _emit(f"!!! {cmd[0]} exited with code {proc.returncode}")
        raise RuntimeError(
            f"{cmd[0]} failed (exit {proc.returncode}): {stderr_tail[-1500:]}"
        )

    return stdout_text, stderr_tail
