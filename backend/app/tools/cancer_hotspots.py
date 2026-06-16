"""Curated cancer mutation hotspot library.

Returns canonical hotspot lists for common tumour-suppressor and splicing
genes, sourced from peer-reviewed literature (citations included). The
output is pre-formatted to feed directly into:
  - the `mutation_map` tool's `hotspots` parameter
  - the `domain_diversity` tool's `regions` parameter

Coordinates follow the canonical human UniProt protein sequences:
  - TP53:   UniProt P04637  (393 aa, isoform 1)
  - SF3B1:  UniProt O75533  (1304 aa)
  - NOTCH1: UniProt P46531  (2555 aa)

This is a CONSERVATIVE list — the well-attested hotspots cited repeatedly
in review literature. For exhaustive catalogues see IARC TP53 R20 or COSMIC.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.tools.registry import ToolDef, ParamDef, register


HOTSPOT_LIBRARY: dict[str, dict[str, Any]] = {
    "TP53": {
        "uniprot": "P04637",
        "length_aa": 393,
        "domains": {
            "TAD1": (1, 42),
            "TAD2": (43, 63),
            "PRR": (64, 92),
            "DBD": (94, 312),
            "OD": (323, 356),
            "CTD": (363, 393),
        },
        "hotspots": [
            # The "classic six" — most frequent missense mutations across cancers.
            # Olivier et al. 2010 Cold Spring Harb Perspect Biol; IARC TP53 R20.
            {"pos": 175, "wt": "R", "common_mut": ["H"],
             "note": "Conformational class; most common across cancers"},
            {"pos": 245, "wt": "G", "common_mut": ["S", "D"],
             "note": "DNA-contact class"},
            {"pos": 248, "wt": "R", "common_mut": ["W", "Q"],
             "note": "DNA-contact class; most frequent in many cancers"},
            {"pos": 249, "wt": "R", "common_mut": ["S"],
             "note": "Conformational class; AFB1-associated hepatocellular carcinoma"},
            {"pos": 273, "wt": "R", "common_mut": ["H", "C"],
             "note": "DNA-contact class"},
            {"pos": 282, "wt": "R", "common_mut": ["W"],
             "note": "Conformational class"},
        ],
        "citation": (
            "Olivier M, Hollstein M, Hainaut P. 2010. TP53 mutations in human "
            "cancers: origins, consequences, and clinical use. Cold Spring "
            "Harb Perspect Biol 2(1):a001008."
        ),
    },
    "SF3B1": {
        "uniprot": "O75533",
        "length_aa": 1304,
        "domains": {
            # SF3B1 has ~22 HEAT repeats spanning 454-1304. Clinically relevant
            # hotspots cluster in HEAT5-9.
            "HEAT_repeats": (454, 1304),
            "HEAT5_9": (583, 781),
        },
        "hotspots": [
            # Cilloni et al. 2022 Cancers; Bak-Gordon & Manley 2025 RNA.
            {"pos": 625, "wt": "R", "common_mut": ["H", "C", "L"],
             "note": "Uveal melanoma hotspot; HEAT5"},
            {"pos": 662, "wt": "H", "common_mut": ["Q", "D"],
             "note": "MDS/CLL hotspot; HEAT6"},
            {"pos": 666, "wt": "K", "common_mut": ["E", "T", "Q", "R"],
             "note": "Most common SF3B1 hotspot in MDS; HEAT6"},
            {"pos": 700, "wt": "K", "common_mut": ["E"],
             "note": "Defining mutation of MDS-RS; HEAT7. >50% of MDS-RS cases."},
            {"pos": 740, "wt": "G", "common_mut": ["E", "R"],
             "note": "CLL hotspot; HEAT8"},
            {"pos": 742, "wt": "K", "common_mut": ["N"],
             "note": "CLL hotspot; HEAT8"},
        ],
        "citation": (
            "Cilloni D et al. 2022. SF3B1 mutations in hematological "
            "malignancies. Cancers 14(19):4927."
        ),
    },
    "NOTCH1": {
        "uniprot": "P46531",
        "length_aa": 2555,
        "domains": {
            "EGF_repeats": (20, 1426),
            "LNR": (1449, 1571),
            "HD": (1572, 1733),
            "TAD": (2129, 2466),
            "PEST": (2467, 2555),
        },
        "hotspots": [
            # Arruga et al. 2014 Leukemia; Gianfelici 2012 Haematologica.
            # In CLL, by far the most common NOTCH1 lesion is the frameshift
            # P2514Rfs*4 in the PEST domain.
            {"pos": 2514, "wt": "P", "common_mut": ["fs"],
             "note": "P2514Rfs*4 — most common NOTCH1 mutation in CLL; PEST domain"},
        ],
        "citation": (
            "Arruga F et al. 2014. Functional impact of NOTCH1 mutations in "
            "chronic lymphocytic leukemia. Leukemia 28(5):1060-1070."
        ),
    },
}


def _format_for_mutation_map(gene: str, include_residue: bool = True) -> str:
    """Format hotspots for the mutation_map tool's `hotspots` param."""
    entry = HOTSPOT_LIBRARY[gene]
    tokens: list[str] = []
    for h in entry["hotspots"]:
        if include_residue:
            tokens.append(f"{h['wt']}{h['pos']}")
        else:
            tokens.append(str(h["pos"]))
    return ", ".join(tokens)


