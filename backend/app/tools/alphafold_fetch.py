"""Fetch a pre-computed structure from the AlphaFold Protein Structure Database.

AlphaFold DB has predicted structures for ~every protein in UniProt's reference
proteomes — including most pinnipeds we care about. Fetching is free, instant,
higher quality than what we'd run locally (full pipeline, AMBER-relaxed).

Usage from frontend:
  - Pick "AlphaFold DB fetch" tool
  - Provide either a UniProt accession (e.g. Q15014) OR
    a species TaxID + gene symbol (we resolve to UniProt internally)

Output: a .pdb file saved as a project_file with mime_hint='pdb', source='ncbi'
(close enough — there's no 'alphafold' enum value, source_metadata records it).
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register


def _uniprot_search_by_gene(gene_symbol: str, taxid: int) -> dict[str, Any] | None:
    """Find a UniProt entry by gene symbol + organism TaxID.
    Returns the first reviewed (Swiss-Prot) hit if any, else the first
    unreviewed (TrEMBL) hit."""
    query = f"(gene:{gene_symbol}) AND (organism_id:{taxid})"
    params = urllib.parse.urlencode({
        "query": query,
        "format": "json",
        "fields": "accession,id,reviewed,gene_names,organism_name,length",
        "size": "10",
    })
    url = f"https://rest.uniprot.org/uniprotkb/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "sentinel-bio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"UniProt search failed: {e}")
    results = data.get("results", [])
    if not results:
        return None
    # Prefer reviewed (Swiss-Prot) over unreviewed (TrEMBL)
    reviewed = [r for r in results if r.get("entryType", "").lower().startswith("uniprotkb reviewed")]
    return (reviewed or results)[0]


def _fetch_alphafold_pdb(uniprot_acc: str) -> tuple[str, dict[str, Any]]:
    """Download the predicted structure PDB from AlphaFold DB.
    Returns (pdb_text, metadata)."""
    # AlphaFold DB metadata endpoint
    meta_url = f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_acc}"
    req = urllib.request.Request(meta_url, headers={"User-Agent": "sentinel-bio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            meta_list = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(f"AlphaFold DB has no prediction for {uniprot_acc}")
        raise RuntimeError(f"AlphaFold DB metadata failed ({e.code}): {e}")
    except Exception as e:
        raise RuntimeError(f"AlphaFold DB metadata failed: {e}")

    if not meta_list:
        raise RuntimeError(f"AlphaFold DB returned no metadata for {uniprot_acc}")
    meta = meta_list[0]
    pdb_url = meta.get("pdbUrl")
    if not pdb_url:
        raise RuntimeError(f"AlphaFold DB metadata has no pdbUrl for {uniprot_acc}")

    req = urllib.request.Request(pdb_url, headers={"User-Agent": "sentinel-bio/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        pdb_text = r.read().decode("utf-8", errors="replace")
    if not pdb_text.strip():
        raise RuntimeError(f"AlphaFold DB returned empty PDB for {uniprot_acc}")
    return pdb_text, meta


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    uniprot_acc = (params.get("uniprot_accession") or "").strip()
    gene_symbol = (params.get("gene_symbol") or "").strip()
    taxid = int(params.get("taxid") or 0)

    if not uniprot_acc:
        if not gene_symbol or not taxid:
            raise ValueError(
                "Provide either uniprot_accession, OR both gene_symbol + taxid"
            )
        hit = await asyncio.to_thread(_uniprot_search_by_gene, gene_symbol, taxid)
        if not hit:
            raise ValueError(
                f"UniProt has no entry for gene={gene_symbol} taxid={taxid}. "
                f"Use the Exonerate annotation tool to predict the protein from "
                f"a related species' genome instead."
            )
        uniprot_acc = hit.get("primaryAccession", "")
        if not uniprot_acc:
            raise ValueError(f"UniProt hit missing accession: {hit}")

    pdb_text, meta = await asyncio.to_thread(_fetch_alphafold_pdb, uniprot_acc)

    filename = f"AF-{uniprot_acc}-{(gene_symbol or 'protein')}.pdb"
    return {
        "uniprot_accession": uniprot_acc,
        "gene_symbol": gene_symbol or None,
        "taxid": taxid or None,
        "alphafold_meta": {
            "organismScientificName": meta.get("organismScientificName"),
            "uniprotDescription": meta.get("uniprotDescription"),
            "uniprotSequence": meta.get("uniprotSequence", "")[:200],
            "uniprotStart": meta.get("uniprotStart"),
            "uniprotEnd": meta.get("uniprotEnd"),
            "modelCreatedDate": meta.get("modelCreatedDate"),
            "latestVersion": meta.get("latestVersion"),
        },
        "output_files": [
            {
                "name": filename,
                "content": pdb_text,
                "kind": "fasta",  # falls through to 'other' mime_hint via worker's map
                "size": len(pdb_text),
            }
        ],
    }


register(ToolDef(
    id="alphafold_fetch",
    label="AlphaFold DB fetch (structure)",
    description=(
        "Download a pre-computed protein structure from the AlphaFold Database. "
        "Free, instant, high quality. Available for most UniProt-mapped species "
        "(Z. californianus and human SF3B1/TP53 are there; A. gazella probably isn't)."
    ),
    input_kind="none",
    params=[
        ParamDef(name="uniprot_accession", type="string", label="UniProt accession",
                 default="",
                 help="e.g. O75533 (human SF3B1). If empty, we look it up from gene+taxid."),
        ParamDef(name="gene_symbol", type="string", label="Gene symbol",
                 default="", help="e.g. SF3B1, TP53."),
        ParamDef(name="taxid", type="int", label="NCBI TaxID",
                 default=0,
                 help="9606=human, 9704=Z. californianus, 9711=A. gazella, "
                      "9712=A. townsendi."),
    ],
    run=run,
))
