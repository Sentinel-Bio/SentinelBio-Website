"""Wikimedia Commons — extra images beyond the Wikipedia lead.

We search the Commons API for files tagged with the species name.
"""
from __future__ import annotations

from typing import Any
import urllib.parse

import httpx

HEADERS = {"User-Agent": "SentinelBio/0.1 (https://github.com/elementare)"}
API = "https://commons.wikimedia.org/w/api.php"


async def fetch_photos(scientific_name: str, limit: int = 8) -> list[dict[str, Any]]:
    """Find images on Wikimedia Commons for this taxon."""
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as c:
        # Search for files whose title or caption mentions the species.
        search_params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": "6",  # File namespace
            "gsrsearch": scientific_name,
            "gsrlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": "1200",
        }
        r = await c.get(API, params=search_params)
        if r.status_code != 200:
            return []
        pages = (r.json().get("query") or {}).get("pages") or {}

    out: list[dict[str, Any]] = []
    for _, page in pages.items():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        meta = info.get("extmetadata") or {}
        artist = (meta.get("Artist") or {}).get("value", "unknown")
        license_name = (meta.get("LicenseShortName") or {}).get("value", "unknown")
        # Artist often has HTML — strip tags crudely.
        import re
        artist = re.sub(r"<[^>]+>", "", artist).strip() or "unknown"

        out.append({
            "url": url,
            "attribution": f"{artist} ({license_name}) via Wikimedia Commons",
            "license": license_name,
            "source": "wikimedia",
            "source_url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page.get('title', ''))}",
        })
    return out
