"""Clade gene/sequence pipeline: gather a query's homolog across all members of a
taxon, then align.

Goal: "Take this sequence (a gene region, or any nucleotide/protein sequence) and
compare it against its homolog in every member of family/genus Y."

Three strategies:

  - blast_clade (recommended, esp. for viruses/bacteria): ONE remote BLAST of the
    query, scoped to the whole clade via ENTREZ_QUERY=txidN[Organism]. Each hit is
    a member's homolog; we group hits by organism and take the best per organism.
    Fast (one BLAST, not N) and correctly per-species. The aligned hit subsequence
    comes straight from the BLAST XML, so no extra per-member fetch.

  - gene_symbol_then_blast: per-member RefSeq mRNA by gene symbol (cheap), falling
    back to a clade BLAST for members the symbol missed. Requires a gene symbol.

  - gene_symbol_only: symbol lookup per member; skip members without that symbol.
    Requires a gene symbol.

Program (blastn/blastp/tblastn/…) is auto-detected from the query sequence type
(nucleotide vs protein) unless overridden. The gene symbol is OPTIONAL — for the
blast modes you can pass just a sequence.

Outputs the assembled pre-alignment FASTA and the MAFFT alignment as project
files (the pre-alignment file is kept so you can re-MAFFT without re-hitting NCBI).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from app.sources import ncbi
from app.tools import blast as blast_tool
from app.tools import mafft as mafft_tool
from app.tools.registry import ToolDef, ParamDef, register


# Strategy identifiers (blast_only kept as an alias of blast_clade for back-compat
# with already-saved runs).
_STRATEGIES = {"blast_clade", "blast_only", "gene_symbol_then_blast", "gene_symbol_only"}


def _safe(s: str) -> str:
    return re.sub(r"[\s(),;:\[\]'\"]+", "_", (s or "").strip()).strip("_")


def _seq_residues(fasta_or_seq: str) -> str:
    """Return just the residue letters (no headers, no whitespace), uppercased."""
    out = []
    for line in (fasta_or_seq or "").splitlines():
        if line.startswith(">"):
            continue
        out.append(line.strip())
    return re.sub(r"[^A-Za-z]", "", "".join(out)).upper()


def detect_seq_type(fasta_or_seq: str) -> str:
    """Heuristically classify a sequence as 'nucl' or 'prot'.

    Rule: nucleotide sequences are essentially all A/C/G/T/U/N (+ ambiguity
    codes). If ≥90% of residues are in the nucleotide alphabet, call it nucl;
    otherwise prot. Empty → 'nucl' (harmless default; blastn).
    """
    res = _seq_residues(fasta_or_seq)
    if not res:
        return "nucl"
    nuc_alphabet = set("ACGTUN")
    nuc_count = sum(1 for c in res if c in nuc_alphabet)
    frac = nuc_count / len(res)
    return "nucl" if frac >= 0.90 else "prot"


def program_for(seq_type: str) -> tuple[str, str]:
    """Map a query sequence type to (blast_program, database).

    - nucleotide query → blastn vs nt (find nucleotide homologs in the clade)
    - protein query    → tblastn vs nt (find coding regions in nucleotide records
                         of the clade; better for cross-species than blastp vs nr
                         when you want the genomic/transcript hit per organism)
    """
    if seq_type == "prot":
        return "tblastn", "nt"
    return "blastn", "nt"


def db_for_program(program: str) -> str:
    """The correct default DATABASE for a BLAST program. This is *program*-driven,
    not query-type-driven: the database alphabet must match what the program
    searches against, or NCBI returns nothing/errors.

      blastp, blastx        → protein DB (nr)
      blastn, tblastn, tblastx → nucleotide DB (nt)

    (e.g. blastp vs a nucleotide db is invalid — a common silent "no hits" cause.)
    """
    if program in ("blastp", "blastx"):
        return "nr"
    return "nt"


def _first_seq_record(fasta_text: str) -> str:
    """Return the first FASTA record (header + sequence) from a multi-record
    blob, stripped. NCBI gene FASTA can return several records; for alignment we
    want one representative per species to keep the column set clean."""
    if not fasta_text or not fasta_text.strip():
        return ""
    records: list[list[str]] = []
    cur: list[str] = []
    for line in fasta_text.splitlines():
        if line.startswith(">"):
            if cur:
                records.append(cur)
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        records.append(cur)
    if not records:
        return ""
    return "\n".join(records[0]).strip()


def _relabel_record(fasta_record: str, species_name: str, taxid: int) -> str:
    """Rewrite the header of a single FASTA record to lead with the species name,
    preserving the original accession in the description. Mirrors the convention
    in app.tools._inputs so downstream tree/alignment labels carry species
    identity."""
    if not fasta_record.strip():
        return ""
    lines = fasta_record.splitlines()
    header = lines[0][1:].strip() if lines and lines[0].startswith(">") else ""
    seq_lines = [ln for ln in lines[1:] if not ln.startswith(">")]
    first_tok, _, rest = header.partition(" ")
    prefix = _safe(species_name) or f"txid{taxid}"
    new_header = f">{prefix}_{_safe(first_tok)}"
    if rest:
        new_header += f" {rest}"
    return "\n".join([new_header, *seq_lines]).strip()


def _organism_from_hit(hit: dict[str, Any]) -> str:
    """Extract an organism label from a BLAST hit's subject definition.

    NCBI Hit_def lines usually look like:
      "Mayaro virus strain X polyprotein gene, complete cds"
      "Una virus isolate Y, complete genome"
    There's no guaranteed organism field in the URL-API XML, so we take the
    leading words up to a delimiter as a best-effort organism name. Hits that
    can't be resolved are grouped under their accession.
    """
    subj = (hit.get("subject") or "").strip()
    if not subj:
        return hit.get("accession") or "unknown"
    # Cut at common boundary words that start the description tail.
    cut = re.split(r"\b(strain|isolate|clone|segment|gene|polyprotein|complete|"
                   r"partial|nonstructural|structural|genomic|mRNA|chromosome)\b",
                   subj, maxsplit=1, flags=re.IGNORECASE)[0]
    label = cut.strip(" ,;:")
    return label or (hit.get("accession") or "unknown")


def _clade_blast_homologs(
    query_text: str,
    root_taxid: int,
    program: str,
    database: str,
    min_identity: float,
    max_members: int,
    log=None,
    evalue: float | None = None,
    word_size: int | None = None,
) -> list[tuple[str, str]]:
    """One clade-scoped remote BLAST. Returns [(organism_label, fasta_record), …],
    best hit per organism, the homologous region taken from the BLAST alignment.

    Raises (with the real NCBI error) on submit/timeout failure — those propagate
    to the run's log + error instead of being swallowed.
    """
    def _emit(m: str) -> None:
        if log:
            log(m)

    entrez_query = f"txid{root_taxid}[Organism]"
    _emit(f"clade BLAST: {program} vs {database}, scoped to {entrez_query}")
    _emit("(one search across the whole clade; NCBI BLAST can take 1-5 min)")

    # Let exceptions propagate — the worker logs the full traceback, and the
    # user asked specifically to see external-tool failures rather than a tag.
    hits, cmd = blast_tool._run_remote_blast(
        query_text, program, database, entrez_query=entrez_query, log=_emit,
        evalue=evalue, word_size=word_size,
    )
    _emit(f"clade BLAST command: {cmd}")
    if not hits:
        _emit("clade BLAST returned 0 hits — query may be too divergent, or the "
              "clade has no records in this database.")
        return []

    # Group hits by organism; keep the best (highest bit-score) per organism.
    best_by_org: dict[str, dict[str, Any]] = {}
    for h in hits:
        if h.get("percent_identity", 0) < min_identity:
            continue
        org = _organism_from_hit(h)
        cur = best_by_org.get(org)
        if cur is None or h["bit_score"] > cur["bit_score"]:
            best_by_org[org] = h

    _emit(f"grouped into {len(best_by_org)} organism(s) above {min_identity}% identity")

    results: list[tuple[str, str]] = []
    for org, h in sorted(best_by_org.items(), key=lambda kv: -kv[1]["bit_score"])[:max_members]:
        # The aligned hit subsequence (gaps removed) IS the member's homolog.
        seq = (h.get("hseq") or "").replace("-", "").strip()
        if not seq:
            # No aligned seq in XML (rare) — fall back to fetching the record.
            acc = h.get("accession") or (h.get("subject") or "").split()[0]
            if acc:
                fa = ncbi.fetch_nuccore_fasta(acc)
                seq = _seq_residues(fa) if fa else ""
        if not seq:
            continue
        acc = h.get("accession") or "hit"
        header = (f">{_safe(org)}_{_safe(acc)} "
                  f"pid={h.get('percent_identity', 0):.1f} bits={h['bit_score']:.0f}")
        wrapped = "\n".join(seq[i:i+70] for i in range(0, len(seq), 70))
        results.append((org, f"{header}\n{wrapped}"))
    return results


def _symbol_homologs(
    members: list[dict[str, Any]],
    gene_symbol: str,
    fallback_clade_blast: bool,
    query_text: str,
    root_taxid: int,
    program: str,
    database: str,
    min_identity: float,
    max_members: int,
    progress_cb,
    log=None,
    evalue: float | None = None,
    word_size: int | None = None,
) -> list[str]:
    """Per-member RefSeq mRNA by gene symbol, optionally falling back to a single
    clade BLAST for the members the symbol missed. Returns relabeled FASTA records."""
    def _emit(m: str) -> None:
        if log:
            log(m)

    total = len(members)
    collected: list[str] = []
    missed: list[dict[str, Any]] = []
    for i, m in enumerate(members):
        _emit(f"[{i+1}/{total}] {m['scientific_name']} (txid{m['taxid']}) — symbol {gene_symbol}")
        fasta = None
        try:
            fasta = ncbi.fetch_gene_fasta(m["taxid"], gene_symbol)
        except Exception as e:
            _emit(f"    symbol fetch error: {e}")
        rec = _first_seq_record(fasta) if fasta else ""
        if rec:
            collected.append(_relabel_record(rec, m["scientific_name"], m["taxid"]))
            _emit("    → found via gene_symbol")
        else:
            missed.append(m)
            _emit("    → symbol miss")
        if progress_cb and (i % 2 == 0 or i == total - 1):
            progress_cb(10 + int((i + 1) / total * 60),
                        f"symbol: {len(collected)}/{i+1} found")

    if missed and fallback_clade_blast:
        _emit(f"{len(missed)} member(s) missed by symbol; one clade BLAST fallback…")
        if progress_cb:
            progress_cb(72, "clade BLAST fallback for symbol misses…")
        try:
            for _org, rec in _clade_blast_homologs(
                query_text, root_taxid, program, database, min_identity,
                max_members, log, evalue=evalue, word_size=word_size,
            ):
                collected.append(rec)
        except Exception as e:
            _emit(f"clade BLAST fallback failed (non-fatal): {e}")

    return collected


def _run_pipeline(
    query_text: str,
    gene_symbol: str,
    root_taxid: int,
    strategy: str,
    program: str,
    database: str,
    min_identity: float,
    max_members: int,
    require_assembly: bool,
    mafft_params: dict[str, Any],
    progress_cb,
    log=None,
    evalue: float | None = None,
    word_size: int | None = None,
) -> dict[str, Any]:
    def _emit(m: str) -> None:
        if log:
            log(m)

    query_record = _first_seq_record(query_text)
    collected: list[str] = []
    if query_record:
        collected.append(query_record)  # query stays as-is; user labeled it

    label_stub = _safe(gene_symbol) or "query"

    # ── BLAST-clade strategies (no per-member enumeration needed) ──────
    if strategy in ("blast_clade", "blast_only"):
        if progress_cb:
            progress_cb(10, "submitting clade BLAST…")
        homologs = _clade_blast_homologs(
            query_text, root_taxid, program, database, min_identity,
            max_members, log, evalue=evalue, word_size=word_size,
        )
        for _org, rec in homologs:
            collected.append(rec)
        found = len(homologs)
        members_total = found  # for blast modes, "members" = organisms hit
        per_member = [{"organism": org, "method": "clade_blast", "status": "found"}
                      for org, _ in homologs]

    # ── Symbol strategies (need member enumeration) ───────────────────
    else:
        if not gene_symbol:
            raise ValueError(
                f"strategy '{strategy}' needs a gene_symbol. To search by sequence "
                "alone, use strategy 'blast_clade'."
            )
        _emit(f"enumerating species-rank members under txid{root_taxid}…")
        members = ncbi.list_species_members(root_taxid, max_results=max_members)
        members = members[:max_members]
        if not members:
            raise ValueError(
                f"no species-rank members found under txid{root_taxid}. "
                "Check the taxid is a family/genus, not a species."
            )
        _emit(f"found {len(members)} member species")

        if require_assembly:
            _emit("filtering to members with a genome assembly…")
            kept = []
            for m in members:
                try:
                    if ncbi.fetch_assembly_count(m["taxid"]) > 0:
                        kept.append(m)
                except Exception:
                    pass
            members = kept
            _emit(f"{len(members)} member(s) have an Assembly-DB genome")
            if not members:
                raise ValueError(
                    "require_assembly=True removed every member. Re-run with it off."
                )

        members_total = len(members)
        recs = _symbol_homologs(
            members, gene_symbol,
            fallback_clade_blast=(strategy == "gene_symbol_then_blast"),
            query_text=query_text, root_taxid=root_taxid,
            program=program, database=database, min_identity=min_identity,
            max_members=max_members, progress_cb=progress_cb, log=log,
            evalue=evalue, word_size=word_size,
        )
        collected.extend(recs)
        found = len(recs)
        per_member = [{"method": "symbol_or_fallback", "status": "see logs"}]

    _emit(f"acquisition complete: {found} homolog sequence(s) collected"
          + (f" (+1 query)" if query_record else ""))
    if found == 0:
        raise RuntimeError(
            "no homolog sequences acquired. Things to try: lower min_identity, "
            "switch strategy to 'blast_clade', check the query sequence type and "
            "program, or verify the root taxid has records in NCBI."
        )

    assembled_name = f"{label_stub}_{_safe_root_label(root_taxid)}_homologs.fasta"
    assembled_text = "\n".join(collected).strip() + "\n"

    # MAFFT.
    if progress_cb:
        progress_cb(82, "running MAFFT alignment…")
    align_name = f"mafft_{label_stub}_{_safe_root_label(root_taxid)}.fasta"
    mafft_result = mafft_tool._run_mafft(assembled_text, mafft_params, align_name, log)
    if progress_cb:
        progress_cb(98, "alignment done; saving files")

    align_files = mafft_result.pop("output_files", [])
    output_files = [
        {
            "name": assembled_name,
            "content": assembled_text,
            "kind": "fasta",
            "size": len(assembled_text),
            "source_metadata": {
                "gene": gene_symbol or None,
                "root_taxid": root_taxid,
                "stage": "pre_alignment",
            },
        },
        *[
            {**f, "source_metadata": {**f.get("source_metadata", {}),
                                      "gene": gene_symbol or None, "root_taxid": root_taxid,
                                      "stage": "alignment"}}
            for f in align_files
        ],
    ]

    return {
        "gene_symbol": gene_symbol or None,
        "root_taxid": root_taxid,
        "strategy": strategy,
        "program": program,
        "database": database,
        "min_identity": min_identity,
        "homologs_found": found,
        "members_considered": members_total,
        "per_member": per_member,
        "mafft": {
            "num_sequences": mafft_result.get("num_sequences"),
            "alignment_length": mafft_result.get("alignment_length"),
            "strategy": mafft_result.get("strategy"),
            "cmd": mafft_result.get("cmd"),
        },
        "output_files": output_files,
    }


def _safe_root_label(root_taxid: int) -> str:
    try:
        tax = ncbi.fetch_taxonomy(root_taxid)
        if tax and tax.get("scientific_name"):
            return _safe(tax["scientific_name"])
    except Exception:
        pass
    return f"txid{root_taxid}"


async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str, log=None) -> dict[str, Any]:
    from app.tools._inputs import get_input_text

    def _emit(m: str) -> None:
        if log:
            log(m)

    root_taxid = int(params.get("root_taxid") or 0)
    if not root_taxid:
        raise ValueError("root_taxid required (the family or genus TaxID)")

    strategy = (params.get("strategy") or "blast_clade").strip()
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    # Back-compat: old saved runs may use 'blast_only'.
    if strategy == "blast_only":
        strategy = "blast_clade"

    gene_symbol = (params.get("gene_symbol") or "").strip()
    gene_id = (params.get("gene_id") or "").strip()

    max_members = int(params.get("max_members", 100))
    min_identity = float(params.get("min_identity", 0.0))
    require_assembly = bool(params.get("require_assembly", False))

    # ── Resolve the query sequence ────────────────────────────────────
    # Priority: explicit query_text → gene_id (fetch FASTA) → project file input.
    query_text = (params.get("query_text") or "").strip()
    if not query_text and gene_id:
        _emit(f"fetching query sequence for gene ID {gene_id}…")
        query_text = await asyncio.to_thread(ncbi.fetch_gene_fasta_by_id, gene_id) or ""
        if not query_text:
            raise ValueError(f"could not fetch a sequence for gene ID {gene_id}")
    if not query_text:
        try:
            query_text = get_input_text(inputs, project_id)
        except Exception:
            query_text = ""
    if not query_text or not query_text.strip():
        raise ValueError(
            "no query sequence: paste query_text, give a gene_id, or select a "
            "project FASTA as input"
        )

    # ── Sequence type + BLAST program (auto-detect, with override) ────
    seq_type = detect_seq_type(query_text)
    auto_program, _auto_db = program_for(seq_type)
    program = (params.get("blast_program") or "").strip() or auto_program
    # Database follows the PROGRAM (alphabet must match), not the query type.
    # If the user explicitly set a db, honor it; otherwise pick the right default
    # for the chosen program. This fixes "blastp + blank db → nt → 0 hits".
    database = (params.get("blast_database") or "").strip() or db_for_program(program)
    overridden = bool((params.get("blast_program") or "").strip())
    _emit(f"query looks like {seq_type.upper()} "
          f"→ program={program}{' (overridden)' if overridden else ' (auto)'}, db={database}")
    if seq_type == "prot" and program == "blastn":
        _emit("WARNING: blastn on a protein query will fail. For protein queries "
              "use blastp (vs nr) or tblastn (vs nt).")
    if program == "blastn":
        _emit("NOTE: blastn targets HIGHLY SIMILAR nucleotide sequences. For "
              "diverged taxa (e.g. MAYV vs CHIKV) it often finds nothing even when "
              "the proteins are clearly homologous — use a protein-level search "
              "(blastp/tblastn) for cross-genus comparisons.")

    # Label the query record if it lacks a header.
    if not query_text.lstrip().startswith(">"):
        stub = _safe(gene_symbol) or _safe(gene_id) or "query"
        query_text = f">query_{stub}\n{query_text}"

    mafft_params = {
        "strategy": params.get("mafft_strategy", "auto"),
        "gap_open_penalty": params.get("gap_open_penalty", 1.53),
        "gap_extension_penalty": params.get("gap_extension_penalty", 0.0),
        "extra_args": params.get("mafft_extra_args", ""),
    }

    # BLAST sensitivity knobs. Defaults are intentionally permissive for
    # cross-species/cross-genus homology: a relaxed e-value finds diverged hits
    # blastn's defaults would miss. None word_size = let NCBI pick the program
    # default. evalue default 10 is BLAST's web default (not the stringent 1e-10
    # used for same-species hits).
    evalue = float(params.get("evalue", 10.0))
    ws_raw = params.get("word_size", 0)
    word_size = int(ws_raw) if ws_raw else None

    _emit(f"strategy={strategy}  root_taxid={root_taxid}  "
          f"gene_symbol={gene_symbol or '—'}  gene_id={gene_id or '—'}")
    _emit(f"max_members={max_members}  min_identity={min_identity}%  "
          f"e-value={evalue}  word_size={word_size or 'default'}  "
          f"require_assembly={require_assembly}")

    def _progress(pct: int, msg: str | None = None) -> None:
        if log and hasattr(log, "progress"):
            log.progress(pct, msg)
        elif msg:
            _emit(msg)

    return await asyncio.to_thread(
        _run_pipeline,
        query_text,
        gene_symbol,
        root_taxid,
        strategy,
        program,
        database,
        min_identity,
        max_members,
        require_assembly,
        mafft_params,
        _progress,
        _emit,
        evalue,
        word_size,
    )


register(ToolDef(
    id="family_gene_pipeline",
    label="Clade gene/sequence pipeline (gather → align)",
    description=(
        "Find a query's homolog across every member of a family/genus, then "
        "MAFFT-align them. Pass a raw nucleotide OR protein sequence (gene symbol "
        "optional). Default 'blast_clade' runs ONE BLAST scoped to the clade — "
        "fast and correct for viruses/bacteria — and each hit becomes a member. "
        "Program is auto-detected from the sequence type unless overridden."
    ),
    input_kind="none",  # query comes from query_text/gene_id param or a selected project FASTA
    params=[
        ParamDef(name="root_taxid", type="int", label="Family/genus TaxID",
                 default=0, help="The clade to search (e.g. 11019 = Alphavirus)."),
        ParamDef(name="strategy", type="enum", label="Acquisition strategy",
                 default="blast_clade",
                 options=["blast_clade", "gene_symbol_then_blast", "gene_symbol_only"],
                 help="blast_clade: one clade-scoped BLAST (recommended; works by "
                      "sequence alone). gene_symbol_* need a gene symbol."),
        ParamDef(name="query_text", type="text_long", label="Query sequence",
                 default="",
                 help="FASTA or raw sequence (nucleotide or protein). The primary "
                      "input for blast_clade. Leave blank to use gene_id or a project file."),
        ParamDef(name="gene_id", type="string", label="NCBI Gene ID (optional)",
                 default="",
                 help="If set and no query_text, the query sequence is fetched for this Gene ID."),
        ParamDef(name="gene_symbol", type="string", label="Gene symbol (optional)",
                 default="",
                 help="Only required for the gene_symbol_* strategies (e.g. TP53)."),
        ParamDef(name="blast_program", type="enum", label="BLAST program",
                 default="",
                 options=["", "blastn", "tblastn", "blastp", "tblastx", "blastx"],
                 help="Blank = auto (nucleotide→blastn, protein→tblastn). Override here."),
        ParamDef(name="blast_database", type="string", label="BLAST database",
                 default="",
                 help="Blank = auto (nt). e.g. nr, refseq_rna, refseq_genomic."),
        ParamDef(name="min_identity", type="float", label="Min % identity",
                 default=0.0, min=0.0, max=100.0,
                 help="Drop hits below this percent identity. 0 = keep all."),
        ParamDef(name="evalue", type="float", label="E-value threshold",
                 default=10.0, min=0.0, max=10000.0,
                 help="Max e-value. Default 10 (web default) finds diverged hits; "
                      "lower (e.g. 1e-5) for stricter same-gene matches."),
        ParamDef(name="word_size", type="int", label="Word size (0 = default)",
                 default=0, min=0, max=50,
                 help="0 lets NCBI pick. Smaller (e.g. blastn 7, blastp 2) finds "
                      "more distant homology at the cost of speed."),
        ParamDef(name="max_members", type="int", label="Max members/hits",
                 default=100, min=1, max=500,
                 help="Cap on organisms/hits gathered."),
        ParamDef(name="require_assembly", type="bool", label="Only members with a genome assembly",
                 default=False,
                 help="Only affects the gene_symbol_* strategies."),
        ParamDef(name="mafft_strategy", type="enum", label="MAFFT strategy",
                 default="auto",
                 options=["auto", "fft-ns-2", "fft-ns-i", "l-ins-i", "g-ins-i", "e-ins-i"],
                 help="l-ins-i is most accurate for <200 seqs."),
        ParamDef(name="gap_open_penalty", type="float", label="MAFFT gap open",
                 default=1.53, min=0.0, max=10.0),
        ParamDef(name="gap_extension_penalty", type="float", label="MAFFT gap extension",
                 default=0.0, min=0.0, max=10.0),
        ParamDef(name="mafft_extra_args", type="string", label="MAFFT extra args",
                 default="", help="Appended to the mafft command line. e.g. --thread 4"),
    ],
    run=run,
))
