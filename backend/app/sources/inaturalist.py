"""iNaturalist — citizen science photos of species.

Docs: https://api.inaturalist.org/v1/docs/

We use the `/taxa/autocomplete` or `/taxa` endpoint to find the taxon,
then `/observations` to find research-grade records with photos.

All iNaturalist photos are user-submitted and licensed — we must show
attribution and license. Most are CC-BY-NC.
"""
from __future__ import annotations

from typing import Any

import httpx

HEADERS = {"User-Agent": "SentinelBio/0.1 (https://github.com/elementare)"}
BASE = "https://api.inaturalist.org/v1"


async def fetch_photos(scientific_name: str, limit: int = 8) -> list[dict[str, Any]]:
    """Find photos for a species. Returns a list of {url, attribution, license, source}."""
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as c:
        # Find the taxon by exact name.
        tr = await c.get(f"{BASE}/taxa", params={"q": scientific_name, "rank": "species", "per_page": 1})
        if tr.status_code != 200:
            return []
        taxa = tr.json().get("results") or []
        if not taxa:
            return []
        taxon_id = taxa[0]["id"]

        # Research-grade observations with photos, newest first.
        params = {
            "taxon_id": taxon_id,
            "quality_grade": "research",
            "photos": "true",
            "per_page": limit,
            "order_by": "created_at",
            "order": "desc",
        }
        r = await c.get(f"{BASE}/observations", params=params)
        if r.status_code != 200:
            return []
        obs = r.json().get("results") or []

    out: list[dict[str, Any]] = []
    for o in obs:
        photos = o.get("photos") or []
        if not photos:
            continue
        p = photos[0]
        url = p.get("url") or ""
        # iNat delivers small squares by default; upgrade to "large".
        if url.endswith("square.jpg") or url.endswith("square.jpeg"):
            url = url.replace("/square.", "/large.")
        elif "/square." in url:
            url = url.replace("/square.", "/large.")

        user = (o.get("user") or {}).get("login") or "unknown"
        license_code = p.get("license_code") or "unknown"
        out.append({
            "url": url,
            "attribution": f"(c) {user}, some rights reserved ({license_code.upper()}) via iNaturalist",
            "license": license_code,
            "source": "inaturalist",
            "source_url": f"https://www.inaturalist.org/observations/{o.get('id')}",
        })
    return out
