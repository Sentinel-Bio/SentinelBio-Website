"""BLAST searches.

Two modes:

1. **local** (default): BLAST a query sequence against a project file
   (typically an unannotated genome assembly). Auto-builds a BLAST index
   for the subject file on first use (cached).

   Use case: "I have human SF3B1 mRNA. Where is it in my A. gazella genome?"

2. **remote**: BLAST against NCBI's online databases (nr, nt, refseq).
   Slow (1-5 min) but no local install required for the database.

   Use case: "What gene is this sequence?"

Both modes return:
- hits: list of {subject, query, identity, length, e_value, bit_score, sub_start, sub_end, q_start, q_end, sub_strand}
- best_hit: convenience pointer to hits[0]
- output_files: an "extracted region" FASTA for the best hit (local mode only)
  so you can immediately MAFFT it with the original query.

Requires `ncbi-blast+` package locally for local mode (`sudo pacman -S blast+`).
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.files import store as filestore
from app.supabase_client import service_client
from app.tools.registry import ToolDef, ParamDef, register


# ─── Query resolution ───────────────────────────────────────────────

def _resolve_query(params: dict[str, Any]) -> tuple[str, str]:
    """Return (query_fasta_text, query_label)."""
    qt = (params.get("query_text") or "").strip()
    if not qt:
        raise ValueError(
            "query_text required: paste a FASTA-format query sequence in the form"
        )
    if not qt.startswith(">"):
        qt = ">user_query\n" + qt
    first_line = qt.splitlines()[0].lstrip(">").strip()
    label = re.sub(r"[^a-zA-Z0-9._-]+", "_", first_line.split()[0])[:64] or "query"
    return qt, label


def _resolve_subject_path(subject_file_id: str, project_id: str) -> tuple[str, str]:
    """Return (absolute_path_to_subject_fasta, label)."""
    client = service_client()
    fres = (
        client.table("project_files")
        .select("storage_path, name")
        .eq("id", subject_file_id)
        .maybe_single().execute()
    )
    if not fres or not fres.data:
        raise ValueError(f"subject file {subject_file_id} not found")
    abs_path = str(filestore.absolute_path(project_id, fres.data["storage_path"]))
    label = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(fres.data["name"]).stem)[:64]
    return abs_path, label


# ─── Local BLAST ────────────────────────────────────────────────────

def _has(name: str) -> bool:
    return shutil.which(name) is not None


def _build_blast_db(subject_path: str, dbtype: str = "nucl") -> str:
    """Build a BLAST DB next to the subject FASTA (if missing). Returns the
    DB base path."""
    db_base = subject_path  # makeblastdb produces {base}.nhr / .nin / .nsq next to it
    nhr = subject_path + (".nhr" if dbtype == "nucl" else ".phr")
    if os.path.exists(nhr):
        return db_base
    if not _has("makeblastdb"):
        raise RuntimeError(
            "makeblastdb not installed. Install BLAST+: `sudo pacman -S blast+`"
        )
    r = subprocess.run(
        ["makeblastdb", "-in", subject_path, "-dbtype", dbtype, "-out", db_base],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        raise RuntimeError(f"makeblastdb failed: {r.stderr or r.stdout}")
    return db_base


def _parse_blast_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse BLAST XML output into structured hit rows."""
    hits: list[dict[str, Any]] = []
    if not xml_text.strip():
        return hits
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"BLAST XML parse error: {e}")

    for iteration in root.iter("Iteration"):
        q_def = (iteration.findtext("Iteration_query-def") or "").strip()
        q_len = int(iteration.findtext("Iteration_query-len") or 0)
        for hit in iteration.iter("Hit"):
            subject = (hit.findtext("Hit_def") or hit.findtext("Hit_id") or "").strip()
            accession = (hit.findtext("Hit_accession") or "").strip()
            sub_len = int(hit.findtext("Hit_len") or 0)
            for hsp in hit.iter("Hsp"):
                bit_score = float(hsp.findtext("Hsp_bit-score") or 0)
                evalue = float(hsp.findtext("Hsp_evalue") or 0)
                identity = int(hsp.findtext("Hsp_identity") or 0)
                align_len = int(hsp.findtext("Hsp_align-len") or 0)
                q_from = int(hsp.findtext("Hsp_query-from") or 0)
                q_to = int(hsp.findtext("Hsp_query-to") or 0)
                h_from = int(hsp.findtext("Hsp_hit-from") or 0)
                h_to = int(hsp.findtext("Hsp_hit-to") or 0)
                # Aligned hit subsequence (with gap dashes). Stripping '-' yields
                # the member's homologous region directly — no extra NCBI fetch.
                hseq = (hsp.findtext("Hsp_hseq") or "").strip()
                pct_id = (identity / align_len * 100) if align_len else 0
                strand = "+" if h_from <= h_to else "-"
                hits.append({
                    "query": q_def,
                    "query_len": q_len,
                    "subject": subject,
                    "accession": accession,
                    "subject_len": sub_len,
                    "bit_score": bit_score,
                    "e_value": evalue,
                    "identity": identity,
                    "align_len": align_len,
                    "percent_identity": pct_id,
                    "q_start": q_from,
                    "q_end": q_to,
                    "sub_start": min(h_from, h_to),
                    "sub_end": max(h_from, h_to),
                    "sub_strand": strand,
                    "hseq": hseq,
                })
    hits.sort(key=lambda h: (-h["bit_score"], h["e_value"]))
    return hits


