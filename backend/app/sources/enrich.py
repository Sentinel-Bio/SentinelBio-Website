"""Shared species enrichment — used by single-species fetch and bulk worker."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.sources import ncbi, wikipedia
from app.sources.ncbi import fetch_assembly_count

async def build_species_row(taxid: int) -> dict[str, Any] | None:
    """Fetch everything we know about a TaxID and return an upsert-ready row."""
    tax = ncbi.fetch_taxonomy(taxid)
    if not tax:
        return None

    scientific_name = tax["scientific_name"]
    rank = (tax.get("rank") or "").lower()
    is_species_like = rank in ("species", "subspecies", "forma", "varietas")

    wiki = await wikipedia.fetch_summary(scientific_name)

    try:
        nuc_count = ncbi.count_nucleotide_records(taxid)
    except Exception:
        nuc_count = None
    try:
        prot_count = ncbi.count_protein_records(taxid)
    except Exception:
        prot_count = None

    # Only fetch best assembly for species-like taxa; for higher groups
    # we fetch the assembly count instead.
    assembly = None
    assembly_count = None
    if is_species_like:
        assembly = ncbi.fetch_best_assembly(taxid)
    else:
        try:
            assembly_count = fetch_assembly_count(taxid)
        except Exception:
            assembly_count = None

    image_obj = None
    if wiki and wiki.get("image_url"):
        image_obj = {
            "url": wiki["image_url"],
            "attribution": "Wikipedia / Wikimedia Commons",
            "license": "see source",
            "source": "wikipedia-lead",
        }

    data: dict[str, Any] = {
        "lineage": tax.get("lineage", []),
        "common_names": tax.get("common_names") or [],
        "division": tax.get("division"),
        "genetic_code": tax.get("genetic_code"),
        "blurb": (wiki or {}).get("extract"),
        "wikipedia_url": (wiki or {}).get("wikipedia_url"),
        "description": (wiki or {}).get("description"),
        "nucleotide_record_count": nuc_count,
        "protein_record_count": prot_count,
    }

    if is_species_like:
        data["has_genome_assembly"] = assembly is not None
        data["assembly"] = assembly
    else:
        if assembly_count is not None:
            data["assembly_count"] = assembly_count
    # Auto-extract traits for species (best-effort; admin can add more manually).
    if is_species_like and wiki:
        from app.sources.wikipedia import extract_traits_from_wiki
        auto_traits = extract_traits_from_wiki((wiki or {}).get("extract"))
        if auto_traits:
            # Merge with any manually-curated traits, preserving manual entries.
            existing_traits = data.get("traits") or []
            manual = [t for t in existing_traits if t.get("source") != "wikipedia_auto"]
            # Auto traits don't overwrite manual ones with the same label.
            manual_labels = {t.get("label") for t in manual}
            new_auto = [t for t in auto_traits if t.get("label") not in manual_labels]
            data["traits"] = manual + new_auto
    return {
        "ncbi_tax_id": taxid,
        "scientific_name": scientific_name,
        "common_name": tax.get("common_name"),
        "rank": tax.get("rank"),
        "taxonomy": tax.get("taxonomy_map", {}),
        "data": data,
        "image": image_obj,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
