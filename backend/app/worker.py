"""arq worker — runs background jobs backed by Redis.

Run in a separate terminal:
    uv run arq app.worker.WorkerSettings

Jobs defined here:
  - fetch_taxon_recursive: walk NCBI subtree, upsert species
  - resync_species: re-fetch a single species by TaxID
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.sources import ncbi
from app.sources.enrich import build_species_row
from app.supabase_client import service_client


def _redis_settings():
    # Imported lazily: arq is only needed when actually running the arq worker
    # or enqueueing in redis mode. Postgres mode never touches arq.
    from arq.connections import RedisSettings
    return RedisSettings.from_dsn(get_settings().redis_url)


# ─── Job helpers ─────────────────────────────────────────────────────
def _invalidate_tree_cache() -> None:
    """Clear tree layout cache after species data changes."""
    try:
        service_client().table("tree_layouts").delete().neq("cache_key", "").execute()
    except Exception:
        pass

def _is_cancelled(job_id: str) -> bool:
    """Check if the job has been marked cancelled from outside."""
    try:
        result = (
            service_client()
            .table("jobs")
            .select("error")
            .eq("id", job_id)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            return result.data.get("error") == "cancelled_by_user"
    except Exception:
        pass
    return False

def _update_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    service_client().table("jobs").update(fields).eq("id", job_id).execute()


# ─── Job: recursive taxon fetch ──────────────────────────────────────

async def fetch_taxon_recursive(
    ctx: dict[str, Any],
    job_id: str,
    root_taxid: int,
    stop_rank: str,
    max_species: int,
) -> dict[str, Any]:
    """Walk the subtree of `root_taxid`, upsert each discovered taxon."""
    _update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        progress=0,
    )

    try:
        taxids = await asyncio.to_thread(
            ncbi.list_descendant_taxids, root_taxid, stop_rank, max_species
        )
        taxids = taxids[:max_species]
        total = len(taxids)

        if total == 0:
            result = {"fetched": 0, "skipped": 0, "failed": 0, "total": 0}
            _invalidate_tree_cache()
            _update_job(
                job_id,
                status="done",
                finished_at=datetime.now(timezone.utc).isoformat(),
                result=result,
                progress=100,
            )
            return result

        client = service_client()
        fetched = skipped = failed = 0

        for i, taxid in enumerate(taxids):
            if _is_cancelled(job_id):
                print(f"[worker] job {job_id} cancelled")
                return {"fetched": fetched, "skipped": skipped, "failed": failed, "total": total, "cancelled": True}
            try:
                row = await build_species_row(taxid)
                if row is None:
                    skipped += 1
                else:
                    client.table("species").upsert(row, on_conflict="ncbi_tax_id").execute()
                    fetched += 1
            except Exception as e:
                failed += 1
                print(f"[worker] fetch failed for taxid {taxid}: {e}")

            if (i + 1) % 5 == 0 or i == total - 1:
                pct = int(((i + 1) / total) * 100)
                _update_job(
                    job_id,
                    progress=pct,
                    result={
                        "fetched": fetched,
                        "skipped": skipped,
                        "failed": failed,
                        "total": total,
                        "current": i + 1,
                    },
                )

            # NCBI: ~3 req/s without API key. We do ~4 calls per species.
            # 1.5s gap keeps us polite.
            await asyncio.sleep(1.5)

        result = {"fetched": fetched, "skipped": skipped, "failed": failed, "total": total}
        _invalidate_tree_cache()
        _update_job(
            job_id,
            status="done",
            finished_at=datetime.now(timezone.utc).isoformat(),
            result=result,
            progress=100,
        )
        try:
            client.table("tree_layouts").delete().neq("cache_key", "").execute()
        except Exception:
            pass
        return result

    except Exception as e:
        _update_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
        raise


# ─── Job: resync a single species ────────────────────────────────────

async def resync_species(ctx: dict[str, Any], job_id: str, taxid: int) -> dict[str, Any]:
    _update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        row = await build_species_row(taxid)
        if row is None:
            raise ValueError(f"taxid {taxid} not found on NCBI")
        service_client().table("species").upsert(row, on_conflict="ncbi_tax_id").execute()
        result = {"taxid": taxid, "scientific_name": row["scientific_name"]}
        _invalidate_tree_cache()
        _update_job(
            job_id,
            status="done",
            finished_at=datetime.now(timezone.utc).isoformat(),
            result=result,
            progress=100,
        )
        return result
    except Exception as e:
        _update_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
        raise


# ─── Worker config ───────────────────────────────────────────────────
async def resync_all_species(ctx: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Walk every species in the pool, refetch each from NCBI."""
    _update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        progress=0,
    )
    try:
        client = service_client()
        all_species = (
            client.table("species")
            .select("id, ncbi_tax_id")
            .not_.is_("ncbi_tax_id", "null")
            .execute()
        )
        rows = all_species.data or []
        total = len(rows)
        if total == 0:
            _invalidate_tree_cache()
            _update_job(
                job_id,
                status="done",
                finished_at=datetime.now(timezone.utc).isoformat(),
                result={"refreshed": 0, "failed": 0, "total": 0},
                progress=100,
            )
            return {"refreshed": 0, "failed": 0, "total": 0}

        refreshed = failed = 0
        for i, sp in enumerate(rows):
            if _is_cancelled(job_id):
                return {"refreshed": refreshed, "failed": failed, "total": total, "cancelled": True}
            try:
                row = await build_species_row(int(sp["ncbi_tax_id"]))
                if row is not None:
                    client.table("species").upsert(row, on_conflict="ncbi_tax_id").execute()
                    refreshed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(f"[worker] resync failed for taxid {sp['ncbi_tax_id']}: {e}")

            if (i + 1) % 5 == 0 or i == total - 1:
                pct = int(((i + 1) / total) * 100)
                _update_job(
                    job_id,
                    progress=pct,
                    result={"refreshed": refreshed, "failed": failed, "total": total, "current": i + 1},
                )
            await asyncio.sleep(1.5)

        result = {"refreshed": refreshed, "failed": failed, "total": total}
        _invalidate_tree_cache()
        _update_job(
            job_id,
            status="done",
            finished_at=datetime.now(timezone.utc).isoformat(),
            result=result,
            progress=100,
        )
        return result
    except Exception as e:
        _update_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
        raise