def _build_regions_string(gene: str) -> str:
    """Format domain coordinates for the domain_diversity tool's `regions` param."""
    entry = HOTSPOT_LIBRARY[gene]
    parts: list[str] = []
    for name, (start, end) in entry["domains"].items():
        parts.append(f"{name}:{start}-{end}")
    return ", ".join(parts)


def _run_cancer_hotspots(params: dict[str, Any], label: str) -> dict[str, Any]:
    gene = (params.get("gene") or "").strip().upper()
    if not gene or gene == "ALL":
        # Return the full catalogue.
        catalogue_text = json.dumps(HOTSPOT_LIBRARY, indent=2)
        return {
            "summary": {"genes_available": list(HOTSPOT_LIBRARY)},
            "library": HOTSPOT_LIBRARY,
            "output_files": [{
                "name": f"{label}.full_catalogue.json",
                "content": catalogue_text,
                "kind": "text",
                "size": len(catalogue_text),
            }],
        }

    entry = HOTSPOT_LIBRARY.get(gene)
    if entry is None:
        raise ValueError(
            f"unknown gene {gene!r}; available: {', '.join(HOTSPOT_LIBRARY)}"
        )

    hotspot_string = _format_for_mutation_map(gene)
    regions_string = _build_regions_string(gene)

    return {
        "summary": {
            "gene": gene,
            "uniprot": entry["uniprot"],
            "length_aa": entry["length_aa"],
            "n_hotspots": len(entry["hotspots"]),
            "n_domains": len(entry["domains"]),
            "citation": entry["citation"],
        },
        "hotspots_for_mutation_map": hotspot_string,
        "regions_for_domain_diversity": regions_string,
        "entry": entry,
        "output_files": [
            {"name": f"{label}.{gene}.json",
             "content": json.dumps(entry, indent=2),
             "kind": "text", "size": 0},
            {"name": f"{label}.{gene}.hotspots.txt",
             "content": hotspot_string + "\n",
             "kind": "text", "size": len(hotspot_string) + 1},
            {"name": f"{label}.{gene}.regions.txt",
             "content": regions_string + "\n",
             "kind": "text", "size": len(regions_string) + 1},
        ],
    }


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str) -> dict[str, Any]:
    # No input file is needed — this tool returns curated reference data.
    # We deliberately do NOT call get_input_text here.
    gene = (params.get("gene") or "TP53").strip()
    label = f"cancer_hotspots_{gene}"
    return await asyncio.to_thread(_run_cancer_hotspots, params, label)


register(ToolDef(
    id="cancer_hotspots",
    label="Cancer hotspot library",
    description=(
        "Curated library of literature-attested cancer mutation hotspots for "
        "TP53, SF3B1, and NOTCH1. Output is pre-formatted for the "
        "mutation_map tool (hotspots) and the domain_diversity tool (regions). "
        "Citations included for each gene."
    ),
    input_kind="none",
    params=[
        ParamDef(
            name="gene",
            type="enum",
            label="Gene",
            default="TP53",
            options=["TP53", "SF3B1", "NOTCH1", "ALL"],
            help=(
                "Pick a gene to fetch its curated hotspot list and domain "
                "layout. ALL returns the full catalogue."
            ),
        ),
    ],
    run=run,
))
