"""Job enqueue dispatch — backend-agnostic.

The admin/taxon jobs (the `jobs` table) can be delivered two ways, chosen by
`settings.job_backend`:

  - "postgres" (default): the job row is already inserted by the route. A
    Postgres-polling worker (`app.job_worker`) claims `status="queued"` rows and
    runs the matching function. No Redis needed — same model as `tool_worker`.

  - "redis": enqueue into arq/Redis; `app.worker` (the arq worker) runs it.

This module is the single place routes call to enqueue, so switching backends is
one setting flip and the routes never import arq directly.

The function-name → callable map (`JOB_FUNCTIONS`) is shared by both the arq
worker and the Postgres worker, so the 5 job bodies in `app.worker` are never
duplicated.
"""
from __future__ import annotations

from typing import Any

from app.config import get_settings

# arq enqueues by string name; the Postgres worker dispatches by the same name.
# Keep these in sync with app.worker.WorkerSettings.functions.
from app import worker as _worker

JOB_FUNCTIONS = {
    "fetch_taxon_recursive": _worker.fetch_taxon_recursive,
    "resync_species": _worker.resync_species,
    "resync_all_species": _worker.resync_all_species,
    "backfill_lineages": _worker.backfill_lineages,
    "run_fastqc_job": _worker.run_fastqc_job,
}


async def enqueue(job_name: str, *args: Any) -> None:
    """Enqueue a job for background execution.

    In postgres mode this is a no-op: the route has already inserted the `jobs`
    row with status="queued" and stored its params, which is everything the
    Postgres worker needs to claim and run it. We still validate the name here so
    a typo fails loudly at enqueue time rather than silently never running.

    In redis mode this pushes onto the arq queue. The positional `args` mirror
    the job function's signature after `ctx` (i.e. job_id, then job-specific
    args), exactly as the routes already pass them.
    """
    if job_name not in JOB_FUNCTIONS:
        raise ValueError(f"unknown job: {job_name}")

    backend = get_settings().job_backend.lower()

    if backend == "redis":
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        try:
            await pool.enqueue_job(job_name, *args)
        finally:
            await pool.close()
        return

    # postgres mode: nothing to push — the inserted jobs row IS the queue entry.
    # app.job_worker will claim it by job_name + params.
    return
