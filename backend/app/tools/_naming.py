"""Naming helpers for tool output files.

Convention: `{tool}.{species_code}_{gene}_{kind}.{ext}`
Examples:
  miniprot.Agaz_TP53_protein.fasta
  exonerate.Atow_SF3B1_cds.fasta
  mafft.TP53_protein_alignment.fasta  (no species — aggregated across many)

The tool prefix lets you tell at a glance which method produced the file,
group all of one tool's outputs together when sorted, and write filter
patterns that match by tool.
"""
from __future__ import annotations

import re
from typing import Any


def resolve_file_with_species(file_id: str, project_id: str) -> dict[str, Any]:
    """Fetch a project file row joined with its species info.

    Returns a dict containing at minimum: id, name, storage_path, abs_path,
    species_id, species_name, species_code. species_* fields are None / 'Sp'
    if the file isn't linked to a species.
    """
    from app.files import store as filestore
    from app.supabase_client import service_client

    client = service_client()
    fres = (
        client.table("project_files")
        .select("id, storage_path, name, mime_hint, size, species_id")
        .eq("id", file_id)
        .maybe_single().execute()
    )
    if not fres or not fres.data:
        raise ValueError(f"file {file_id} not found")
    f = fres.data

    species_id = f.get("species_id")
    species_name: str | None = None
    if species_id:
        sres = (
            client.table("species")
            .select("id, scientific_name, common_name")
            .eq("id", species_id)
            .maybe_single().execute()
        )
        if sres and sres.data:
            species_name = sres.data.get("scientific_name")

    abs_path = str(filestore.absolute_path(project_id, f["storage_path"]))
    return {
        "id": f["id"],
        "name": f["name"],
        "storage_path": f["storage_path"],
        "abs_path": abs_path,
        "size": f.get("size"),
        "species_id": species_id,
        "species_name": species_name,
        "species_code": species_short_code(species_name) if species_name else None,
    }


def species_short_code(species_name: str | None, fallback: str = "Sp") -> str:
    """Derive a 4-character species code from a Latin binomial.

    Rule: first letter of genus (uppercase) + first 3 letters of species
    epithet (lowercase). For subspecies (3+ words), uses the species epithet,
    not the subspecies. For single-word names, takes first 4 letters.

    Examples:
      'Arctocephalus gazella'           -> 'Agaz'
      'Arctocephalus townsendi'         -> 'Atow'
      'Zalophus californianus'          -> 'Zcal'
      'Odobenus rosmarus divergens'     -> 'Oros'
      'Lobodon carcinophaga'            -> 'Lcar'
      None or ''                        -> fallback
    """
    if not species_name:
        return fallback

    # Normalize: collapse whitespace, strip
    name = re.sub(r"\s+", " ", species_name.strip())
    if not name:
        return fallback

    parts = name.split(" ")
    genus = parts[0]
    species = parts[1] if len(parts) > 1 else ""

    if not species:
        # Single-word name — take first 4 chars
        cleaned = re.sub(r"[^A-Za-z]", "", genus)
        return (cleaned[:4]).capitalize() or fallback

    g = re.sub(r"[^A-Za-z]", "", genus)[:1].upper()
    s = re.sub(r"[^A-Za-z]", "", species)[:3].lower()
    code = g + s
    return code if len(code) >= 2 else fallback


