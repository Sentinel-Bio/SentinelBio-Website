"""Postgres-polled worker for tool_runs. No Redis required.

Runs in its own process:
    uv run python -m app.tool_worker

Concurrency model:
- Loop polls every POLL_INTERVAL seconds for queued jobs.
- Up to MAX_CONCURRENT jobs run in parallel.
- Each tool's `run()` is invoked from `asyncio.create_task` so the loop can
  claim more jobs while existing ones are still running.
- Tools that do heavy CPU/IO work should wrap that work in `asyncio.to_thread`
  internally so they don't block the event loop. (MAFFT, IQ-TREE, etc. already
  do this; new tools should follow the same pattern.)

Memory safety: MAX_CONCURRENT defaults to 2 for a 16 GB workstation. Bump
this on a beefier machine, but keep in mind that MAFFT/IQ-TREE on gene-scale
input use ~50-200 MB each, while a 2 GB genome fetch streams to disk
(constant ~10 MB).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.supabase_client import service_client
from app.tools.registry import get_tool


POLL_INTERVAL = 2.0          # seconds between claim attempts
MAX_CONCURRENT = 2           # tool jobs running in parallel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim_job() -> dict[str, Any] | None:
    """Atomically claim one queued job. Optimistic concurrency via
    `eq("status", "queued")` on the UPDATE."""
    client = service_client()
    queued = (
        client.table("tool_runs")
        .select("*")
        .eq("status", "queued")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    if not queued.data:
        return None
    job = queued.data[0]
    result = (
        client.table("tool_runs")
        .update({
            "status": "running",
            "started_at": _now(),
            "updated_at": _now(),
            "progress": 5,
            "logs": "",   # fresh console for this run (matters on retry/re-run)
            "error": None,
        })
        .eq("id", job["id"])
        .eq("status", "queued")  # optimistic concurrency check
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


def _update_progress(job_id: str, progress: int) -> None:
    client = service_client()
    client.table("tool_runs").update({
        "progress": progress,
        "updated_at": _now(),
    }).eq("id", job_id).execute()


def _finish_job(job: dict, result: dict) -> None:
    """Persist tool outputs to server storage, create project_files rows,
    then mark the run done."""
    client = service_client()

    output_files = result.pop("output_files", []) or []
    saved_file_ids: list[str] = []

    if output_files:
        from app.files import store as filestore
        project_id = job["project_id"]
        run_id = job["id"]
        species_id = (job.get("inputs") or {}).get("species_id")

        for f in output_files:
            content = f.get("content")
            name = f.get("name", "output")
            kind = f.get("kind", "other")
            if not content:
                continue
            mime_hint_map = {
                "fastq": "fastq",
                "fasta": "fasta",
                "aligned_fasta": "fasta",
                "newick": "newick",
                "pdb": "pdb",
                "cif": "cif",
                "text": "other",
                "gff": "annotation",
                "gff3": "annotation",
            }
            mime_hint = mime_hint_map.get(kind, "other")

            content_bytes = content.encode("utf-8") if isinstance(content, str) else content
            meta = filestore.write_bytes(
                project_id, subdir=f"tools/{run_id}", filename=name, content=content_bytes,
            )

            # Per-file overrides — tools producing outputs for multiple species
            # tag each file with its own species_id and richer source_metadata.
            file_species_id = f.get("species_id", species_id)
            base_metadata = {
                "tool": job["tool"], "run_id": run_id, "kind": kind,
            }
            file_metadata = f.get("source_metadata") or {}
            merged_metadata = {**base_metadata, **file_metadata}

            row = {
                "project_id": project_id,
                "owner_id": job["owner_id"],
                "name": meta["name"],
                "storage_path": meta["storage_path"],
                "size": meta["size"],
                "sha256": meta["sha256"],
                "mime_hint": mime_hint,
                "source": "tool",
                "source_metadata": merged_metadata,
                "tool_run_id": run_id,
                "species_id": file_species_id,
                "created_at": _now(),
            }
            inserted = client.table("project_files").insert(row).execute()
            if inserted.data:
                saved_file_ids.append(inserted.data[0]["id"])

    result["output_file_ids"] = saved_file_ids

    client.table("tool_runs").update({
        "status": "done",
        "progress": 100,
        "result": result,
        "finished_at": _now(),
        "updated_at": _now(),
    }).eq("id", job["id"]).execute()


def _fail_job(job: dict, error: str) -> None:
    """Mark a job failed. Takes the full job dict (not just an id)."""
    client = service_client()
    client.table("tool_runs").update({
        "status": "failed",
        "error": error[:5000],  # truncate huge tracebacks
        "finished_at": _now(),
        "updated_at": _now(),
    }).eq("id", job["id"]).execute()


async def run_one_job(job: dict[str, Any]) -> None:
    tool = get_tool(job["tool"])
    if not tool:
        _fail_job(job, f"unknown tool: {job['tool']}")
        return

    from app.job_log import JobLogger
    import inspect

    logger = JobLogger(job["id"])
    logger.log(f"=== {job['tool']} starting (run {job['id']}) ===")
    print(f"[tool_worker] start {job['tool']} (run {job['id']})")
    try:
        inputs = job.get("inputs") or {}
        params = job.get("params") or {}

        # Backward-compatible log injection: only pass `log` to tools whose run()
        # accepts it (by keyword), so existing 3-arg tools keep working unchanged.
        run_kwargs: dict[str, Any] = {}
        try:
            sig = inspect.signature(tool.run)
            if "log" in sig.parameters:
                run_kwargs["log"] = logger
        except (TypeError, ValueError):
            pass

        result = await tool.run(inputs, params, job["project_id"], **run_kwargs)
        logger.log("tool finished; saving outputs")
        _finish_job(job, result)
        logger.log("=== done ===")
        logger.close()
        print(f"[tool_worker] done  {job['id']}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.log(f"!!! FAILED: {e}")
        logger.log(tb)
        logger.close()
        _fail_job(job, f"{e}\n{tb}")
        print(f"[tool_worker] FAIL  {job['id']}: {e}")


async def main_loop() -> None:
    print(f"[tool_worker] started; poll={POLL_INTERVAL}s, max_concurrent={MAX_CONCURRENT}")

    # Track running tasks so we know how many slots are free.
    running: set[asyncio.Task] = set()

    while True:
        try:
            # Clean up finished tasks.
            for task in list(running):
                if task.done():
                    running.discard(task)
                    # Surface any uncaught exception (defensive — run_one_job
                    # should catch its own).
                    try:
                        task.result()
                    except Exception as e:
                        print(f"[tool_worker] task exception escaped: {e}")

            # Claim as many jobs as we have slots for.
            claimed_this_round = 0
            while len(running) < MAX_CONCURRENT:
                job = _claim_job()
                if not job:
                    break
                task = asyncio.create_task(run_one_job(job))
                running.add(task)
                claimed_this_round += 1

            # If we didn't claim anything AND nothing is running, wait full poll.
            # If we did claim something, loop again immediately to claim more if available.
            if claimed_this_round == 0:
                await asyncio.sleep(POLL_INTERVAL)
            else:
                await asyncio.sleep(0.1)  # quick yield, then re-check
        except KeyboardInterrupt:
            print("[tool_worker] stopping")
            # Let running tasks finish.
            if running:
                print(f"[tool_worker] waiting on {len(running)} running jobs…")
                await asyncio.gather(*running, return_exceptions=True)
            break
        except Exception as e:
            print(f"[tool_worker] loop error: {e}")
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