async def backfill_lineages(ctx: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Re-fetch every existing species's taxonomy to populate data.lineage
    with TaxIDs. Use after upgrading the enrichment code.
    """
    _update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        progress=0,
    )
    try:
        client = service_client()
        rows = (
            client.table("species")
            .select("id, ncbi_tax_id, data")
            .not_.is_("ncbi_tax_id", "null")
            .execute()
        )
        species = rows.data or []
        # Skip ones that already have data.lineage populated with taxid entries.
        to_backfill = []
        for sp in species:
            lineage = ((sp.get("data") or {}).get("lineage")) or []
            if not lineage or not any(e.get("taxid") for e in lineage):
                to_backfill.append(sp)

        total = len(to_backfill)
        if total == 0:
            _invalidate_tree_cache()
            _update_job(
                job_id, status="done", progress=100,
                finished_at=datetime.now(timezone.utc).isoformat(),
                result={"updated": 0, "total": 0, "skipped_already_done": len(species)},
            )
            return {"updated": 0, "total": 0}

        updated = failed = 0
        for i, sp in enumerate(to_backfill):
            if _is_cancelled(job_id):
                return {"updated": updated, "failed": failed, "total": total, "cancelled": True}
            try:
                taxid = int(sp["ncbi_tax_id"])
                tax = await asyncio.to_thread(ncbi.fetch_taxonomy, taxid)
                if tax and tax.get("lineage"):
                    existing_data = sp.get("data") or {}
                    existing_data["lineage"] = tax["lineage"]
                    client.table("species").update({
                        "data": existing_data,
                        "taxonomy": tax.get("taxonomy_map", sp.get("taxonomy") or {}),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", sp["id"]).execute()
                    updated += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(f"[worker] backfill failed for {sp.get('ncbi_tax_id')}: {e}")

            if (i + 1) % 5 == 0 or i == total - 1:
                pct = int(((i + 1) / total) * 100)
                _update_job(
                    job_id,
                    progress=pct,
                    result={"updated": updated, "failed": failed, "total": total, "current": i + 1},
                )
            await asyncio.sleep(0.4)   # NCBI only — no wiki calls — faster pacing

        _invalidate_tree_cache()
        _update_job(
            job_id, status="done", progress=100,
            finished_at=datetime.now(timezone.utc).isoformat(),
            result={"updated": updated, "failed": failed, "total": total},
        )
        return {"updated": updated, "failed": failed, "total": total}
    except Exception as e:
        _update_job(
            job_id, status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
        raise
async def run_fastqc_job(
    ctx: dict[str, Any],
    job_id: str,
    fastq_b64: str,
    filename: str,
) -> dict[str, Any]:
    """Run FastQC on a base64-encoded FASTQ payload."""
    import base64
    from app.sources.fastqc import run_fastqc

    _update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        progress=10,
    )
    try:
        fastq_bytes = base64.b64decode(fastq_b64)
        result = await asyncio.to_thread(run_fastqc, fastq_bytes, filename)
        _update_job(
            job_id,
            status="done",
            finished_at=datetime.now(timezone.utc).isoformat(),
            result={
                "summary": result["summary"],
                "stats": result["stats"],
                "modules": result["modules"],
                "report_html": result["report_html"],
                "filename": filename,
            },
            progress=100,
        )
        return result
    except Exception as e:
        _update_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
        raise

class _LazyRedisMeta(type):
    """Resolves `redis_settings` on attribute access so importing this module
    requires no arq (Postgres mode), while arq still finds the setting when it
    instantiates the worker (Redis mode)."""
    @property
    def redis_settings(cls):
        return _redis_settings()


class WorkerSettings(metaclass=_LazyRedisMeta):
    """arq worker config. Only used when JOB_BACKEND=redis and you run
    `uv run arq app.worker.WorkerSettings`. In the default Postgres mode this is
    never instantiated by arq; `redis_settings` is lazy so importing the module
    (e.g. via app.jobs) does not require arq installed."""
    functions = [fetch_taxon_recursive, resync_species, resync_all_species, backfill_lineages, run_fastqc_job]
    job_timeout = 60 * 60 * 4  # 4h — recursive fetch of a big taxon can take hours
    max_jobs = 2               # keep NCBI happy; don't saturate
    max_tries = 1
