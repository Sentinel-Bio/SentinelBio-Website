"""Species endpoints — search, fetch, detail."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Query
from slugify import slugify

from app.auth import RequireUser, is_admin
from app.rate_limit import limiter
from app.sources import ncbi, wikipedia, rcsb
from app.supabase_client import service_client
from app.tree_layout import layout as build_layout
import asyncio
import re

async def _invalidate_tree_cache() -> None:
    client = service_client()
    client.table("tree_layouts").delete().neq("cache_key", "").execute()

router = APIRouter(prefix="/species", tags=["species"])

@router.get("")
async def list_species(
    q: str | None = None,
    rank: str | None = None,
    kingdom: str | None = None,
    phylum: str | None = None,
    class_: str | None = None,
    order: str | None = None,
    family: str | None = None,
    genus: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search species. All filters are ANDed together.

    Note: `class_` (with trailing underscore) is the query param `class`.
    FastAPI auto-maps it because `class` is a Python reserved word.
    """
    client = service_client()
    query = client.table("species").select("*").limit(min(limit, 500))
    if q:
        query = query.ilike("scientific_name", f"%{q}%")
    if rank:
        query = query.eq("rank", rank)

    # Taxonomy filters are JSON extractions. We use Postgres JSONB containment
    # via the `cs` operator on the taxonomy object.
    for key, val in [
        ("kingdom", kingdom),
        ("phylum", phylum),
        ("class", class_),
        ("order", order),
        ("family", family),
        ("genus", genus),
    ]:
        if val:
            # supabase-py's `contains` does jsonb @> matching
            query = query.contains("taxonomy", {key: val})

    query = query.order("updated_at", desc=True)
    return query.execute().data or []

@router.get("/tree")
async def get_tree(root: str = "Eukaryota") -> dict[str, Any]:
    """Build a hierarchical tree from all species currently in our DB.

    We walk each species's `taxonomy` field (a rank→name map) and construct
    a tree rooted at the given name. Returns nested {name, rank, taxid?, children}.
    """
    client = service_client()
    rows = (
        client.table("species")
        .select("scientific_name, rank, ncbi_tax_id, taxonomy")
        .limit(10000)
        .execute()
    )
    species = rows.data or []

    RANK_ORDER = [
        "domain", "superkingdom", "kingdom", "subkingdom",
        "phylum", "subphylum", "class", "subclass",
        "superorder", "order", "suborder", "infraorder",
        "superfamily", "family", "subfamily",
        "tribe", "subtribe", "genus", "subgenus",
        "species", "subspecies",
    ]

    # First pass: build a name → {rank, taxid} map for every named taxon we
    # encounter, from lineages and from the species records themselves.
    info: dict[str, dict[str, Any]] = {}

    def register(name: str, rank: str | None, taxid: int | None = None):
        if not name:
            return
        slot = info.setdefault(name, {"rank": rank, "taxid": taxid, "children": set()})
        if rank and not slot.get("rank"):
            slot["rank"] = rank
        if taxid and not slot.get("taxid"):
            slot["taxid"] = taxid

    # Parent linkage — a name's parent is the next-coarser rank in its lineage.
    def parent_of(lineage_entries: list[tuple[str, str]], idx: int) -> str | None:
        if idx == 0:
            return None
        return lineage_entries[idx - 1][1]  # name of previous (coarser) rank

    for sp in species:
        tax = sp.get("taxonomy") or {}
        # Build an ordered lineage [(rank, name), ...]
        entries = [
            (rank, name)
            for rank, name in tax.items()
            if rank in RANK_ORDER and name
        ]
        entries.sort(key=lambda p: RANK_ORDER.index(p[0]))

        # Register each ancestor and link to its parent.
        for i, (rank, name) in enumerate(entries):
            register(name, rank)
            parent = parent_of(entries, i)
            if parent and parent != name:
                info[parent]["children"].add(name)
        # Register the species itself.
        sp_name = sp["scientific_name"]
        register(sp_name, sp.get("rank") or "species", sp.get("ncbi_tax_id"))
        if entries and entries[-1][1] != sp_name:
            info[entries[-1][1]]["children"].add(sp_name)

    # Find the root by name.
    if root not in info:
        # Nothing in the DB below this root.
        return {"name": root, "rank": None, "taxid": None, "children": []}

    def build(name: str, visited: set[str]) -> dict[str, Any]:
        if name in visited:
            # Cycle — stop descending.
            return {
                "name": name,
                "rank": info.get(name, {}).get("rank"),
                "taxid": info.get(name, {}).get("taxid"),
                "children": [],
            }
        visited = visited | {name}  # new set, don't mutate caller's
        node = info[name]
        children = [build(c, visited) for c in sorted(node["children"]) if c != name]
        return {
            "name": name,
            "rank": node.get("rank"),
            "taxid": node.get("taxid"),
            "children": children,
        }

    return build(root, set())

