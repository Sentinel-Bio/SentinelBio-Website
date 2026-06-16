<script lang="ts">
  import {
    listAssemblies, fetchNcbiAssemblyToProject,
    type Assembly, type ProjectSpecies, type QueuedJob,
  } from '$lib/api';
  import GeneFetchDialog from '$lib/GeneFetchDialog.svelte';

  interface Props {
    projectSlug: string;
    species: ProjectSpecies;
    onAdded?: () => void;
  }
  let { projectSlug, species, onAdded }: Props = $props();

  let loading = $state(false);
  let assemblies = $state<Assembly[]>([]);
  let error = $state<string | null>(null);
  let lastQueued = $state<QueuedJob | null>(null);

  const PAGE_SIZE = 10;
  let currentPage = $state(1);
  let typeFilter = $state<'all' | 'GCF' | 'GCA' | 'NUC'>('all');

  let geneMode = $state<Record<string, string>>({});
  let busyAccession = $state<string | null>(null);

  async function load() {
    if (!species.ncbi_tax_id) return;
    loading = true;
    error = null;
    try {
      assemblies = await listAssemblies(species.ncbi_tax_id);
    } catch (e) {
      error = e instanceof Error ? e.message : 'fetch failed';
    }
    loading = false;
  }

  const filterTypes = $derived.by(() => {
    const base: Array<'all' | 'GCF' | 'GCA' | 'NUC'> = ['all', 'GCF', 'GCA'];
    // Only surface the NUC pill when nuccore (viral/organelle) records exist,
    // so the eukaryote case isn't cluttered with an always-empty filter.
    if (assemblies.some((a) => a.type === 'NUC')) base.push('NUC');
    return base;
  });
  const hasViral = $derived(assemblies.some((a) => a.type === 'NUC'));

  const filtered = $derived.by(() => {
    if (typeFilter === 'all') return assemblies;
    return assemblies.filter((a) => a.type === typeFilter);
  });
  const totalPages = $derived(Math.max(1, Math.ceil(filtered.length / PAGE_SIZE)));
  const pageStart = $derived((currentPage - 1) * PAGE_SIZE);
  const pageAssemblies = $derived(filtered.slice(pageStart, pageStart + PAGE_SIZE));

  $effect(() => {
    const _ = typeFilter;
    currentPage = 1;
  });

  async function fetchWholeAssembly(asm: Assembly) {
    if (!species.ncbi_tax_id) return;
    if (asm.size_mb && asm.size_mb > 100) {
      if (!confirm(
        `This assembly is ~${asm.size_mb} MB compressed. ` +
        `Server will queue an async download — you can keep working while it runs. Continue?`
      )) return;
    }
    busyAccession = asm.accession;
    error = null;
    try {
      const result = await fetchNcbiAssemblyToProject(projectSlug, {
        accession: asm.accession,
        taxid: species.ncbi_tax_id,
        species_id: species.id,
      });
      // Queued — won't appear in files immediately. Surface a hint.
      lastQueued = result;
      onAdded?.();
    } catch (e) {
      error = e instanceof Error ? e.message : 'fetch failed';
    }
    busyAccession = null;
  }

  // Gene fetch dialog state. Opened by clicking the gene "fetch" button;
  // submits via the dialog's own confirm action.
  let geneDialogOpen = $state(false);
  let geneDialogSymbol = $state('');
  let geneDialogAccession = $state('');

  function openGeneDialog(asm: Assembly) {
    if (!species.ncbi_tax_id) return;
    const symbol = (geneMode[asm.accession] || '').trim();
    if (!symbol) {
      alert('enter a gene symbol (e.g. TP53)');
      return;
    }
    geneDialogSymbol = symbol;
    geneDialogAccession = asm.accession;
    geneDialogOpen = true;
  }

  function onGeneFetchDone() {
    // Clear the symbol input for that assembly row + refresh files.
    if (geneDialogAccession) {
      geneMode = { ...geneMode, [geneDialogAccession]: '' };
    }
    onAdded?.();
  }

  $effect(() => {
    load();
  });
</script>

