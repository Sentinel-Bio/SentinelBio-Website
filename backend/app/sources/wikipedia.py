"""Wikipedia summary + lead image via REST API."""
from __future__ import annotations

import urllib.parse
import re
from typing import Any
import httpx

SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
HEADERS = {"User-Agent": "SentinelBio/0.1 (https://github.com/elementare)"}

TRAIT_PATTERNS: list[tuple[str, str]] = [
    ("length", r"\b(length|body length|total length)\b"),
    ("height", r"\b(height|shoulder height|standing height)\b"),
    ("weight", r"\b(weight|mass|body mass|adult weight)\b"),
    ("lifespan", r"\b(lifespan|longevity|life span|life expectancy)\b"),
    ("diet", r"\b(diet|feeding)\b"),
    ("habitat", r"\b(habitat|range)\b"),
    ("depth", r"\b(depth|diving depth|dive depth|max depth)\b"),
    ("speed", r"\b(speed|swim speed|running speed|maximum speed)\b"),
    ("gestation", r"\b(gestation|gestation period|pregnancy)\b"),
]


async def fetch_summary(scientific_name: str) -> dict | None:
    title = urllib.parse.quote(scientific_name.replace(" ", "_"))
    url = SUMMARY_URL.format(title=title)
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
        try:
            r = await c.get(url, params={"redirect": "true"})
        except httpx.RequestError:
            return None
    if r.status_code != 200:
        return None
    data = r.json()
    extract = data.get("extract")
    if not extract:
        return None
    thumb = data.get("thumbnail") or {}
    original = data.get("originalimage") or {}
    image_url = original.get("source") or thumb.get("source")

    return {
        "extract": extract,
        "description": data.get("description"),
        "wikipedia_url": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page"),
        "image_url": image_url,
    }

def extract_traits_from_wiki(summary_text: str | None) -> list[dict[str, Any]]:
    """Best-effort trait extraction from Wikipedia extract text.

    Only matches things with recognizable numeric values + units.
    Admin fills in the rest manually via the species editor.
    """
    if not summary_text:
        return []

    traits: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    text = re.sub(r"\s+", " ", summary_text)

    for label, pattern in TRAIT_PATTERNS:
        if label in seen_labels:
            continue
        match = re.search(
            rf"{pattern}[^\d.]{{0,40}}?(\d[0-9.,\s\-–]*\s*(?:kg|g|lb|lbs|cm|m|ft|in|mm|years?|yrs?|mph|km/h)\b)",
            text,
            re.IGNORECASE,
        )
        if match:
            value = match.group(2).strip().rstrip(".,;")
            if 2 <= len(value) <= 50:
                traits.append({
                    "label": label,
                    "value": value,
                    "source": "wikipedia_auto",
                })
                seen_labels.add(label)

    return traits
