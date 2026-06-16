"""NCBI Entrez + Datasets wrappers.

We use Biopython's Entrez module (simpler than raw HTTP and handles XML parsing).
For higher-throughput / modern replacement, NCBI's Datasets REST API is nicer
but less complete for pure taxonomy. Biopython for now.

Polite use:
- Always set `Entrez.email` and `Entrez.tool`
- Stay well under 3 req/s (NCBI's free tier)
- For bursts, get an API key and add `Entrez.api_key` (10 req/s)
"""
from __future__ import annotations

from typing import Any
import re
from Bio import Entrez

Entrez.email = "sentinel-bio@example.com"  # TODO: move to config
Entrez.tool = "sentinel-bio"


def resolve_name_to_taxid(name: str) -> int | None:
    """Given a scientific or common name, find the NCBI TaxID."""
    handle = Entrez.esearch(db="taxonomy", term=name)
    record = Entrez.read(handle)
    handle.close()
    ids = record.get("IdList", [])
    if not ids:
        return None
    return int(ids[0])

def fetch_taxonomy(taxid: int) -> dict[str, Any] | None:
    """Fetch the taxonomy record for a TaxID.

    Returns a dict with:
      - scientific_name: str
      - rank: str
      - common_name: str | None
      - lineage: list of {rank, name, taxid} — ordered root → leaf, self-exclusive
      - other_names: list[str]
    """
    handle = Entrez.efetch(db="taxonomy", id=str(taxid), retmode="xml")
    records = Entrez.read(handle)
    handle.close()
    if not records:
        return None
    rec = records[0]

    # LineageEx is the rich form — rank, name, AND TaxID for every ancestor.
    lineage: list[dict[str, Any]] = []
    for lin in rec.get("LineageEx", []):
        rank = (lin.get("Rank") or "").lower()
        name = lin.get("ScientificName")
        lin_taxid = lin.get("TaxId")
        if not name:
            continue
        try:
            lin_taxid_int = int(lin_taxid) if lin_taxid else None
        except (TypeError, ValueError):
            lin_taxid_int = None
        if rank and rank != "no rank":
            lineage.append({
                "rank": rank,
                "name": name,
                "taxid": lin_taxid_int,
            })

    # Convenience map for backwards compat.
    taxonomy_map: dict[str, str] = {e["rank"]: e["name"] for e in lineage}

    self_rank = (rec.get("Rank") or "").lower()
    if self_rank and self_rank != "no rank":
        taxonomy_map[self_rank] = rec.get("ScientificName", "")

    other_names_block = rec.get("OtherNames", {}) or {}
    common_names: list[str] = []
    for key in ("GenbankCommonName", "CommonName"):
        val = other_names_block.get(key)
        if isinstance(val, str):
            common_names.append(val)
        elif isinstance(val, list):
            common_names.extend([str(v) for v in val])

    return {
        "taxid": taxid,
        "scientific_name": rec.get("ScientificName", ""),
        "rank": self_rank,
        "common_name": common_names[0] if common_names else None,
        "common_names": common_names,
        "lineage": lineage,          # rich form with taxids
        "taxonomy_map": taxonomy_map, # flat {rank: name} for existing code
        "division": rec.get("Division"),
        "genetic_code": (rec.get("GeneticCode") or {}).get("GCName"),
    }

def count_nucleotide_records(taxid: int) -> int:
    """How many nucleotide sequences NCBI has for this taxon? Rough indicator of research coverage."""
    handle = Entrez.esearch(db="nucleotide", term=f"txid{taxid}[Organism:exp]", retmax=0)
    record = Entrez.read(handle)
    handle.close()
    return int(record.get("Count", 0))


def count_protein_records(taxid: int) -> int:
    handle = Entrez.esearch(db="protein", term=f"txid{taxid}[Organism:exp]", retmax=0)
    record = Entrez.read(handle)
    handle.close()
    return int(record.get("Count", 0))


def has_genome_assembly(taxid: int) -> bool:
    """Quick check: does NCBI have any assembly for this taxon?"""
    handle = Entrez.esearch(db="assembly", term=f"txid{taxid}[Organism:exp]", retmax=0)
    record = Entrez.read(handle)
    handle.close()
    return int(record.get("Count", 0)) > 0