def _extract_region_fasta(subject_path: str, seq_id: str, start: int, end: int, strand: str) -> str:
    """Pull a region from the subject FASTA, reverse-complement if needed."""
    if _has("samtools"):
        # samtools faidx; build index if needed
        fai = subject_path + ".fai"
        if not os.path.exists(fai):
            r = subprocess.run(
                ["samtools", "faidx", subject_path],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                raise RuntimeError(f"samtools faidx failed: {r.stderr}")
        region = f"{seq_id}:{start}-{end}"
        cmd = ["samtools", "faidx", subject_path, region]
        if strand == "-":
            cmd.append("-i")  # reverse-complement
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"samtools faidx failed: {r.stderr}")
        return r.stdout

    # Pure-Python fallback (slow on huge genomes but works).
    seq_buf: list[str] = []
    in_target = False
    consumed = 0
    with open(subject_path, "r") as f:
        for line in f:
            if line.startswith(">"):
                if in_target:
                    break
                hid = line[1:].split(None, 1)[0]
                if hid == seq_id:
                    in_target = True
                continue
            if not in_target:
                continue
            chunk = line.strip()
            if not chunk:
                continue
            cstart = consumed + 1
            cend = consumed + len(chunk)
            if cend < start:
                consumed += len(chunk)
                continue
            if cstart > end:
                break
            lo = max(0, start - cstart)
            hi = len(chunk) if cend <= end else end - cstart + 1
            seq_buf.append(chunk[lo:hi])
            consumed += len(chunk)
    if not seq_buf:
        raise RuntimeError(f"sequence '{seq_id}' not found")
    seq = "".join(seq_buf).upper()
    if strand == "-":
        comp = str.maketrans("ACGTN", "TGCAN")
        seq = seq.translate(comp)[::-1]
    wrapped = "\n".join(seq[i:i + 60] for i in range(0, len(seq), 60))
    return f">{seq_id}:{start}-{end}({strand})\n{wrapped}\n"


