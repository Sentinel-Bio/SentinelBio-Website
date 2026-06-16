"""RCSB PDB integration — structure search and metadata fetching."""
from __future__ import annotations

import httpx
from typing import Any


RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_DATA_URL = "https://data.rcsb.org/rest/v1/core/entry"


async def search_structures_by_gene(gene_symbol: str, limit: int = 20) -> list[dict[str, Any]]:
    """Query RCSB for structures associated with a gene symbol.
    Returns [{pdb_id, title, organism, resolution, method, year}].
    """
    query = {
        "query": {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "attribute": "rcsb_entity_source_organism.rcsb_gene_name.value",
                "operator": "exact_match",
                "value": gene_symbol,
            },
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": limit},
            "results_content_type": ["experimental"],
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(RCSB_SEARCH_URL, json=query)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[rcsb] search failed: {e}")
            return []

    entries = data.get("result_set", [])
    if not entries:
        return []

    pdb_ids = [e["identifier"] for e in entries[:limit]]

    # Fetch metadata for each in parallel.
    results = []
    async with httpx.AsyncClient(timeout=15) as client:
        for pdb_id in pdb_ids:
            try:
                r = await client.get(f"{RCSB_DATA_URL}/{pdb_id}")
                r.raise_for_status()
                d = r.json()
                results.append({
                    "pdb_id": pdb_id,
                    "title": (d.get("struct") or {}).get("title", ""),
                    "organism": _extract_organism(d),
                    "resolution": _extract_resolution(d),
                    "method": _extract_method(d),
                    "year": _extract_year(d),
                })
            except Exception as e:
                print(f"[rcsb] metadata fetch failed for {pdb_id}: {e}")

    return results


async def get_structure_metadata(pdb_id: str) -> dict[str, Any] | None:
    pdb_id = pdb_id.upper().strip()
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{RCSB_DATA_URL}/{pdb_id}")
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            print(f"[rcsb] metadata fetch failed for {pdb_id}: {e}")
            return None

    return {
        "pdb_id": pdb_id,
        "title": (d.get("struct") or {}).get("title", ""),
        "organism": _extract_organism(d),
        "resolution": _extract_resolution(d),
        "method": _extract_method(d),
        "year": _extract_year(d),
    }


def _extract_organism(data: dict[str, Any]) -> str:
    orgs = data.get("rcsb_entry_container_identifiers", {}).get("entity_ids", [])
    return ""


def _extract_resolution(data: dict[str, Any]) -> float | None:
    info = data.get("rcsb_entry_info") or {}
    res = info.get("resolution_combined")
    if isinstance(res, list) and res:
        return res[0]
    return None


def _extract_method(data: dict[str, Any]) -> str:
    exptl = data.get("exptl") or []
    if exptl and isinstance(exptl, list):
        return exptl[0].get("method", "")
    return ""


def _extract_year(data: dict[str, Any]) -> int | None:
    accession = data.get("rcsb_accession_info") or {}
    date = accession.get("deposit_date") or accession.get("initial_release_date")
    if date and len(date) >= 4:
        try:
            return int(date[:4])
        except ValueError:
            return None
    return None