# ─── Recursive taxonomy + assembly lookup ────────────────────────────

RANK_ORDER = [
    "domain",
    "realm", "subrealm",
    "superkingdom", "kingdom", "subkingdom", "infrakingdom",
    "superphylum", "phylum", "subphylum", "infraphylum", "microphylum",
    "superclass", "class", "subclass", "infraclass", "parvclass",
    "superorder", "order", "suborder", "infraorder", "parvorder",
    "superfamily", "family", "subfamily",
    "supertribe", "tribe", "subtribe",
    "genus", "subgenus",
    "section", "subsection", "series",
    "species group", "species subgroup",
    "species", "subspecies", "forma", "varietas",
]

def list_descendant_taxids(
    root_taxid: int,
    stop_rank: str = "species",
    max_results: int = 10000,
) -> list[int]:
    """Walk the subtree of `root_taxid`, keeping taxa whose rank is at or ABOVE
    `stop_rank` (higher up the tree). The root itself is always included
    regardless of its own rank.

    Example: stop_rank="genus" with root Pinnipedia returns Pinnipedia +
    Otariidae, Phocidae, their subfamilies, and the genera. No species.
    """
    if stop_rank not in RANK_ORDER:
        raise ValueError(f"unknown rank: {stop_rank}")

    stop_index = RANK_ORDER.index(stop_rank)
    acceptable = set(RANK_ORDER[: stop_index + 1])

    handle = Entrez.esearch(
        db="taxonomy",
        term=f"txid{root_taxid}[Subtree]",
        retmax=max_results,
    )
    record = Entrez.read(handle)
    handle.close()
    ids = [int(i) for i in record.get("IdList", [])]
    if not ids:
        return []

    kept: list[int] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        h = Entrez.efetch(db="taxonomy", id=",".join(str(x) for x in chunk), retmode="xml")
        records = Entrez.read(h)
        h.close()
        for rec in records:
            rank = (rec.get("Rank") or "").lower()
            try:
                taxid_val = rec.attributes.get("TaxId") if hasattr(rec, "attributes") else None
                if taxid_val is None:
                    taxid_val = rec.get("TaxId")
                tid = int(taxid_val)
            except (TypeError, ValueError):
                continue

            # Always include the root, regardless of its rank.
            if tid == root_taxid:
                kept.append(tid)
                continue

            if rank in acceptable:
                kept.append(tid)

    return kept

def fetch_best_assembly(taxid: int) -> dict[str, str] | None:
    """Return the 'best' genome assembly accession+links for this TaxID.

    Ranking:
      1. reference genome
      2. representative genome
      3. anything else
    """
    try:
        handle = Entrez.esearch(
            db="assembly",
            term=f"txid{taxid}[Organism:exp]",
            retmax=20,
        )
        record = Entrez.read(handle)
        handle.close()
        ids = record.get("IdList", [])
        if not ids:
            # No Assembly-DB entry. For viruses / organelle-only taxa the genome
            # lives in nuccore; surface the best complete-genome record so the
            # species card reflects that a genome is in fact available.
            viral = list_viral_genomes(taxid, limit=1)
            if viral:
                v = viral[0]
                return {
                    "accession": v["accession"],
                    "name": v["name"],
                    "refseq_category": v.get("category", ""),
                    "ftp": "",
                    "source_db": "nuccore",
                    "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{v['accession']}",
                }
            return None

        handle = Entrez.esummary(db="assembly", id=",".join(ids))
        summary = Entrez.read(handle)
        handle.close()
        assemblies = summary.get("DocumentSummarySet", {}).get("DocumentSummary", [])
        if not assemblies:
            return None

        def score(a):
            cat = (a.get("RefSeq_category") or "").lower()
            if cat == "reference genome":
                return 0
            if cat == "representative genome":
                return 1
            return 2

        best = min(assemblies, key=score)
        accession = best.get("AssemblyAccession", "")
        ftp = best.get("FtpPath_RefSeq") or best.get("FtpPath_GenBank") or ""
        return {
            "accession": accession,
            "name": best.get("AssemblyName", ""),
            "refseq_category": best.get("RefSeq_category", ""),
            "ftp": ftp,
            "url": (
                f"https://www.ncbi.nlm.nih.gov/datasets/genome/{accession}/"
                if accession else ""
            ),
        }
    except Exception:
        return None