def _run_local_blast(
    query_text: str,
    subject_path: str,
    program: str,
    evalue: float,
    max_hits: int,
) -> tuple[list[dict[str, Any]], str]:
    """Returns (hits, cmd_used)."""
    if not _has(program):
        raise RuntimeError(
            f"{program} not installed. Install BLAST+: `sudo pacman -S blast+`"
        )
    db_base = _build_blast_db(subject_path, dbtype="nucl")  # for now, nucl only

    qfd, qpath = tempfile.mkstemp(prefix="blast_q_", suffix=".fasta")
    try:
        with os.fdopen(qfd, "w") as f:
            f.write(query_text)
        cmd = [
            program,
            "-query", qpath,
            "-db", db_base,
            "-outfmt", "5",  # XML
            "-evalue", str(evalue),
            "-max_target_seqs", str(max_hits),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"{program} failed: {r.stderr or r.stdout}")
        return _parse_blast_xml(r.stdout), " ".join(cmd)
    finally:
        try:
            os.unlink(qpath)
        except Exception:
            pass


# ─── Remote BLAST (NCBI URL API) ────────────────────────────────────

_NCBI_BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"


def _extract_rid(body: str) -> tuple[str | None, int | None, str | None]:
    """Extract (RID, RTOE, status) from NCBI's submit response.

    NCBI wraps the real RID inside a comment block:
        <!--QBlastInfoBegin
            RID = ABC123
            RTOE = 27
        QBlastInfoEnd-->
    The bare regex `RID=\\S+` can match unrelated RID= tokens elsewhere in the
    HTML/JS, so we scope extraction to the QBlastInfo block. Returns the RID, the
    estimated time, and any 'Status' the page reports (READY/WAITING/etc.).
    """
    block_m = re.search(r"QBlastInfoBegin(.*?)QBlastInfoEnd", body, re.DOTALL)
    scope = block_m.group(1) if block_m else body
    rid_m = re.search(r"RID\s*=\s*([A-Z0-9]+)", scope)
    rtoe_m = re.search(r"RTOE\s*=\s*(\d+)", scope)
    status_m = re.search(r"Status\s*=\s*(\w+)", body)
    rid = rid_m.group(1) if rid_m else None
    rtoe = int(rtoe_m.group(1)) if rtoe_m else None
    status = status_m.group(1) if status_m else None
    return rid, rtoe, status


def _remote_blast_submit(query_text: str, program: str, database: str,
                         entrez_query: str | None = None, log=None,
                         evalue: float | None = None,
                         word_size: int | None = None) -> str:
    fields = {
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": database,
        "QUERY": query_text,
    }
    if entrez_query:
        # Scope the search to e.g. a clade: "txid11019[Organism]". This is the
        # supported way to restrict an NCBI URL-API BLAST to a taxon.
        fields["ENTREZ_QUERY"] = entrez_query
    if evalue is not None:
        fields["EXPECT"] = str(evalue)
    if word_size is not None:
        fields["WORD_SIZE"] = str(word_size)
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        _NCBI_BLAST_URL, data=data,
        headers={"User-Agent": "sentinel-bio/1.0"},
    )
    if log:
        log(f"NCBI BLAST submit: program={program} db={database}"
            + (f" entrez_query={entrez_query!r}" if entrez_query else "")
            + (f" expect={evalue}" if evalue is not None else "")
            + (f" word_size={word_size}" if word_size is not None else ""))
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    rid, rtoe, _status = _extract_rid(body)
    if not rid:
        # Surface a chunk of the actual response so the failure is debuggable.
        raise RuntimeError(f"NCBI BLAST submit failed (no RID found). "
                           f"Response head: {body[:600]}")
    if log:
        eta = f", est. {rtoe}s" if rtoe else ""
        log(f"NCBI BLAST queued: RID={rid}{eta}")
    return rid


