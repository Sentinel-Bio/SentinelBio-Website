"""Per-run log capture for tool jobs.

Tools receive a `log` callable (see the updated run() contract) that appends a
timestamped line to an in-memory buffer. The buffer is flushed to the
`tool_runs.logs` text column debounced — at most once per FLUSH_INTERVAL seconds,
or immediately when FLUSH_LINES new lines have accumulated — so a long-running
tool's console updates without one DB write per line.

The frontend polls `tool_runs` (it already does, while a run is queued/running)
and renders `logs` as a live console. No websockets/SSE: ~1-2s lag is fine for a
single-user self-hosted workspace.

Thread-safety: tools do CPU/subprocess work inside `asyncio.to_thread`, so
`log()` can be called from a worker thread. The buffer is guarded by a lock and
DB flushes go through a plain (sync) supabase call, which is safe from any
thread.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from app.supabase_client import service_client


FLUSH_INTERVAL = 1.0   # seconds: max staleness of the logs column while running
FLUSH_LINES = 25       # flush early once this many unflushed lines accrue
MAX_LOG_CHARS = 200_000  # cap stored logs; keep the tail if a tool is very chatty


class JobLogger:
    """Collects log lines for one tool_run and flushes them to the DB.

    Usage (in the worker):
        logger = JobLogger(run_id)
        logger.log("starting")
        ...
        logger.flush(final=True)   # force a last write
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._unflushed = 0
        self._last_flush = 0.0
        self._closed = False

    # ── line capture ────────────────────────────────────────────────
    def log(self, message: str) -> None:
        """Append one line (a trailing newline is added). Multi-line messages
        are split so each physical line gets its own timestamp."""
        if self._closed:
            return
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with self._lock:
            for raw in str(message).rstrip("\n").split("\n"):
                self._lines.append(f"[{ts}] {raw}")
                self._unflushed += 1
        # Debounced flush decision outside the lock-heavy path.
        now = time.time()
        if self._unflushed >= FLUSH_LINES or (now - self._last_flush) >= FLUSH_INTERVAL:
            self.flush()

    def __call__(self, message: str) -> None:
        # So a JobLogger instance can be passed directly as the `log` callable.
        self.log(message)

    def progress(self, pct: int, message: str | None = None) -> None:
        """Update the run's progress bar (0-100), optionally with a log line.
        Writes progress immediately (it's cheap and the bar is what users stare
        at); the log line goes through the normal debounced path."""
        pct = max(0, min(100, int(pct)))
        if message:
            self.log(f"[{pct}%] {message}")
        try:
            service_client().table("tool_runs").update(
                {"progress": pct, "updated_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", self.run_id).execute()
        except Exception as e:
            print(f"[joblog] progress update failed for {self.run_id}: {e}")

    # ── persistence ─────────────────────────────────────────────────
    def _rendered(self) -> str:
        text = "\n".join(self._lines)
        if len(text) > MAX_LOG_CHARS:
            # Keep the tail — the end of a log is where failures live.
            text = "…(truncated)…\n" + text[-MAX_LOG_CHARS:]
        return text

    def flush(self, final: bool = False) -> None:
        with self._lock:
            if self._unflushed == 0 and not final:
                return
            payload = self._rendered()
            self._unflushed = 0
            self._last_flush = time.time()
        try:
            service_client().table("tool_runs").update(
                {"logs": payload, "updated_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", self.run_id).execute()
        except Exception as e:  # never let logging kill a job
            print(f"[joblog] flush failed for {self.run_id}: {e}")

    def close(self) -> None:
        self.flush(final=True)
        self._closed = True