def fetch_assembly_count(taxid: int) -> int:
    """Count of genome assemblies associated with this taxon (including descendants).

    Uses Entrez against the 'assembly' db with a taxonomy query.
    """
    from Bio import Entrez

    try:
        handle = Entrez.esearch(
            db="assembly",
            term=f"txid{taxid}[Organism:exp]",
            retmax=0,
        )
        result = Entrez.read(handle)
        handle.close()
        return int(result.get("Count", 0))
    except Exception:
        return 0


def list_assemblies(taxid: int, limit: int = 100) -> list[dict[str, Any]]:
    """List all genome assemblies for a taxon (direct assignment, not descendants).

    Returns assemblies sorted: RefSeq (GCF) first, then GenBank (GCA), by date desc.
    Each entry: {accession, name, category, level, submitter, submission_date,
                 size_mb, ftp_url, type}
    """
    from Bio import Entrez

    try:
        search = Entrez.esearch(
            db="assembly",
            term=f"txid{taxid}[Organism:noexp]",
            retmax=limit,
        )
        search_result = Entrez.read(search)
        search.close()

        ids = search_result.get("IdList", [])
        if not ids:
            # No genome assembly. This is the normal case for viruses and many
            # organelle-only taxa, whose genomes live in nuccore rather than the
            # Assembly DB. Fall back to a nuccore "complete genome" search so the
            # picker isn't empty for e.g. Mayaro virus.
            return list_viral_genomes(taxid, limit=limit)

        summary = Entrez.esummary(db="assembly", id=",".join(ids))
        summary_result = Entrez.read(summary)
        summary.close()

        docs = summary_result.get("DocumentSummarySet", {}).get("DocumentSummary", [])

        assemblies = []
        for doc in docs:
            accession = doc.get("AssemblyAccession", "")
            if not accession:
                continue

            ftp_path = doc.get("FtpPath_RefSeq") or doc.get("FtpPath_GenBank") or ""
            meta = doc.get("Meta", "")

            # Extract genome size if present in Meta XML-like field.
            size_mb = None
            if "total_length" in meta:
                match = re.search(r'<Stat[^>]*category="total_length"[^>]*>(\d+)</Stat>', meta)
                if match:
                    size_mb = round(int(match.group(1)) / 1_000_000, 1)

            assemblies.append({
                "accession": accession,
                "name": doc.get("AssemblyName", ""),
                "category": doc.get("RefSeq_category", ""),  # representative / reference / na
                "level": doc.get("AssemblyStatus", ""),      # complete / chromosome / scaffold / contig
                "submitter": doc.get("SubmitterOrganization", ""),
                "submission_date": doc.get("AsmReleaseDate_GenBank", "") or doc.get("SubmissionDate", ""),
                "size_mb": size_mb,
                "ftp_url": ftp_path,
                "type": "GCF" if accession.startswith("GCF_") else "GCA" if accession.startswith("GCA_") else "other",
            })

        # Sort: GCF first, then by date desc.
        def sort_key(a: dict[str, Any]) -> tuple:
            type_priority = 0 if a["type"] == "GCF" else 1 if a["type"] == "GCA" else 2
            date = a.get("submission_date", "")
            return (type_priority, -len(date), date)  # desc date via length+lexical

        assemblies.sort(key=sort_key, reverse=True)
        assemblies.sort(key=lambda a: 0 if a["type"] == "GCF" else 1 if a["type"] == "GCA" else 2)
        return assemblies
    except Exception as e:
        print(f"[ncbi] list_assemblies failed for {taxid}: {e}")
        return []


def _division_for_taxid(taxid: int) -> str | None:
    """Return the NCBI taxonomy Division (e.g. 'Viruses', 'Mammals', 'Bacteria')
    for a taxon. Used to decide whether to look in the Assembly DB (eukaryotes /
    prokaryotes) or nuccore (viruses, organelles)."""
    try:
        tax = fetch_taxonomy(taxid)
        return (tax or {}).get("division")
    except Exception:
        return None