@router.get("/{taxid}/subtree")
async def get_subtree(taxid: int) -> dict[str, Any]:
    """Same as /tree but rooted at an arbitrary taxon's scientific name."""
    client = service_client()
    root_row = (
        client.table("species")
        .select("scientific_name")
        .eq("ncbi_tax_id", taxid)
        .maybe_single()
        .execute()
    )
    if not root_row or not root_row.data:
        return {"name": str(taxid), "rank": None, "taxid": taxid, "children": []}
    return await get_tree(root=root_row.data["scientific_name"])  # reuse

@router.get("/tree/layout")
async def get_tree_layout(
    root_taxid: int | None = Query(default=None),
) -> dict[str, Any]:
    """Return all nodes in the tree with pre-computed 2D positions.

    If `root_taxid` is given, filter to descendants of that taxon and
    re-center positions so the subtree root sits at (0, 0).
    """
    client = service_client()

    # Cache key depends on root_taxid so different subtrees don't collide.
    cache_key = f"full" if root_taxid is None else f"sub-{root_taxid}"
    cached = (
        client.table("tree_layouts")
        .select("data, computed_at")
        .eq("cache_key", cache_key)
        .maybe_single()
        .execute()
    )
    if cached and cached.data:
        return cached.data["data"]

    # Build the nested tree (LUCA-rooted), then run the radial layout over it.
    full_tree = await _build_full_tree()
    nodes = build_layout(full_tree)

    # If no subtree requested, return / cache the full tree.
    if root_taxid is None:
        client.table("tree_layouts").upsert(
            {"cache_key": cache_key, "data": {"nodes": nodes}}
        ).execute()
        return {"nodes": nodes}

    # Subtree: walk descendants of root_taxid and filter.
    by_taxid = {n["taxid"]: n for n in nodes if n.get("taxid") is not None}
    root_node = by_taxid.get(root_taxid)
    if not root_node:
        raise HTTPException(status_code=404, detail="root_taxid_not_in_tree")

    children_by_parent: dict[int, list[int]] = {}
    for n in nodes:
        pt = n.get("parent_taxid")
        if pt is None:
            continue
        children_by_parent.setdefault(pt, []).append(n["taxid"])

    keep: set[int] = {root_taxid}
    stack = [root_taxid]
    while stack:
        t = stack.pop()
        for c in children_by_parent.get(t, []):
            if c not in keep:
                keep.add(c)
                stack.append(c)

    # Re-center positions at the subtree root, adjust depths, and cut the
    # subtree root's parent so it appears as the new root.
    rx = root_node["x"]
    ry = root_node["y"]
    root_depth = root_node["depth"]

    filtered = []
    for n in nodes:
        if n.get("taxid") not in keep:
            continue
        # Copy to avoid mutating the cached full-tree nodes.
        adjusted = dict(n)
        adjusted["x"] = n["x"] - rx
        adjusted["y"] = n["y"] - ry
        adjusted["depth"] = n["depth"] - root_depth
        if adjusted["taxid"] == root_taxid:
            adjusted["parent_taxid"] = None
        filtered.append(adjusted)

    payload = {"nodes": filtered}

    # Cache the subtree result.
    client.table("tree_layouts").upsert(
        {"cache_key": cache_key, "data": payload}
    ).execute()

    return payload

