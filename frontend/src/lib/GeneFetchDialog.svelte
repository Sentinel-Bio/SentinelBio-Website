<script lang="ts">
  import Dialog from '$lib/Dialog.svelte';
  import { fetchNcbiGeneToProject, type GeneFetchResult } from '$lib/api';

  interface Props {
    open: boolean;
    onClose: () => void;
    onDone?: (result: GeneFetchResult) => void;
    projectSlug: string;
    geneSymbol: string;
    taxid: number;
    speciesId?: string | null;
    speciesLabel?: string;
  }
  let {
    open, onClose, onDone,
    projectSlug, geneSymbol, taxid,
    speciesId = null, speciesLabel = '',
  }: Props = $props();

  let saveMrna = $state(false);
  let saveGenomicRegion = $state(false);
  let genomicFlankBp = $state(2000);
  let extractCds = $state(false);
  let saveProtein = $state(false);
  let saveGenbank = $state(false);

  let busy = $state(false);
  let error = $state<string | null>(null);

  const anySelected = $derived(
    saveMrna || saveGenomicRegion || extractCds || saveProtein || saveGenbank
  );

  const totalFiles = $derived(
    (saveMrna ? 1 : 0)
    + (saveGenomicRegion ? 1 : 0)
    + (extractCds ? 1 : 0)
    + (saveProtein ? 1 : 0)
    + (saveGenbank ? 1 : 0)
  );

  // Reset on re-open
  $effect(() => {
    const _ = [open, geneSymbol, taxid];
    if (open) {
      saveMrna = false;
      saveGenomicRegion = false;
      genomicFlankBp = 2000;
      extractCds = false;
      saveProtein = false;
      saveGenbank = false;
      error = null;
      busy = false;
    }
  });

  let lastResult = $state<{ selected_accession?: string; selected_title?: string } | null>(null);

  async function confirm() {
    if (!anySelected) return;
    busy = true;
    error = null;
    lastResult = null;
    try {
      const result = await fetchNcbiGeneToProject(projectSlug, {
        gene_symbol: geneSymbol,
        taxid,
        species_id: speciesId ?? undefined,
        save_mrna: saveMrna,
        save_genomic_region: saveGenomicRegion,
        genomic_flank_bp: genomicFlankBp,
        extract_cds: extractCds,
        save_protein: saveProtein,
        save_genbank: saveGenbank,
      });
      if (result.selected_accession) {
        lastResult = {
          selected_accession: result.selected_accession,
          selected_title: result.selected_title,
        };
      }
      if (result.warnings && result.warnings.length > 0) {
        error = result.warnings.join(' · ');
        busy = false;
        onDone?.(result);
        return;
      }
      onDone?.(result);
      onClose();
    } catch (e) {
      error = e instanceof Error ? e.message : 'fetch failed';
    }
    busy = false;
  }
</script>