def list_viral_genomes(taxid: int, limit: int = 100) -> list[dict[str, Any]]:
    """List 'complete genome' nucleotide records for a taxon from nuccore.

    NCBI's Assembly database barely covers viruses — most viral genomes live in
    nuccore as RefSeq (NC_*) or GenBank records, not as GCF/GCA assemblies. This
    is why an Assembly-only search returns nothing for e.g. Mayaro virus while it
    happily returns assemblies for eukaryotes. We search nuccore directly and
    shape the results to look like the assembly rows the frontend expects, so the
    AssemblyPicker can render them with no changes.

    RefSeq (NC_) records are surfaced first, mirroring the GCF-first ordering.
    """
    from Bio import Entrez

    try:
        # "complete genome" catches the canonical full-length records; the
        # txid[Organism:exp] scopes to the taxon and its descendants (e.g. a
        # virus species with several segments / strains).
        search = Entrez.esearch(
            db="nuccore",
            term=(
                f"txid{taxid}[Organism:exp] "
                f"AND (complete genome[Title] OR complete sequence[Title]) "
                f"AND (refseq[Filter] OR genbank[Filter])"
            ),
            retmax=limit,
            sort="SLEN",  # longest first ~ most complete
        )
        result = Entrez.read(search)
        search.close()
        ids = result.get("IdList", [])
        if not ids:
            return []

        summary = Entrez.esummary(db="nuccore", id=",".join(ids))
        docs = Entrez.read(summary)
        summary.close()

        genomes: list[dict[str, Any]] = []
        for doc in docs:
            acc = str(doc.get("AccessionVersion") or doc.get("Caption") or "")
            if not acc:
                continue
            length = int(doc.get("Length", 0) or 0)
            title = str(doc.get("Title", ""))
            is_refseq = acc.startswith(("NC_", "NG_", "AC_"))
            create_date = str(doc.get("CreateDate", "") or "")
            genomes.append({
                "accession": acc,
                "name": title[:120],
                "category": "reference genome" if is_refseq else "",
                "level": "complete genome",
                "submitter": "",
                "submission_date": create_date,
                "size_mb": round(length / 1_000_000, 4) if length else None,
                "ftp_url": "",  # nuccore records are fetched via efetch, not FTP
                # 'type' drives the picker's filter pills + ordering. We use a
                # distinct 'NUC' type so the frontend can tell these apart from
                # GCF/GCA assemblies and route the fetch through the right path.
                "type": "NUC",
                "source_db": "nuccore",
                "is_refseq": is_refseq,
            })

        # RefSeq (NC_) first, then by length desc (already sorted by SLEN but the
        # secondary date/refseq sort makes intent explicit).
        genomes.sort(key=lambda g: (0 if g["is_refseq"] else 1, -(g["size_mb"] or 0)))
        return genomes
    except Exception as e:
        print(f"[ncbi] list_viral_genomes failed for {taxid}: {e}")
        return []


def fetch_nuccore_fasta(accession: str) -> str | None:
    """Fetch a full nucleotide record as FASTA text (for viral/organelle genomes
    that have no Assembly FTP path). Returns FASTA text or None."""
    from Bio import Entrez

    try:
        handle = Entrez.efetch(
            db="nuccore", id=accession, rettype="fasta", retmode="text",
        )
        fasta = handle.read()
        handle.close()
        return fasta if fasta.strip() else None
    except Exception as e:
        print(f"[ncbi] fetch_nuccore_fasta failed for {accession}: {e}")
        return None


def list_species_members(
    root_taxid: int,
    max_results: int = 500,
) -> list[dict[str, Any]]:
    """List species-rank descendants of `root_taxid` with their scientific names.

    Unlike `list_descendant_taxids` (which returns bare ints and can include
    higher ranks), this returns species-rank taxa only, each as
    {taxid, scientific_name, rank}, ready to drive a per-member gene fetch.

    Used by the family/genus gene pipeline to enumerate "all members of family Y".
    """
    handle = Entrez.esearch(
        db="taxonomy",
        term=f"txid{root_taxid}[Subtree] AND species[Rank]",
        retmax=max_results,
    )
    record = Entrez.read(handle)
    handle.close()
    ids = [int(i) for i in record.get("IdList", [])]
    if not ids:
        return []

    members: list[dict[str, Any]] = []
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        h = Entrez.efetch(
            db="taxonomy", id=",".join(str(x) for x in chunk), retmode="xml",
        )
        recs = Entrez.read(h)
        h.close()
        for rec in recs:
            rank = (rec.get("Rank") or "").lower()
            if rank != "species":
                continue
            try:
                tid = int(rec.attributes.get("TaxId")) if hasattr(rec, "attributes") and rec.attributes.get("TaxId") else int(rec.get("TaxId"))
            except (TypeError, ValueError):
                continue
            members.append({
                "taxid": tid,
                "scientific_name": rec.get("ScientificName", ""),
                "rank": rank,
            })
    return members