def _remote_blast_wait_and_fetch(rid: str, max_wait_s: int = 600, log=None) -> str:
    """Poll NCBI for results, return XML. Polls every 20s up to max_wait_s."""
    start = time.time()
    polls = 0
    while True:
        if time.time() - start > max_wait_s:
            raise RuntimeError(f"NCBI BLAST timed out (RID={rid})")
        # Check status
        params = urllib.parse.urlencode({"CMD": "Get", "RID": rid, "FORMAT_OBJECT": "SearchInfo"})
        req = urllib.request.Request(
            f"{_NCBI_BLAST_URL}?{params}",
            headers={"User-Agent": "sentinel-bio/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        if "Status=WAITING" in body:
            polls += 1
            if log:
                log(f"NCBI BLAST waiting (RID={rid}, {int(time.time()-start)}s elapsed)…")
            time.sleep(20)
            continue
        if "Status=FAILED" in body:
            raise RuntimeError(f"NCBI BLAST job failed (RID={rid})")
        if "Status=UNKNOWN" in body:
            raise RuntimeError(f"NCBI BLAST RID expired or unknown ({rid})")
        if "Status=READY" in body:
            if log:
                log(f"NCBI BLAST ready (RID={rid})")
            break
        time.sleep(20)
    # Fetch the XML
    params = urllib.parse.urlencode({"CMD": "Get", "RID": rid, "FORMAT_TYPE": "XML"})
    req = urllib.request.Request(
        f"{_NCBI_BLAST_URL}?{params}",
        headers={"User-Agent": "sentinel-bio/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _run_remote_blast(query_text: str, program: str, database: str,
                      entrez_query: str | None = None, log=None,
                      evalue: float | None = None,
                      word_size: int | None = None) -> tuple[list[dict[str, Any]], str]:
    rid = _remote_blast_submit(query_text, program, database, entrez_query, log,
                               evalue=evalue, word_size=word_size)
    xml = _remote_blast_wait_and_fetch(rid, log=log)
    hits = _parse_blast_xml(xml)
    if log:
        if hits:
            log(f"NCBI BLAST parsed {len(hits)} hit(s) from RID={rid}")
        else:
            # Distinguish "search ran, genuinely zero hits" from a broken parse:
            # a valid empty result still contains the BlastOutput shell.
            valid = "<BlastOutput>" in xml or "BlastOutput_iterations" in xml
            if valid:
                log(f"NCBI BLAST completed with 0 hits (RID={rid}). The search "
                    "succeeded but nothing passed threshold — try a protein-level "
                    "program, a higher e-value, or a smaller word size.")
            else:
                log(f"NCBI BLAST returned no parseable results (RID={rid}). "
                    f"Response head: {xml[:300]}")
    return hits, f"NCBI BLAST {program} vs {database} (RID={rid})"


# ─── Tool entry ─────────────────────────────────────────────────────

async def run(inputs: dict[str, Any], params: dict[str, Any], project_id: str, log=None) -> dict[str, Any]:
    def _emit(m: str) -> None:
        if log:
            log(m)

    mode = (params.get("mode") or "local").strip()
    program = (params.get("program") or "blastn").strip()
    evalue = float(params.get("evalue", 1e-10))
    max_hits = int(params.get("max_hits", 25))
    extract_best = bool(params.get("extract_best_hit", True))
    flank_bp = int(params.get("flank_bp", 0))

    query_text, query_label = _resolve_query(params)
    _emit(f"mode={mode}  program={program}  query={query_label}  e-value={evalue}")

    if mode == "remote":
        database = (params.get("database") or "refseq_genomic").strip()
        _emit(f"submitting remote BLAST to NCBI (db={database}) — this can take 1-5 min…")
        hits, cmd = await asyncio.to_thread(_run_remote_blast, query_text, program, database)
        _emit(f"remote BLAST returned {len(hits)} hit(s)")
        result: dict[str, Any] = {
            "mode": "remote",
            "program": program,
            "database": database,
            "hits": hits[:max_hits],
            "n_hits": len(hits),
            "best_hit": hits[0] if hits else None,
            "cmd": cmd,
            "output_files": [],
        }
        return result

    # Local mode.
    subject_file_id = (params.get("subject_file_id") or "").strip()
    if not subject_file_id:
        raise ValueError(
            "local BLAST needs subject_file_id (the FASTA to search against, "
            "typically a project genome assembly)"
        )
    _emit("resolving subject file…")
    subject_path, subject_label = await asyncio.to_thread(
        _resolve_subject_path, subject_file_id, project_id,
    )
    _emit(f"subject={subject_label}; building/loading BLAST DB and searching…")

    hits, cmd = await asyncio.to_thread(
        _run_local_blast, query_text, subject_path, program, evalue, max_hits,
    )
    _emit(f"local BLAST returned {len(hits)} hit(s)")

    output_files: list[dict[str, Any]] = []
    if extract_best and hits:
        best = hits[0]
        # Subject ID is the first token of "Hit_def"
        sub_id = best["subject"].split()[0] if best["subject"] else ""
        start = max(1, best["sub_start"] - flank_bp)
        end = best["sub_end"] + flank_bp
        _emit(f"extracting best-hit region {sub_id}:{start}-{end} ({best['sub_strand']})")
        try:
            region_fasta = await asyncio.to_thread(
                _extract_region_fasta, subject_path, sub_id, start, end, best["sub_strand"],
            )
            output_files.append({
                "name": f"blast_{query_label}_in_{subject_label}_{sub_id}_{start}-{end}.fasta",
                "content": region_fasta,
                "kind": "fasta",
                "size": len(region_fasta),
            })
        except Exception as e:
            # Don't fail the whole run if extraction fails — hits are still useful.
            _emit(f"region extraction failed (non-fatal): {e}")
            print(f"[blast] region extraction failed: {e}")

    return {
        "mode": "local",
        "program": program,
        "subject_file_id": subject_file_id,
        "subject_label": subject_label,
        "query_label": query_label,
        "hits": hits[:max_hits],
        "n_hits": len(hits),
        "best_hit": hits[0] if hits else None,
        "cmd": cmd,
        "output_files": output_files,
    }


register(ToolDef(
    id="blast",
    label="BLAST (search)",
    description=(
        "Find a sequence in a genome or NCBI database. Use 'local' mode "
        "to find a gene in your uploaded assembly (e.g. human SF3B1 vs A. gazella "
        "genome). Use 'remote' to identify an unknown sequence via NCBI."
    ),
    input_kind="none",  # query can be a project file OR pasted text; we use params
    params=[
        ParamDef(name="mode", type="enum", label="Mode",
                 default="local", options=["local", "remote"],
                 help="local = against a project file. remote = against NCBI (slow)."),
        ParamDef(name="program", type="enum", label="Program",
                 default="blastn", options=["blastn", "tblastn", "blastp", "tblastx"],
                 help="blastn for DNA-vs-DNA. tblastn translates the subject (good for finding a protein in an unannotated genome)."),
        ParamDef(name="subject_file_id", type="project_file_fasta",
                 label="Subject (local mode): genome/FASTA to search in",
                 default="",
                 help="The project FASTA file to BLAST against. Typically your uploaded assembly."),
        ParamDef(name="query_text", type="text_long",
                 label="Query sequence",
                 default="",
                 help="FASTA-format query. The thing you're looking FOR."),
        ParamDef(name="database", type="string", label="NCBI database (remote mode)",
                 default="refseq_genomic",
                 help="refseq_genomic, nt, nr, refseq_rna, etc."),
        ParamDef(name="evalue", type="float", label="E-value cutoff",
                 default=1e-10, min=0.0, max=1.0,
                 help="Smaller = stricter. 1e-10 is conservative."),
        ParamDef(name="max_hits", type="int", label="Max hits to report",
                 default=25, min=1, max=500),
        ParamDef(name="extract_best_hit", type="bool", label="Auto-extract best hit (local only)",
                 default=True,
                 help="Save the best-hit region as a new FASTA file you can MAFFT next."),
        ParamDef(name="flank_bp", type="int", label="Flanking bp on extracted region",
                 default=0, min=0, max=100000,
                 help="Pad the extracted region. Useful if your primers sit outside the gene's CDS."),
    ],
    run=run,
))
