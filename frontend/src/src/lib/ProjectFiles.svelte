<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listProjectFiles, downloadProjectFile, deleteProjectFile, getProjectFileText,
    fetchGenbankByAccession, fetchGeneGenbank,
    type Project, type ProjectFile,
  } from '$lib/api';
  import GenomeViewer from '$lib/GenomeViewer.svelte';
  import StructureViewer from '$lib/StructureViewer.svelte';
  import UploadZone from '$lib/UploadZone.svelte';

  interface Props {
    project: Project;
    isOwner: boolean;
  }
  let { project, isOwner }: Props = $props();

  let files = $state<ProjectFile[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let kindFilter = $state<'all' | 'fasta' | 'fastq' | 'pdb' | 'cif' | 'newick' | 'annotation' | 'genbank' | 'other'>('all');

  // Viewer state
  let viewerOpen = $state(false);
  let viewerName = $state('');
  let viewerKind = $state<'fasta' | 'structure'>('fasta');
  let viewerFasta = $state<string | null>(null);
  let viewerGenbank = $state<string | null>(null);
  let viewerStructureData = $state<string | null>(null);
  let viewerStructureFormat = $state<'pdb' | 'cif' | 'mmcif'>('pdb');
  let viewerBusy = $state(false);

  onMount(load);

  async function load() {
    loading = true;
    error = null;
    try {
      files = await listProjectFiles(project.slug);
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
    loading = false;
  }

  const filtered = $derived(
    kindFilter === 'all' ? files : files.filter((f) => f.mime_hint === kindFilter)
  );

  async function download(f: ProjectFile) {
    try {
      await downloadProjectFile(project.slug, f);
    } catch (e) {
      error = e instanceof Error ? e.message : 'download failed';
    }
  }

  async function remove(f: ProjectFile) {
    if (!confirm(`Remove ${f.name}? File will be deleted from the server.`)) return;
    try {
      await deleteProjectFile(project.slug, f.id);
      await load();
    } catch (e) {
      error = e instanceof Error ? e.message : 'delete failed';
    }
  }

  async function viewFasta(f: ProjectFile, annotated: boolean) {
    viewerBusy = true;
    error = null;
    try {
      if (annotated) {
        // Get GenBank from NCBI via accession/gene metadata if available.
        const meta = f.source_metadata ?? {};
        let gb: string | null = null;
        if (meta.accession) {
          const result = await fetchGenbankByAccession(meta.accession);
          gb = result.genbank;
        } else if (meta.gene && meta.taxid) {
          const result = await fetchGeneGenbank(meta.taxid, meta.gene);
          gb = result.genbank;
        }
        if (!gb) {
          alert('This file has no NCBI accession/gene metadata. Use Plain view.');
          viewerBusy = false;
          return;
        }
        viewerKind = 'fasta';
        viewerName = f.name + ' · annotated';
        viewerFasta = null;
        viewerGenbank = gb;
        viewerOpen = true;
      } else {
        const data = await getProjectFileText(project.slug, f.id);
        viewerKind = 'fasta';
        viewerName = f.name;
        viewerFasta = data.text;
        viewerGenbank = null;
        viewerOpen = true;
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'load failed';
      if (msg.includes('413')) {
        alert(`${f.name} is too large to render in-browser. Download it instead.`);
      } else {
        alert(msg);
      }
    }
    viewerBusy = false;
  }

  async function viewStructure(f: ProjectFile) {
    viewerBusy = true;
    try {
      const data = await getProjectFileText(project.slug, f.id);
      viewerKind = 'structure';
      viewerName = f.name;
      viewerStructureData = data.text;
      viewerStructureFormat = f.mime_hint === 'cif' ? 'cif' : 'pdb';
      viewerOpen = true;
    } catch (e) {
      alert(e instanceof Error ? e.message : 'load failed');
    }
    viewerBusy = false;
  }

  function closeViewer() {
    viewerOpen = false;
    viewerFasta = null;
    viewerGenbank = null;
    viewerStructureData = null;
  }

  function bytesPretty(n: number): string {
    if (n > 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + ' GB';
    if (n > 1_000_000) return (n / 1_000_000).toFixed(1) + ' MB';
    if (n > 1_000) return (n / 1_000).toFixed(0) + ' KB';
    return n + ' B';
  }

  function sourceLabel(s: string): string {
    if (s === 'ncbi') return 'NCBI';
    if (s === 'tool') return 'tool output';
    if (s === 'upload') return 'upload';
    return s;
  }
</script>

<svelte:window onkeydown={(e) => { if (viewerOpen && e.key === 'Escape') closeViewer(); }} />

<div class="files-view">
  {#if isOwner}
    <UploadZone
      projectSlug={project.slug}
      onUploaded={() => load()}
    />
  {/if}

  <div class="section-head">
    <h3 class="section-title">Project files · {files.length}</h3>
    <div class="filters">
      {#each ['all', 'fasta', 'fastq', 'pdb', 'cif', 'newick', 'annotation', 'genbank', 'other'] as k}
        <button
          class="filter-btn"
          class:active={kindFilter === k}
          onclick={() => (kindFilter = k as typeof kindFilter)}
        >{k}</button>
      {/each}
    </div>
  </div>

  {#if loading}
    <p class="dim">Loading…</p>
  {:else if error}
    <p class="error mono">{error}</p>
  {:else if filtered.length === 0}
    <p class="dim">No files yet. Attach some via species cards on the Overview tab.</p>
  {:else}
    <ul class="file-list">
      {#each filtered as f (f.id)}
        <li class="file-row">
          <div class="file-info">
            <div class="file-name mono">{f.name}</div>
            <div class="file-meta mono dim">
              {f.mime_hint}
              {#if f.source_metadata?.category && f.source_metadata.category !== 'other'}
                / {f.source_metadata.category}
              {/if}
              · {sourceLabel(f.source)} · {bytesPretty(f.size)}
              {#if f.source_metadata?.accession}· {f.source_metadata.accession}{/if}
              {#if f.source_metadata?.gene}· {f.source_metadata.gene}{/if}
              {#if f.source_metadata?.tool}· from {f.source_metadata.tool}{/if}
            </div>
          </div>
          <div class="file-actions">
            {#if f.mime_hint === 'fasta'}
              <button class="mini" onclick={() => viewFasta(f, false)} disabled={viewerBusy}>View</button>
              {#if f.source_metadata?.accession || f.source_metadata?.gene}
                <button class="mini" onclick={() => viewFasta(f, true)} disabled={viewerBusy}>Annotated</button>
              {/if}
            {:else if f.mime_hint === 'pdb' || f.mime_hint === 'cif'}
              <button class="mini" onclick={() => viewStructure(f)} disabled={viewerBusy}>View 3D</button>
            {/if}
            <button class="mini" onclick={() => download(f)}>↓</button>
            {#if isOwner}
              <button class="mini danger" onclick={() => remove(f)}>×</button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

{#if viewerOpen}
  <div class="viewer-backdrop" onclick={closeViewer}>
    <div class="viewer-modal" onclick={(e) => e.stopPropagation()}>
      <div class="viewer-head">
        <div class="mono">{viewerName}</div>
        <button onclick={closeViewer}>Close</button>
      </div>
      <div class="viewer-body">
        {#if viewerKind === 'fasta' && (viewerFasta || viewerGenbank)}
          <GenomeViewer
            fasta={viewerFasta}
            genbank={viewerGenbank}
            name={viewerName}
            height={600}
          />
        {:else if viewerKind === 'structure' && viewerStructureData}
          <StructureViewer
            structureData={viewerStructureData}
            format={viewerStructureFormat}
          />
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .section-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--border);
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .section-title {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    margin: 0;
  }
  .filters { display: flex; gap: 0.2rem; flex-wrap: wrap; }
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

  .file-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.35rem; }
  .file-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0.7rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .file-info { min-width: 0; flex: 1 1 14rem; }
  .file-name { font-size: 0.85rem; color: var(--fg); }
  .file-meta { font-size: 0.66rem; margin-top: 0.15rem; }

  .file-actions { display: flex; gap: 0.3rem; flex-shrink: 0; }
  .mini {
    padding: 0.15rem 0.5rem;
    font-size: 0.7rem;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    font-family: var(--font-mono);
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mini.danger:hover { color: #c93535; border-color: #c93535; }
  .mini:disabled { opacity: 0.5; cursor: not-allowed; }

  .error { color: #c93535; }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }

  .viewer-backdrop {
    position: fixed; inset: 0;
    background: rgba(0, 0, 0, 0.7);
    z-index: 50;
    display: grid; place-items: center;
    padding: 1rem;
  }
  .viewer-modal {
    width: 90vw; max-width: 1400px;
    height: 85vh;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    display: flex; flex-direction: column;
  }
  .viewer-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
  }
  .viewer-body { flex: 1 1 auto; min-height: 0; }
</style>