def fetch_assembly_fasta_url(accession: str) -> str | None:
    """Given an assembly accession (GCF_000001.1 style), return the FTP URL to
    the genomic FASTA file. NCBI stores these as {accession}_{name}_genomic.fna.gz.
    """
    from Bio import Entrez

    try:
        search = Entrez.esearch(db="assembly", term=accession, retmax=1)
        result = Entrez.read(search)
        search.close()
        ids = result.get("IdList", [])
        if not ids:
            return None

        summary = Entrez.esummary(db="assembly", id=ids[0])
        s = Entrez.read(summary)
        summary.close()

        docs = s.get("DocumentSummarySet", {}).get("DocumentSummary", [])
        if not docs:
            return None

        ftp = docs[0].get("FtpPath_RefSeq") or docs[0].get("FtpPath_GenBank")
        if not ftp:
            return None

        name = docs[0].get("AssemblyName", "")
        acc = docs[0].get("AssemblyAccession", accession)
        # FTP convention: {ftp}/{acc}_{name}_genomic.fna.gz
        # But the filename uses underscore-joined accession + name.
        basename = f"{acc}_{name}".replace(" ", "_")
        # Convert FTP URL to HTTPS for browser-friendly fetching.
        https_ftp = ftp.replace("ftp://", "https://")
        return f"{https_ftp}/{basename}_genomic.fna.gz"
    except Exception as e:
        print(f"[ncbi] fetch_assembly_fasta_url failed for {accession}: {e}")
        return None


def _find_best_mrna_accession(
    taxid: int,
    gene_symbol: str,
) -> tuple[str, str] | None:
    """Find the best RefSeq mRNA accession for a gene.

    Returns (accession, description) or None.

    Strategy:
    1. Search nuccore directly for mRNA records matching gene + taxid.
    2. Prefer NM_ (curated RefSeq) over XM_ (predicted) over anything else.
    3. Among multiple NM_/XM_ hits, prefer the longest (most complete).
    4. Never return genomic DNA accessions (NC_, NW_, NT_, NZ_).
    """
    from Bio import Entrez

    # Direct mRNA search — avoids the elink fallback that returns genomic records.
    search = Entrez.esearch(
        db="nuccore",
        term=(
            f"{gene_symbol}[Gene Name] "
            f"AND txid{taxid}[Organism:noexp] "
            f"AND mRNA[Filter] "
            f"AND RefSeq[Filter]"
        ),
        retmax=20,
        usehistory="y",
    )
    result = Entrez.read(search)
    search.close()
    ids = result.get("IdList", [])

    if not ids:
        # Widen: try without RefSeq filter (catches some species with no curated mRNAs)
        search2 = Entrez.esearch(
            db="nuccore",
            term=(
                f"{gene_symbol}[Gene Name] "
                f"AND txid{taxid}[Organism:noexp] "
                f"AND mRNA[Filter]"
            ),
            retmax=20,
        )
        result2 = Entrez.read(search2)
        search2.close()
        ids = result2.get("IdList", [])

    if not ids:
        return None

    # Fetch summaries to get accession + sequence length + title.
    summary_handle = Entrez.esummary(db="nuccore", id=",".join(ids))
    summaries = Entrez.read(summary_handle)
    summary_handle.close()

    # Score each candidate: NM_ = 2, XM_ = 1, anything else = 0
    # Tiebreak: longer sequence wins (more complete transcript).
    def _score(s: dict) -> tuple[int, int]:
        acc = str(s.get("Caption", "") or s.get("AccessionVersion", ""))
        if acc.startswith("NM_"):
            return (2, int(s.get("Length", 0)))
        if acc.startswith("XM_"):
            return (1, int(s.get("Length", 0)))
        # Reject genomic accessions outright.
        for bad_prefix in ("NC_", "NW_", "NT_", "NZ_", "NG_"):
            if acc.startswith(bad_prefix):
                return (-1, 0)
        return (0, int(s.get("Length", 0)))

    valid = [s for s in summaries if _score(s)[0] >= 0]
    if not valid:
        return None

    best = max(valid, key=_score)
    acc = str(best.get("AccessionVersion") or best.get("Caption") or "")
    title = str(best.get("Title", ""))
    return acc, title