<Dialog {open} {onClose} title="Fetch gene from NCBI">
  <div class="form">
    <div class="meta mono small">
      <span class="dim">gene</span> <strong>{geneSymbol}</strong>
      <span class="dim sep">·</span>
      <span class="dim">taxid</span> {taxid}
      {#if speciesLabel}<span class="dim sep">·</span> {speciesLabel}{/if}
    </div>

    <p class="instruction mono small dim">Choose what to download. Pick as many as you want.</p>

    <div class="opts">

      <label class="opt">
        <input type="checkbox" bind:checked={saveGenomicRegion} />
        <div class="opt-body">
          <div class="opt-title mono">Genomic DNA region</div>
          <div class="opt-desc mono small dim">
            Chromosomal DNA containing the gene — includes introns, flanking sequence.
            Use for primer design or when you need the full locus context.
          </div>
          {#if saveGenomicRegion}
            <div class="flank-row mono small">
              <label for="flank">Flank each side by</label>
              <input
                id="flank"
                type="number"
                bind:value={genomicFlankBp}
                min="0" max="100000" step="500"
              />
              <span class="dim">bp</span>
            </div>
          {/if}
        </div>
      </label>

      <label class="opt">
        <input type="checkbox" bind:checked={saveMrna} />
        <div class="opt-body">
          <div class="opt-title mono">mRNA FASTA</div>
          <div class="opt-desc mono small dim">
            Processed transcript (spliced). Includes 5′ UTR + CDS + 3′ UTR.
            No introns. Use as reference or for expression-level alignment.
          </div>
        </div>
      </label>

      <label class="opt">
        <input type="checkbox" bind:checked={extractCds} />
        <div class="opt-body">
          <div class="opt-title mono">CDS (coding sequence)</div>
          <div class="opt-desc mono small dim">
            Only the protein-coding part of the mRNA, spliced and in-frame.
            Best input for codon-aware alignment (HyPhy / dN/dS).
          </div>
        </div>
      </label>

      <label class="opt">
        <input type="checkbox" bind:checked={saveProtein} />
        <div class="opt-body">
          <div class="opt-title mono">Protein FASTA</div>
          <div class="opt-desc mono small dim">
            NCBI's canonical translation from the CDS. Use for protein-level
            alignment, AlphaFold input, or PROVEAN analysis.
          </div>
        </div>
      </label>

      <label class="opt">
        <input type="checkbox" bind:checked={saveGenbank} />
        <div class="opt-body">
          <div class="opt-title mono">Annotated GenBank record (.gb)</div>
          <div class="opt-desc mono small dim">
            Full record with feature table (CDS, exon coordinates, misc notes).
            Useful for running translate_dna in auto mode or manual inspection.
          </div>
        </div>
      </label>

    </div>

    {#if anySelected}
      <p class="count mono small">
        Will save <strong>{totalFiles}</strong> file{totalFiles === 1 ? '' : 's'}.
      </p>
    {:else}
      <p class="count mono small warn">Select at least one output.</p>
    {/if}

    {#if lastResult?.selected_accession}
      <p class="accession mono small">
        <span class="dim">Using:</span>
        <strong>{lastResult.selected_accession}</strong>
        {#if lastResult.selected_title}
          <span class="dim"> · {lastResult.selected_title}</span>
        {/if}
      </p>
    {/if}

    {#if error}<p class="error mono small">{error}</p>{/if}

    <div class="actions">
      <button class="secondary mono" onclick={onClose} disabled={busy}>Cancel</button>
      <button
        class="primary mono"
        onclick={confirm}
        disabled={busy || !anySelected}
      >
        {busy ? 'Fetching…' : 'Fetch'}
      </button>
    </div>
  </div>
</Dialog>

<style>
  .form { display: flex; flex-direction: column; gap: 0.65rem; }

  .meta {
    padding: 0.4rem 0.65rem;
    background: var(--bg-inset);
    border-left: 2px solid var(--accent);
  }
  .meta strong { color: var(--accent); }
  .sep { margin: 0 0.25rem; }
  .instruction { margin: 0; }

  .opts { display: flex; flex-direction: column; gap: 0.4rem; }
  .opt {
    display: flex;
    gap: 0.6rem;
    align-items: flex-start;
    padding: 0.55rem 0.7rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    cursor: pointer;
  }
  .opt:has(input:checked) { border-color: var(--border-strong); }
  .opt:hover { border-color: var(--border-strong); }
  .opt input[type="checkbox"] { margin-top: 3px; flex-shrink: 0; }
  .opt-body { flex: 1; min-width: 0; }
  .opt-title { font-size: 0.82rem; }
  .opt-desc { margin-top: 0.15rem; line-height: 1.35; }

  .flank-row {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-top: 0.5rem;
  }
  .flank-row input[type="number"] {
    width: 80px;
    padding: 0.2rem 0.4rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.78rem;
  }

  .accession {
    padding: 0.3rem 0.5rem;
    background: var(--bg-inset);
    border-left: 2px solid var(--border-strong);
    margin: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .accession strong { color: var(--fg); }
  .count { margin: 0; color: var(--fg-muted); }
  .count strong { color: var(--accent); }
  .warn { color: #e0b060 !important; }
  .error { color: #c93535; margin: 0; font-size: 0.72rem; }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.2rem;
    padding-top: 0.5rem;
    border-top: 1px dashed var(--border);
  }
  .primary, .secondary {
    padding: 0.4rem 0.9rem;
    font-size: 0.78rem;
    border: 1px solid var(--border-strong);
    background: transparent;
    color: var(--fg-muted);
    cursor: pointer;
  }
  .primary {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent);
  }
  .primary:hover:not(:disabled) { background: var(--accent); color: var(--bg-base); }
  .primary:disabled, .secondary:disabled { opacity: 0.4; cursor: not-allowed; }
  .secondary:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
  .small { font-size: 0.72rem; }
</style>
