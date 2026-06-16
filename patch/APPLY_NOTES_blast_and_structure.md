# Sentinel Bio — BLAST fixes + structure-first aligner (apply notes)

Unzip over your live tree. No SQL. No code-level new deps, but the structural
aligner needs a binary (below).

Changed: backend/app/tools/{family_gene_pipeline,blast,struct_align}.py,
backend/app/sources/ncbi.py, frontend/src/lib/ProjectTools.svelte
New: backend/app/tools/usalign.py, frontend/src/lib/SuperpositionViewer.svelte

## 1. Why BLAST found nothing (fixed)

Two compounding causes:

a) **blastp + blank database silently became `blastp vs nt`** (a nucleotide DB),
   which is invalid → zero/garbage. The database now follows the PROGRAM:
   blastp/blastx → nr (protein), blastn/tblastn/tblastx → nt (nucleotide).

b) **blastn can't see diverged homology.** For MAYV vs CHIKV the nucleotide
   identity is too low for blastn (it targets HIGHLY similar sequences) even
   though the proteins/structures are clearly homologous. The tool now:
   - warns when you run blastn (suggests blastp/tblastn for cross-genus),
   - defaults e-value to 10 (web default) instead of 1e-10, so diverged hits
     survive; new `evalue` + `word_size` params let you loosen further,
   - logs "0 hits, search SUCCEEDED" distinctly from a parse failure.

Also fixed: the **RID extraction** now reads NCBI's `QBlastInfo` block instead of
the first `RID=` in the page (which could grab a decoy token from page JS),
and the submit/poll path logs each step (RID, ETA, waiting polls) so it never
looks frozen and real NCBI errors propagate into the run log.

**For your case:** query = E2 amino-acid sequence, strategy blast_clade, leave
program blank (auto → tblastn vs nt) OR set blastp (→ nr). root_taxid = the
alphavirus clade. You should now get CHIKV/other-alphavirus hits.

## 2. Structure-first aligner (new) — your viewer request

`struct_align` is upgraded with a `method` param:
- **usalign (new default): aligns on 3D GEOMETRY** via US-align/TM-align, then
  reads the residue↔residue correspondence (sequence alignment) OFF the
  superposition. This is the correct approach for diverged homologs where
  sequence alignment fails — exactly MAYV vs CHIKV.
- **seq_guided (legacy):** the old sequence-aligned Cα superposition.

Outputs: per-pair TM-score / RMSD / structure-based %ID, each superposed
structure as PDB (shared reference frame), and the structure-derived alignment
FASTA.

**Viewer:** the run's "View" now opens a Mol* SuperpositionViewer that loads the
reference + all superposed structures together (color-coded, with a legend), a
metrics table, and the structure-derived alignment in a linked panel. Everything
is downloadable from the Files tab.

### REQUIRED: install US-align (or TM-align)
The aligner is an external binary, like mafft/iqtree/blast. Install on the
machine running the tool worker:
```
pixi add -c bioconda usalign      # preferred (also does multimer)
# or
pixi add -c bioconda tmalign      # fallback; the tool auto-detects either
```
Without it, usalign mode fails with a clear message; seq_guided still works.

For "find similar structures in a database" (vs "align these N structures"),
FoldSeek is the right tool — deferred to a separate pass per our discussion.

## Still deferred
Disk genome cache (gzip + hash-dedup, CRAM seam for eukaryote genomes).