def fetch_genbank_record(accession: str) -> str | None:
    """Fetch a GenBank-format record by accession. Returns raw .gb text."""
    from Bio import Entrez
    try:
        handle = Entrez.efetch(
            db="nuccore", id=accession, rettype="gbwithparts", retmode="text",
        )
        text = handle.read()
        handle.close()
        return text if text.strip() else None
    except Exception as e:
        print(f"[ncbi] fetch_genbank_record failed for {accession}: {e}")
        return None


def fetch_gene_fasta(taxid: int, gene_symbol: str) -> str | None:
    """Fetch the best RefSeq mRNA FASTA for a gene. Returns FASTA text or None."""
    from Bio import Entrez

    try:
        hit = _find_best_mrna_accession(taxid, gene_symbol)
        if not hit:
            return None
        accession, title = hit
        print(f"[ncbi] fetch_gene_fasta: using {accession} ({title[:80]})")

        handle = Entrez.efetch(
            db="nuccore", id=accession, rettype="fasta", retmode="text",
        )
        fasta = handle.read()
        handle.close()
        return fasta if fasta.strip() else None
    except Exception as e:
        print(f"[ncbi] fetch_gene_fasta failed for {gene_symbol}/{taxid}: {e}")
        return None


def fetch_gene_fasta_by_id(gene_id: str) -> str | None:
    """Fetch a representative sequence FASTA for an NCBI Gene ID.

    A Gene ID (e.g. "7157" for human TP53) isn't itself a sequence — it links to
    several. We prefer the RefSeq RNA (elink gene→nuccore_refseqrna); if there's
    none (common for viral/prokaryote genes), fall back to the RefSeq protein
    (gene→protein_refseq), then to any linked nuccore record. Returns FASTA text
    or None.
    """
    from Bio import Entrez

    gid = str(gene_id).strip()
    if not gid.isdigit():
        # Allow passing a "GeneID:NNNN" or symbol-like value gracefully.
        m = re.search(r"(\d+)", gid)
        if not m:
            print(f"[ncbi] fetch_gene_fasta_by_id: '{gene_id}' is not a numeric Gene ID")
            return None
        gid = m.group(1)

    link_targets = [
        ("nuccore", "gene_nuccore_refseqrna", "fasta"),
        ("protein", "gene_protein_refseq", "fasta"),
        ("nuccore", "gene_nuccore_refseqgene", "fasta"),
        ("nuccore", "gene_nuccore", "fasta"),
    ]
    try:
        for db, linkname, rettype in link_targets:
            try:
                handle = Entrez.elink(dbfrom="gene", db=db, id=gid, linkname=linkname)
                rec = Entrez.read(handle)
                handle.close()
            except Exception:
                continue
            ids: list[str] = []
            for ls in rec:
                for lsdb in ls.get("LinkSetDb", []):
                    for link in lsdb.get("Link", []):
                        if link.get("Id"):
                            ids.append(link["Id"])
            if not ids:
                continue
            acc = ids[0]
            print(f"[ncbi] fetch_gene_fasta_by_id: gene {gid} → {db} {acc} via {linkname}")
            h = Entrez.efetch(db=db, id=acc, rettype=rettype, retmode="text")
            fasta = h.read()
            h.close()
            if fasta and fasta.strip():
                return fasta
        print(f"[ncbi] fetch_gene_fasta_by_id: no linked sequence for gene {gid}")
        return None
    except Exception as e:
        print(f"[ncbi] fetch_gene_fasta_by_id failed for {gene_id}: {e}")
        return None