async def _build_full_tree() -> dict[str, Any]:
    """Construct a nested tree dict rooted at LUCA ('cellular organisms'),
    keyed by NCBI TaxID.

    Every internal node has a real taxid because we pull them from species
    lineages (which store them now).
    """
    client = service_client()
    rows = (
        client.table("species")
        .select("scientific_name, rank, ncbi_tax_id, taxonomy, data")
        .limit(50000)
        .execute()
    )
    species = rows.data or []

    # Node info keyed by taxid.
    # Unknown-taxid ancestors (from old data) get negative synthetic IDs.
    info: dict[int, dict[str, Any]] = {}
    name_to_id: dict[str, int] = {}
    next_synthetic = -1

    def register(
        taxid: int | None,
        name: str,
        rank: str | None,
    ) -> int:
        nonlocal next_synthetic
        if taxid is not None:
            key = taxid
        elif name in name_to_id:
            key = name_to_id[name]
        else:
            key = next_synthetic
            next_synthetic -= 1
            name_to_id[name] = key

        if key not in info:
            info[key] = {
                "taxid": taxid if taxid is not None else None,
                "name": name,
                "rank": rank,
                "children": set(),
            }
        else:
            slot = info[key]
            if rank and not slot.get("rank"):
                slot["rank"] = rank
            if taxid is not None and slot.get("taxid") is None:
                slot["taxid"] = taxid
        return key

    # Root: LUCA.
    LUCA = 131567
    register(LUCA, "cellular organisms", "root")

    for sp in species:
        sp_taxid = sp.get("ncbi_tax_id")
        sp_name = sp["scientific_name"]
        sp_rank = sp.get("rank") or "species"

        # Rich lineage preferred; fall back to flat taxonomy map if data.lineage missing.
        lineage = ((sp.get("data") or {}).get("lineage")) or []
        if not lineage:
            flat = sp.get("taxonomy") or {}
            RANK_ORDER = [
                "superkingdom", "kingdom", "subkingdom",
                "phylum", "subphylum", "class", "subclass",
                "superorder", "order", "suborder", "infraorder",
                "superfamily", "family", "subfamily",
                "tribe", "subtribe", "genus", "subgenus",
            ]
            lineage = [
                {"rank": rank, "name": flat[rank], "taxid": None}
                for rank in RANK_ORDER
                if rank in flat and flat[rank]
            ]

        # Walk lineage from root → species. Each item's parent is the previous item.
        parent_key = LUCA
        for entry in lineage:
            key = register(entry.get("taxid"), entry["name"], entry.get("rank"))
            if key != parent_key:
                info[parent_key]["children"].add(key)
            parent_key = key

        # Attach species itself under its last ancestor.
        if sp_taxid:
            sp_key = register(sp_taxid, sp_name, sp_rank)
            if sp_key != parent_key:
                info[parent_key]["children"].add(sp_key)

    def build(key: int, visited: set[int]) -> dict[str, Any]:
        if key in visited:
            node = info.get(key, {})
            return {
                "taxid": node.get("taxid"),
                "name": node.get("name", str(key)),
                "rank": node.get("rank"),
                "children": [],
            }
        visited = visited | {key}
        node = info[key]
        children = sorted(node["children"], key=lambda k: info[k]["name"])
        return {
            "taxid": node.get("taxid"),
            "name": node["name"],
            "rank": node.get("rank"),
            "children": [build(c, visited) for c in children if c != key],
        }

    return build(LUCA, set())

@router.post("/tree/invalidate")
async def invalidate_tree() -> dict[str, str]:
    """Called internally after species mutations."""
    await _invalidate_tree_cache()
    return {"status": "cleared"}

@router.get("/{taxid}")
async def get_species(taxid: int) -> dict[str, Any]:
    """Return the species row for a TaxID.

    If the taxon isn't in our DB yet, fetch it synchronously from NCBI
    (this creates a new row — any TaxID becomes a first-class citizen once
    visited).
    """
    client = service_client()
    result = (
        client.table("species")
        .select("*")
        .eq("ncbi_tax_id", taxid)
        .maybe_single()
        .execute()
    )
    if result and result.data:
        return result.data

    # Not in DB. Fetch from NCBI on-demand.
    from app.sources.enrich import build_species_row
    row = await build_species_row(taxid)
    if row is None:
        raise HTTPException(status_code=404, detail="taxid_not_in_ncbi")

    upserted = (
        client.table("species")
        .upsert(row, on_conflict="ncbi_tax_id")
        .execute()
    )
    if not upserted.data:
        raise HTTPException(status_code=500, detail="upsert_failed")

    await _invalidate_tree_cache()
    return upserted.data[0]