def sanitize_filename_part(s: str) -> str:
    """Strip filesystem-unsafe characters from a filename component."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def tool_output_name(
    tool: str,
    species_code: str | None,
    gene: str | None,
    kind: str,
    ext: str = "fasta",
) -> str:
    """Build a standardized output filename.

    Format: {tool}.{species_code}_{gene}_{kind}.{ext}
    Components are sanitized; empty parts are skipped cleanly.

    Examples:
      tool_output_name('miniprot', 'Agaz', 'TP53', 'protein', 'fasta')
        -> 'miniprot.Agaz_TP53_protein.fasta'
      tool_output_name('mafft', None, 'TP53', 'protein_alignment', 'fasta')
        -> 'mafft.TP53_protein_alignment.fasta'
      tool_output_name('miniprot', 'Agaz', 'TP53', '', 'gff3')
        -> 'miniprot.Agaz_TP53.gff3'
    """
    tool_part = sanitize_filename_part(tool) if tool else "tool"
    pieces: list[str] = []
    if species_code:
        pieces.append(sanitize_filename_part(species_code))
    if gene:
        pieces.append(sanitize_filename_part(gene))
    if kind:
        pieces.append(sanitize_filename_part(kind))
    middle = "_".join(p for p in pieces if p)
    if not middle:
        middle = "output"
    ext_part = sanitize_filename_part(ext) or "out"
    return f"{tool_part}.{middle}.{ext_part}"


# Known controlled vocabularies for inference / dropdowns.
# Update as new gene targets are added to projects.
KNOWN_GENES = {
    "TP53", "SF3B1", "NOTCH1", "BRCA1", "BRCA2", "PTEN", "MYC", "KRAS",
    "EGFR", "PIK3CA", "APC", "RB1", "VHL", "TP63", "TP73", "ATM", "TP53BP1",
}
KNOWN_KINDS = {
    "protein", "cds", "mrna", "region", "gff", "alignment",
    "tree", "genomic", "genbank", "structure",
}
KNOWN_TOOLS = {
    "miniprot", "exonerate", "liftoff", "mafft", "iqtree",
    "hyphy", "blast", "ncbi", "alphafold", "colabfold", "fastqc", "cutadapt",
    "mhcxgraph", "translate", "annotate",
}


def infer_file_metadata(name: str) -> dict[str, Any]:
    """Best-effort inference of {gene, kind, tool, species_code} from a filename.

    Used to backfill metadata for files that pre-date the structured naming
    convention. New files (post-patch17) carry this info in source_metadata
    directly; this helper fills in what's missing.

    Returns a dict with keys that were successfully inferred. Missing keys
    are simply absent (not None).

    Recognized patterns (in priority order):
      1. New convention:  {tool}.{SpCode}_{Gene}_{kind}.{ext}
         e.g. miniprot.Agaz_TP53_protein.fasta
      2. New aggregator:  {tool}.{Gene}_{kind}.{ext}      (no species)
         e.g. mafft.TP53_protein_alignment.fasta
      3. Old annotation:  {prefix}_{Gene}.{kind}.fasta
         e.g. GCA_040869175.1_genomic_TP53.protein.fasta
      4. NCBI fetch:      {Gene}_{taxid}.{kind}.fasta
         e.g. SF3B1_9704.protein.fasta
      5. Heuristic:       fallback keyword scan
    """
    out: dict[str, Any] = {}
    if not name:
        return out

    base = name.lower()
    base_orig = name

    # Helper: validate against known vocabularies. Returns the
    # canonicalized form (uppercase for gene, lowercase for kind/tool).
    def _maybe_gene(s: str) -> str | None:
        s_up = s.upper()
        if s_up in KNOWN_GENES:
            return s_up
        # Permissive: accept anything 2-12 chars matching gene-like pattern.
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", s_up):
            return s_up
        return None

    def _maybe_kind(s: str) -> str | None:
        s_low = s.lower()
        if s_low in KNOWN_KINDS:
            return s_low
        # Common aliases / suffixes
        if s_low in ("aa", "prot"):
            return "protein"
        if s_low in ("gbk", "genbank"):
            return "genbank"
        if s_low in ("aln", "msa"):
            return "alignment"
        return None

    def _maybe_tool(s: str) -> str | None:
        s_low = s.lower()
        if s_low in KNOWN_TOOLS:
            return s_low
        return None

    # Pattern 1: tool.SpCode_Gene_kind.ext   (e.g. miniprot.Agaz_TP53_protein.fasta)
    m = re.fullmatch(
        r"([a-z][a-z0-9_]*)\.([A-Z][a-z]{1,5})_([A-Z][A-Z0-9]+)_([a-z_]+)\.([a-z0-9]+)",
        base_orig,
    )
    if m:
        tool, sp, gene, kind, _ext = m.groups()
        if _maybe_tool(tool):
            out["tool"] = tool.lower()
        out["species_code"] = sp
        if g := _maybe_gene(gene):
            out["gene"] = g
        if k := _maybe_kind(kind):
            out["kind"] = k
        # Fall through to heuristics for any remaining missing fields.

    # Pattern 2: tool.Gene_kind.ext   (aggregator, no species)
    if "gene" not in out or "kind" not in out:
        m = re.fullmatch(
            r"([a-z][a-z0-9_]*)\.([A-Z][A-Z0-9]+)_([a-z_]+)\.([a-z0-9]+)",
            base_orig,
        )
        if m:
            tool, gene, kind, _ext = m.groups()
            if "tool" not in out and _maybe_tool(tool):
                out["tool"] = tool.lower()
            if "gene" not in out and (g := _maybe_gene(gene)):
                out["gene"] = g
            if "kind" not in out and (k := _maybe_kind(kind)):
                out["kind"] = k

    # Pattern 3: ...{Gene}.{kind}.fasta  (old annotation outputs)
    m = re.search(
        r"_([A-Z][A-Z0-9]{1,11})\.(protein|cds|mrna|region|genomic|gff3?)\.\w+$",
        base_orig,
    )
    if m:
        gene, kind = m.groups()
        if g := _maybe_gene(gene):
            out["gene"] = g
        if k := _maybe_kind(kind):
            out["kind"] = k

    # Pattern 4: NCBI fetch: Gene_taxid.kind.fasta
    m = re.fullmatch(
        r"([A-Z][A-Z0-9]+)_(\d{3,7})\.(protein|cds|mrna|genomic)\.fasta",
        base_orig,
    )
    if m:
        gene, _taxid, kind = m.groups()
        if "gene" not in out and (g := _maybe_gene(gene)):
            out["gene"] = g
        if "kind" not in out and (k := _maybe_kind(kind)):
            out["kind"] = k
        out.setdefault("tool", "ncbi")

    # Pattern 5: GenBank record
    m = re.fullmatch(r"([A-Z][A-Z0-9]+)_(\d{3,7})\.gb", base_orig)
    if m:
        gene, _taxid = m.groups()
        if "gene" not in out and (g := _maybe_gene(gene)):
            out["gene"] = g
        out.setdefault("kind", "genbank")
        out.setdefault("tool", "ncbi")

    # Pattern 6: Heuristic kind detection if still unknown
    if "kind" not in out:
        if re.search(r"\b(protein|prot|\.aa\.)", base):
            out["kind"] = "protein"
        elif re.search(r"\b(cds)\b", base):
            out["kind"] = "cds"
        elif re.search(r"\b(mrna|transcript)\b", base):
            out["kind"] = "mrna"
        elif re.search(r"\b(genomic|region|chromosomal)\b", base):
            out["kind"] = "region"
        elif re.search(r"\b(alignment|aligned|aln|msa|mafft)\b", base):
            out["kind"] = "alignment"
        elif base.endswith((".gff3", ".gff")):
            out["kind"] = "gff"
        elif base.endswith((".nwk", ".tree", ".treefile")):
            out["kind"] = "tree"

    # Pattern 7: Heuristic gene detection — find an uppercase token from known list
    if "gene" not in out:
        tokens = re.findall(r"\b([A-Z][A-Z0-9]{1,11})\b", base_orig)
        for t in tokens:
            if t in KNOWN_GENES:
                out["gene"] = t
                break

    return out


def enrich_file_metadata(file_row: dict[str, Any]) -> dict[str, Any]:
    """Augment a project_file row with inferred metadata for any keys that
    aren't already present in source_metadata. Returns a NEW dict; doesn't
    mutate the input. Fields added to source_metadata:
      gene, kind, tool, species_code  (any subset that could be inferred)
    Plus a top-level 'inferred_meta' dict for UI badging.
    """
    out = dict(file_row)
    meta = dict(out.get("source_metadata") or {})
    inferred = infer_file_metadata(out.get("name", ""))

    # Only fill keys not already present in source_metadata
    for k, v in inferred.items():
        if k not in meta or not meta[k]:
            meta[k] = v

    out["source_metadata"] = meta
    # Surface inferred-only fields for UI styling (e.g., italic for inferred)
    out["_inferred"] = inferred
    return out
