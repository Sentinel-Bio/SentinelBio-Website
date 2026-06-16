"""Postgres-polled worker for the `jobs` table (admin / taxon jobs).

Run in its own process when job_backend="postgres" (the default):
    uv run python -m app.job_worker

This is the Redis-free counterpart to app.worker (the arq worker). It reuses the
exact same job function bodies (via app.jobs.JOB_FUNCTIONS) — the only thing that
differs is delivery: instead of arq pulling a task off Redis, this loop claims a
`jobs` row whose status is "queued" and calls the matching function.

Why a separate worker from tool_worker.py? Different table (`jobs` vs
`tool_runs`), different concurrency needs (taxon fetches are long + NCBI-rate-
limited, so we run them one at a time), and different arg-reconstruction (these
jobs were designed for arq's positional enqueue, so we rebuild positional args
from the stored `params`).

The job functions expect an arq-style `ctx` dict as their first argument; we pass
an empty dict since none of them use it. They also self-manage their own status
transitions (running → done/failed) and progress via `_update_job`, so this loop
only has to claim and invoke.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from app.jobs import JOB_FUNCTIONS
from app.supabase_client import service_client


POLL_INTERVAL = 2.0
# Taxon fetches hammer NCBI and are internally rate-limited (1.5s/species). Two
# running at once would double the NCBI request rate, so keep this at 1 unless
# you have an NCBI API key and have raised the per-job pacing accordingly.
MAX_CONCURRENT = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── params → positional args reconstruction ─────────────────────────
#
# Each job was written for arq's positional enqueue. In redis mode the route
# calls e.g. enqueue("fetch_taxon_recursive", job_id, root_taxid, stop_rank,
# max_species). In postgres mode there are no positional args on the wire — only
# the `jobs` row, whose `params` jsonb holds the same values by name. These
# builders turn that row back into the positional tail the function expects
# (everything after `ctx`).

def _args_fetch_taxon_recursive(job: dict[str, Any]) -> list[Any]:
    p = job.get("params") or {}
    return [job["id"], int(p["root_taxid"]), str(p["stop_rank"]), int(p["max_species"])]


def _args_resync_species(job: dict[str, Any]) -> list[Any]:
    p = job.get("params") or {}
    return [job["id"], int(p["taxid"])]


def _args_resync_all_species(job: dict[str, Any]) -> list[Any]:
    return [job["id"]]


def _args_backfill_lineages(job: dict[str, Any]) -> list[Any]:
    return [job["id"]]


def _args_run_fastqc_job(job: dict[str, Any]) -> list[Any]:
    p = job.get("params") or {}
    return [job["id"], str(p["fastq_b64"]), str(p["filename"])]


ARG_BUILDERS: dict[str, Callable[[dict[str, Any]], list[Any]]] = {
    "fetch_taxon_recursive": _args_fetch_taxon_recursive,
    "resync_species": _args_resync_species,
    "resync_all_species": _args_resync_all_species,
    "backfill_lineages": _args_backfill_lineages,
    "run_fastqc_job": _args_run_fastqc_job,
}


def _claim_job() -> dict[str, Any] | None:
    """Atomically claim one queued job via optimistic concurrency on the UPDATE.

    NOTE: the job functions themselves set status="running" + started_at as their
    first action. We set status="running" here too as the claim marker so a
    second worker can't grab the same row. The function's own update is then
    idempotent (re-sets running + started_at, harmless).
    """
    client = service_client()
    queued = (
        client.table("jobs")
        .select("*")
        .eq("status", "queued")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    if not queued.data:
        return None
    job = queued.data[0]
    claimed = (
        client.table("jobs")
        .update({"status": "running", "started_at": _now(), "updated_at": _now()})
        .eq("id", job["id"])
        .eq("status", "queued")  # optimistic concurrency check
        .execute()
    )
    if not claimed.data:
        return None  # someone else claimed it first
    return claimed.data[0]


async def run_one_job(job: dict[str, Any]) -> None:
    kind = job.get("kind")
    fn = JOB_FUNCTIONS.get(kind)
    builder = ARG_BUILDERS.get(kind)
    if not fn or not builder:
        # Unknown kind — mark failed so it doesn't sit "running" forever.
        service_client().table("jobs").update({
            "status": "failed",
            "error": f"unknown job kind: {kind}",
            "finished_at": _now(),
            "updated_at": _now(),
        }).eq("id", job["id"]).execute()
        return

    print(f"[job_worker] start {kind} (job {job['id']})")
    try:
        args = builder(job)
        # ctx is unused by these functions; pass an empty dict.
        await fn({}, *args)
        print(f"[job_worker] done  {job['id']}")
    except Exception as e:
        # The functions already set status=failed + error on their own except
        # blocks before re-raising, so we just log here. Still, defensively
        # ensure the row isn't left "running" if something escaped.
        import traceback
        print(f"[job_worker] FAIL  {job['id']}: {e}")
        try:
            client = service_client()
            cur = client.table("jobs").select("status").eq("id", job["id"]).maybe_single().execute()
            if cur and cur.data and cur.data.get("status") == "running":
                client.table("jobs").update({
                    "status": "failed",
                    "error": f"{e}\n{traceback.format_exc()}"[:5000],
                    "finished_at": _now(),
                    "updated_at": _now(),
                }).eq("id", job["id"]).execute()
        except Exception:
            pass


async def main_loop() -> None:
    print(f"[job_worker] started; poll={POLL_INTERVAL}s, max_concurrent={MAX_CONCURRENT}")
    running: set[asyncio.Task] = set()

    while True:
        try:
            for task in list(running):
                if task.done():
                    running.discard(task)
                    try:
                        task.result()
                    except Exception as e:
                        print(f"[job_worker] task exception escaped: {e}")

            claimed_this_round = 0
            while len(running) < MAX_CONCURRENT:
                job = _claim_job()
                if not job:
                    break
                running.add(asyncio.create_task(run_one_job(job)))
                claimed_this_round += 1

            await asyncio.sleep(POLL_INTERVAL if claimed_this_round == 0 else 0.1)
        except KeyboardInterrupt:
            print("[job_worker] stopping")
            if running:
                print(f"[job_worker] waiting on {len(running)} running jobs…")
                await asyncio.gather(*running, return_exceptions=True)
            break
        except Exception as e:
            print(f"[job_worker] loop error: {e}")
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