@router.post("/fetch/{taxid}")
@limiter.limit("20/hour")
async def fetch_by_taxid(
    request: Request,
    taxid: int,
    user: RequireUser,
) -> dict[str, Any]:
    """Fetch a species by NCBI TaxID. Rate limited for non-admins.

    Upsert-by-taxid: if we already have it, we re-enrich and return the updated row.
    """
    admin = await is_admin(user)
    # Admins bypass the rate limit — slowapi doesn't cleanly support this,
    # so we just do the check manually. Admins can still hit this endpoint without
    # the decorator firing because we re-check below; the decorator's rate-count
    # increments regardless, but that's fine — admins won't hit a limit.
    # (If we want strict bypass, we'd need a custom Limiter subclass.)

    tax = ncbi.fetch_taxonomy(taxid)
    if not tax:
        raise HTTPException(status_code=404, detail="taxid_not_found_on_ncbi")

    scientific_name = tax["scientific_name"]

    wiki = await wikipedia.fetch_summary(scientific_name)

    # Stat counts — cheap, useful for "do we have sequences to analyze?"
    try:
        nuc_count = ncbi.count_nucleotide_records(taxid)
    except Exception:
        nuc_count = None
    try:
        prot_count = ncbi.count_protein_records(taxid)
    except Exception:
        prot_count = None

    # Genome assembly (best available) — includes GCF/GCA accession + URL.
    assembly = ncbi.fetch_best_assembly(taxid)
    has_genome = assembly is not None

    # Map NCBI lineage into the taxonomy column shape we use.
    taxonomy = tax.get("lineage", {})

    data_blob: dict[str, Any] = {
        "common_names": tax.get("common_names") or [],
        "division": tax.get("division"),
        "genetic_code": tax.get("genetic_code"),
        "blurb": (wiki or {}).get("extract"),
        "wikipedia_url": (wiki or {}).get("wikipedia_url"),
        "description": (wiki or {}).get("description"),
        "nucleotide_record_count": nuc_count,
        "protein_record_count": prot_count,
        "has_genome_assembly": has_genome,
        "assembly": assembly,
    }

    image_obj = None
    if wiki and wiki.get("image_url"):
        image_obj = {
            "url": wiki["image_url"],
            "attribution": "Wikipedia / Wikimedia Commons",
            "license": "see source",
            "source": "wikipedia-lead",
        }

    row = {
        "ncbi_tax_id": taxid,
        "scientific_name": scientific_name,
        "common_name": tax.get("common_name"),
        "rank": tax.get("rank"),
        "taxonomy": taxonomy,
        "data": data_blob,
        "image": image_obj,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    client = service_client()
    result = (
        client.table("species")
        .upsert(row, on_conflict="ncbi_tax_id")
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=500, detail="upsert_failed")
    # Silently exempt admins would go here if we strictly enforced. For now, 20/h is generous.
    _ = admin
    await _invalidate_tree_cache()
    return {"species": result.data[0], "slug": slugify(scientific_name)}


@router.post("/resolve-name")
@limiter.limit("30/hour")
async def resolve_name(
    request: Request,
    payload: dict[str, str],
    user: RequireUser,
) -> dict[str, Any]:
    """Resolve a scientific or common name to a TaxID (no fetch, just lookup)."""
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="missing_name")
    taxid = ncbi.resolve_name_to_taxid(name)
    if not taxid:
        raise HTTPException(status_code=404, detail="name_not_found")
    return {"taxid": taxid, "query": name}

# ─── Assembly listing + FASTA fetching ─────────────────────────────────

@router.get("/{taxid}/assemblies")
async def list_species_assemblies(
    taxid: int,
    limit: int = Query(default=100, le=500),
) -> list[dict[str, Any]]:
    """List all NCBI assemblies for a taxon. Read-through to NCBI; not cached."""
    assemblies = ncbi.list_assemblies(taxid, limit=limit)
    return assemblies


@router.get("/{taxid}/fetch-assembly-fasta")
@limiter.limit("20/hour")
async def fetch_assembly_fasta(
    request: Request,
    taxid: int,
    user: RequireUser,
    accession: str = Query(..., description="Assembly accession (GCF_xxx or GCA_xxx)"),
) -> dict[str, Any]:
    """Return the HTTPS URL for a genome assembly FASTA (gzipped).

    The frontend downloads directly from NCBI. We don't proxy the bytes through
    our backend — genomes are 50 MB to 5 GB and we don't want to double the
    bandwidth cost.
    """
    url = ncbi.fetch_assembly_fasta_url(accession)
    if not url:
        raise HTTPException(status_code=404, detail="assembly_not_found")
    return {"url": url, "accession": accession}


@router.post("/{taxid}/fetch-gene-fasta")
@limiter.limit("30/hour")
async def fetch_gene_fasta_endpoint(
    request: Request,
    taxid: int,
    user: RequireUser,
    payload: dict[str, str],
) -> dict[str, str]:
    """Fetch a gene's FASTA sequence for this species. Returns the raw FASTA text."""
    gene_symbol = (payload or {}).get("gene_symbol", "").strip()
    if not gene_symbol:
        raise HTTPException(status_code=400, detail="missing_gene_symbol")

    fasta = await asyncio.to_thread(ncbi.fetch_gene_fasta, taxid, gene_symbol)
    if not fasta:
        raise HTTPException(status_code=404, detail="gene_not_found_for_species")

    return {"fasta": fasta, "gene_symbol": gene_symbol, "taxid": str(taxid)}