<div class="asm-picker">
  <div class="asm-head">
    <span class="mono dim">NCBI assemblies</span>
    <div class="filter-group">
      {#each filterTypes as t}
        <button
          class="filter-btn"
          class:active={typeFilter === t}
          onclick={() => (typeFilter = t as typeof typeFilter)}
        >{t}</button>
      {/each}
    </div>
  </div>

  {#if lastQueued}
    <p class="queued mono small">
      Queued <strong>{lastQueued.accession}</strong> for download · run {lastQueued.run_id.slice(0, 8)}.
      Check the <strong>Tools</strong> tab for progress. Multiple downloads can run in parallel.
    </p>
  {/if}

  {#if hasViral}
    <p class="hint mono small">
      No genome assembly in NCBI's Assembly DB for this taxon — showing complete
      genome records from nuccore instead (typical for viruses &amp; organelles).
    </p>
  {/if}

  {#if loading}
    <p class="dim mono small">loading…</p>
  {:else if error}
    <p class="error mono small">{error}</p>
  {:else if assemblies.length === 0}
    <p class="dim mono small">No assemblies available for this species.</p>
  {:else}
    <ul class="asm-list">
      {#each pageAssemblies as asm (asm.accession)}
        <li class="asm-row">
          <div class="asm-info">
            <div class="asm-acc mono">
              <a href={asm.type === 'NUC'
                  ? `https://www.ncbi.nlm.nih.gov/nuccore/${asm.accession}`
                  : `https://www.ncbi.nlm.nih.gov/assembly/${asm.accession}`}
                target="_blank" rel="noreferrer">
                {asm.accession}
              </a>
              <span class="type-pill mono" class:gcf={asm.type === 'GCF'} class:gca={asm.type === 'GCA'} class:nuc={asm.type === 'NUC'}>
                {asm.type}
              </span>
            </div>
            <div class="asm-meta mono dim">
              {asm.name}
              {#if asm.level}· {asm.level}{/if}
              {#if asm.size_mb}· {asm.size_mb} MB{/if}
              {#if asm.submission_date}· {asm.submission_date.slice(0, 10)}{/if}
            </div>
          </div>
          <div class="asm-actions">
            <button
              class="fetch-btn"
              onclick={() => fetchWholeAssembly(asm)}
              disabled={busyAccession === asm.accession}
              title="Server downloads the full genomic FASTA (decompressed) and stores it in this project."
            >
              {busyAccession === asm.accession ? '…' : 'Fetch whole'}
            </button>
            <div class="gene-fetch">
              <input
                type="text"
                placeholder="gene (TP53)"
                bind:value={geneMode[asm.accession]}
                onkeydown={(e) => e.key === 'Enter' && openGeneDialog(asm)}
              />
              <button
                class="fetch-btn"
                onclick={() => openGeneDialog(asm)}
                title="Fetch FASTA for a specific gene in this species."
              >
                Gene…
              </button>
            </div>
          </div>
        </li>
      {/each}
    </ul>
    {#if totalPages > 1}
      <div class="pagination">
        <button
          class="page-btn"
          onclick={() => (currentPage = Math.max(1, currentPage - 1))}
          disabled={currentPage === 1}
        >‹</button>
        <span class="mono dim">page {currentPage} of {totalPages} · {filtered.length} assemblies</span>
        <button
          class="page-btn"
          onclick={() => (currentPage = Math.min(totalPages, currentPage + 1))}
          disabled={currentPage === totalPages}
        >›</button>
      </div>
    {/if}
  {/if}
</div>

<GeneFetchDialog
  open={geneDialogOpen}
  onClose={() => (geneDialogOpen = false)}
  onDone={onGeneFetchDone}
  {projectSlug}
  geneSymbol={geneDialogSymbol}
  taxid={species.ncbi_tax_id ?? 0}
  speciesId={species.id}
  speciesLabel={species.common_name || species.scientific_name || ''}
/>

<style>
  .asm-picker { padding: 0.6rem 0 0; }
  .asm-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
  }
  .filter-group { display: flex; gap: 0.2rem; }
  .filter-btn {
    padding: 0.15rem 0.5rem;
    font-size: 0.62rem;
    font-family: var(--font-mono);
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-dim);
  }
  .filter-btn.active {
    color: var(--accent);
    border-color: var(--accent);
    background: var(--accent-dim);
  }
  .asm-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.35rem; }
  .asm-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.4rem 0.5rem;
    background: var(--bg-inset);
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .asm-info { min-width: 0; flex: 1 1 12rem; }
  .asm-acc { display: flex; align-items: center; gap: 0.4rem; font-size: 0.78rem; }
  .asm-acc a { color: var(--accent); text-decoration: none; }
  .asm-acc a:hover { text-decoration: underline; }
  .type-pill {
    font-size: 0.58rem;
    padding: 0.08rem 0.4rem;
    border: 1px solid var(--border);
    color: var(--fg-dim);
  }
  .type-pill.gcf { border-color: #7fd89c; color: #7fd89c; }
  .type-pill.gca { border-color: #e0b060; color: #e0b060; }
  .type-pill.nuc { border-color: #8fb6e0; color: #8fb6e0; }
  .hint {
    background: var(--bg-inset);
    border-left: 2px solid #8fb6e0;
    color: var(--fg-muted);
    padding: 0.4rem 0.6rem;
    margin: 0.3rem 0 0.5rem;
    font-size: 0.72rem;
    line-height: 1.4;
  }
  .asm-meta { font-size: 0.62rem; margin-top: 0.12rem; }
  .asm-actions { display: flex; gap: 0.3rem; align-items: center; flex-wrap: wrap; }
  .gene-fetch { display: flex; gap: 0.2rem; align-items: center; }
  .gene-fetch input {
    width: 6rem;
    padding: 0.2rem 0.4rem;
    font-size: 0.72rem;
    font-family: var(--font-mono);
    background: var(--bg-base);
    border: 1px solid var(--border);
    color: var(--fg);
  }
  .fetch-btn {
    padding: 0.2rem 0.55rem;
    font-size: 0.68rem;
    font-family: var(--font-mono);
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
  }
  .fetch-btn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .fetch-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .pagination {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.6rem;
    padding-top: 0.4rem;
    border-top: 1px dashed var(--border);
  }
  .page-btn {
    padding: 0.15rem 0.55rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-dim);
    font-size: 0.9rem;
  }
  .page-btn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .page-btn:disabled { opacity: 0.3; cursor: not-allowed; }
  .error { color: #c93535; }
  .queued {
    background: var(--accent-dim);
    border-left: 2px solid var(--accent);
    color: var(--fg);
    padding: 0.5rem 0.7rem;
    margin: 0.5rem 0;
    font-size: 0.78rem;
  }
  .queued strong { color: var(--accent); }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
  .small { font-size: 0.78rem; }
</style>