def fetch_gene_genbank(taxid: int, gene_symbol: str) -> str | None:
    """Fetch the best RefSeq mRNA record as annotated GenBank text."""
    from Bio import Entrez

    try:
        hit = _find_best_mrna_accession(taxid, gene_symbol)
        if not hit:
            return None
        accession, title = hit
        print(f"[ncbi] fetch_gene_genbank: using {accession} ({title[:80]})")

        handle = Entrez.efetch(
            db="nuccore", id=accession, rettype="gbwithparts", retmode="text",
        )
        gb = handle.read()
        handle.close()
        return gb if gb.strip() else None
    except Exception as e:
        print(f"[ncbi] fetch_gene_genbank failed for {gene_symbol}/{taxid}: {e}")
        return None


def fetch_gene_genomic_region(
    taxid: int,
    gene_symbol: str,
    flank_bp: int = 2000,
) -> dict | None:
    """Fetch the chromosomal DNA region containing a gene.

    Returns {fasta, accession, gene_start, gene_stop, fetch_start, fetch_stop,
    strand, gene_id, flank_bp} or None.

    Walks Entrez Gene XML to find <Entrezgene_locus> → <Gene-commentary_seqs>
    → <Seq-loc_int> → <Seq-interval>, which is the canonical gene-level
    chromosomal location (not exon-level).
    """
    from Bio import Entrez

    try:
        # Step 1: find the gene ID.
        search = Entrez.esearch(
            db="gene",
            term=f"{gene_symbol}[Gene Name] AND txid{taxid}[Organism:noexp]",
            retmax=5,
        )
        result = Entrez.read(search)
        search.close()
        gene_ids = result.get("IdList", [])
        if not gene_ids:
            print(f"[ncbi] no gene found for {gene_symbol}/{taxid}")
            return None
        gene_id = gene_ids[0]

        # Step 2: fetch Gene XML and parse into Python dict.
        fetch_xml = Entrez.efetch(db="gene", id=gene_id, retmode="xml")
        parsed = Entrez.read(fetch_xml)
        fetch_xml.close()

        if not parsed:
            print(f"[ncbi] gene {gene_id} returned empty XML")
            return None

        # Biopython parses NCBI Gene XML into a list of Entrezgene dicts.
        entrezgene = parsed[0] if isinstance(parsed, list) else parsed
        locus_list = entrezgene.get("Entrezgene_locus", [])
        if not locus_list:
            print(f"[ncbi] gene {gene_id} has no Entrezgene_locus")
            return None

        # Pick the locus marked as "primary" if there is one (RefSeq assembly).
        # Otherwise take the first non-empty entry.
        primary_locus = None
        for loc in locus_list:
            # The primary locus has a Gene-commentary_heading like "Reference GRCh38..."
            # but more reliably, it has Gene-commentary_seqs populated.
            seqs = loc.get("Gene-commentary_seqs", [])
            if seqs:
                primary_locus = loc
                break
        if primary_locus is None:
            print(f"[ncbi] gene {gene_id} has no Gene-commentary_seqs in any locus")
            return None

        # Walk into the Seq-loc → Seq-interval structure.
        # Path: Gene-commentary_seqs[0] → Seq-loc_int → Seq-interval
        seq_loc = primary_locus["Gene-commentary_seqs"][0]
        # seq_loc is a Seq-loc dict; we want Seq-loc_int (interval) or Seq-loc_mix
        # for compound locations.
        seq_int = None
        if "Seq-loc_int" in seq_loc:
            seq_int_wrap = seq_loc["Seq-loc_int"]
            seq_int = seq_int_wrap.get("Seq-interval")
        elif "Seq-loc_mix" in seq_loc:
            # Compound location — take the union start/end
            mix = seq_loc["Seq-loc_mix"]
            sublocs = mix if isinstance(mix, list) else []
            starts, stops = [], []
            chr_id_dict = None
            for sub in sublocs:
                if "Seq-loc_int" in sub:
                    si = sub["Seq-loc_int"].get("Seq-interval")
                    if si:
                        starts.append(int(si["Seq-interval_from"]))
                        stops.append(int(si["Seq-interval_to"]))
                        if chr_id_dict is None:
                            chr_id_dict = si.get("Seq-interval_id")
            if starts and stops and chr_id_dict:
                # Synthesize a flat Seq-interval-like dict
                seq_int = {
                    "Seq-interval_from": min(starts),
                    "Seq-interval_to": max(stops),
                    "Seq-interval_id": chr_id_dict,
                    "Seq-interval_strand": sublocs[0].get("Seq-loc_int", {}).get("Seq-interval", {}).get("Seq-interval_strand"),
                }

        if seq_int is None:
            print(f"[ncbi] gene {gene_id} has no Seq-interval (locus structure unrecognized)")
            return None

        # Pull coordinates (0-based in XML).
        gene_start = int(seq_int["Seq-interval_from"]) + 1
        gene_stop = int(seq_int["Seq-interval_to"]) + 1

        # Strand
        strand_block = seq_int.get("Seq-interval_strand", {})
        # In Biopython's parse, this is {"Na-strand": {"attributes": {"value": "minus"}}}
        # or just a string under different versions.
        strand_val = "+"
        try:
            if isinstance(strand_block, dict):
                na = strand_block.get("Na-strand")
                if isinstance(na, dict):
                    if na.get("attributes", {}).get("value") == "minus":
                        strand_val = "-"
                elif na == "minus":
                    strand_val = "-"
        except Exception:
            pass

        # Chromosome / contig accession.
        # Seq-interval_id → Seq-id → Seq-id_gi (a number, e.g. "568815596")
        # OR Seq-id_other → Textseq-id → Textseq-id_accession + Textseq-id_version
        chr_accession = None
        seq_id_wrap = seq_int.get("Seq-interval_id", {})
        if isinstance(seq_id_wrap, dict):
            seq_id_inner = seq_id_wrap.get("Seq-id", {})
            if isinstance(seq_id_inner, dict):
                # Prefer accession form
                if "Seq-id_other" in seq_id_inner:
                    other = seq_id_inner["Seq-id_other"].get("Textseq-id", {})
                    acc_name = other.get("Textseq-id_accession", "")
                    acc_ver = other.get("Textseq-id_version", "")
                    if acc_name:
                        chr_accession = f"{acc_name}.{acc_ver}" if acc_ver else acc_name
                elif "Seq-id_gi" in seq_id_inner:
                    chr_accession = str(seq_id_inner["Seq-id_gi"])
                elif "Seq-id_genbank" in seq_id_inner:
                    gb = seq_id_inner["Seq-id_genbank"].get("Textseq-id", {})
                    acc_name = gb.get("Textseq-id_accession", "")
                    acc_ver = gb.get("Textseq-id_version", "")
                    if acc_name:
                        chr_accession = f"{acc_name}.{acc_ver}" if acc_ver else acc_name

        if not chr_accession:
            print(f"[ncbi] gene {gene_id} has Seq-interval but no parseable chr accession")
            return None

        # Step 3: fetch the region with flanks.
        fetch_start = max(1, gene_start - flank_bp)
        fetch_stop = gene_stop + flank_bp

        print(
            f"[ncbi] fetch_gene_genomic_region: {gene_symbol}/{taxid} → "
            f"{chr_accession}:{fetch_start}-{fetch_stop} strand={strand_val}"
        )

        fetch_seq = Entrez.efetch(
            db="nuccore",
            id=chr_accession,
            seq_start=str(fetch_start),
            seq_stop=str(fetch_stop),
            strand="1" if strand_val != "-" else "2",
            rettype="fasta",
            retmode="text",
        )
        fasta = fetch_seq.read()
        fetch_seq.close()

        if not fasta or not fasta.strip():
            print(f"[ncbi] efetch returned empty fasta for {chr_accession}:{fetch_start}-{fetch_stop}")
            return None

        return {
            "fasta": fasta,
            "accession": chr_accession,
            "gene_start": gene_start,
            "gene_stop": gene_stop,
            "fetch_start": fetch_start,
            "fetch_stop": fetch_stop,
            "strand": strand_val,
            "gene_id": gene_id,
            "flank_bp": flank_bp,
        }

    except Exception as e:
        import traceback
        print(f"[ncbi] fetch_gene_genomic_region failed for {gene_symbol}/{taxid}: {e}")
        print(traceback.format_exc())
        return None