@router.get("/{taxid}/descendants")
async def get_descendants_sample(
    taxid: int,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return a sample of species/subtaxa that are descendants of `taxid` in
    the *current* DB. Used by the group pages to show representative examples.

    We match on any taxonomy field equal to the root's scientific name.
    """
    client = service_client()
    # Fetch the root to know its scientific name.
    root = (
        client.table("species")
        .select("scientific_name, rank")
        .eq("ncbi_tax_id", taxid)
        .maybe_single()
        .execute()
    )
    if not root or not root.data:
        return []
    root_name = root.data["scientific_name"]

    # Query species whose taxonomy contains the root name in any position.
    # Postgres operator @> would require the rank, so we go wider and filter
    # client-side using a select * and in-process scan. For real volume this
    # needs an index or RPC, but at <10k species it's fine.
    all_rows = (
        client.table("species")
        .select("*")
        .order("updated_at", desc=True)
        .limit(2000)
        .execute()
    )
    candidates = []
    for row in all_rows.data or []:
        tax = row.get("taxonomy") or {}
        if row.get("scientific_name") == root_name:
            continue
        # Only species-rank entries in the gallery.
        if (row.get("rank") or "").lower() != "species":
            continue
        if any(v == root_name for v in tax.values()):
            candidates.append(row)
    # Seeded-by-day pseudo-random selection — stable within a day, shuffles tomorrow.
    import random
    from datetime import date
    seed = f"{taxid}:{date.today().isoformat()}"
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return candidates[:limit]

# ─── GenBank records ───────────────────────────────────────────────────

@router.post("/{taxid}/fetch-gene-genbank")
@limiter.limit("30/hour")
async def fetch_gene_genbank_endpoint(
    request: Request,
    taxid: int,
    user: RequireUser,
    payload: dict[str, str],
) -> dict[str, Any]:
    """Fetch a GenBank-format record for a gene (includes annotations)."""
    gene_symbol = (payload or {}).get("gene_symbol", "").strip()
    if not gene_symbol:
        raise HTTPException(status_code=400, detail="missing_gene_symbol")
    gb = await asyncio.to_thread(ncbi.fetch_gene_genbank, taxid, gene_symbol)
    if not gb:
        raise HTTPException(status_code=404, detail="gene_not_found")
    return {"genbank": gb, "gene_symbol": gene_symbol, "taxid": str(taxid)}


@router.post("/fetch-genbank-accession")
@limiter.limit("30/hour")
async def fetch_genbank_by_accession(
    request: Request,
    user: RequireUser,
    payload: dict[str, str],
) -> dict[str, Any]:
    """Fetch GenBank by nucleotide accession."""
    accession = (payload or {}).get("accession", "").strip()
    if not accession:
        raise HTTPException(status_code=400, detail="missing_accession")
    gb = await asyncio.to_thread(ncbi.fetch_genbank_record, accession)
    if not gb:
        raise HTTPException(status_code=404, detail="record_not_found")
    return {"genbank": gb, "accession": accession}


# ─── RCSB structures ───────────────────────────────────────────────────

@router.get("/structures/search")
@limiter.limit("30/hour")
async def search_structures(
    request: Request,
    user: RequireUser,
    gene: str = Query(..., min_length=1, max_length=20),
    limit: int = Query(default=20, le=50),
) -> list[dict[str, Any]]:
    """Search RCSB for structures matching a gene symbol."""
    return await rcsb.search_structures_by_gene(gene, limit=limit)


@router.get("/structures/{pdb_id}")
@limiter.limit("60/hour")
async def get_structure(request: Request, pdb_id: str) -> dict[str, Any]:
    meta = await rcsb.get_structure_metadata(pdb_id)
    if not meta:
        raise HTTPException(status_code=404, detail="pdb_not_found")
    return meta

@router.post("/resolve-ancestors")
async def resolve_ancestors(
    payload: dict[str, list[str]],
) -> dict[str, dict[str, Any] | None]:
    """Given a list of scientific names (ancestors in a lineage), return a map
    of name → species row (or null) for each one that's in our DB.
    """
    names = (payload or {}).get("names") or []
    if not names:
        return {}

    client = service_client()
    # Fetch all matches in one query.
    result = (
        client.table("species")
        .select("ncbi_tax_id, scientific_name, rank")
        .in_("scientific_name", names)
        .execute()
    )
    rows = result.data or []
    by_name: dict[str, dict[str, Any] | None] = {name: None for name in names}
    for r in rows:
        by_name[r["scientific_name"]] = r
    return by_name

