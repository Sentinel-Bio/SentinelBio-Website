<script lang="ts">
  import {
    listProjectFiles, uploadProjectFile, deleteProjectFile, downloadProjectFile,
    type ProjectSpecies, type ProjectFile,
  } from '$lib/api';
  import AssemblyPicker from '$lib/AssemblyPicker.svelte';

  interface Props {
    projectSlug: string;
    species: ProjectSpecies;
    isOwner: boolean;
  }
  let { projectSlug, species, isOwner }: Props = $props();

  let files = $state<ProjectFile[]>([]);
  let uploading = $state(false);
  let error = $state<string | null>(null);
  let showAssemblies = $state(false);
  let loading = $state(false);

  async function load() {
    loading = true;
    error = null;
    try {
      const all = await listProjectFiles(projectSlug, { species_id: species.id });
      // Only show FASTA / FASTQ-like for this panel.
      files = all.filter((f) => f.mime_hint === 'fasta' || f.mime_hint === 'fastq');
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
    loading = false;
  }

  $effect(() => {
    void species.id;
    load();
  });

  async function onUpload(e: Event) {
    const input = e.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    uploading = true;
    error = null;
    try {
      await uploadProjectFile(projectSlug, { file, species_id: species.id });
      await load();
    } catch (e) {
      error = e instanceof Error ? e.message : 'upload failed';
    }
    uploading = false;
    input.value = '';
  }

  async function download(f: ProjectFile) {
    try {
      await downloadProjectFile(projectSlug, f);
    } catch (e) {
      error = e instanceof Error ? e.message : 'download failed';
    }
  }

  async function remove(f: ProjectFile) {
    if (!confirm(`Remove ${f.name}? File will be deleted from the server.`)) return;
    try {
      await deleteProjectFile(projectSlug, f.id);
      await load();
    } catch (e) {
      error = e instanceof Error ? e.message : 'delete failed';
    }
  }

  function bytesPretty(n: number): string {
    if (n > 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + ' GB';
    if (n > 1_000_000) return (n / 1_000_000).toFixed(1) + ' MB';
    if (n > 1_000) return (n / 1_000).toFixed(0) + ' KB';
    return n + ' B';
  }
</script>

<div class="fasta-panel">
  <div class="fasta-head">
    <span class="mono dim">FASTA files ({files.length})</span>
    {#if isOwner}
      <div class="fasta-actions">
        <button
          class="upload-btn"
          onclick={() => (showAssemblies = !showAssemblies)}
        >{showAssemblies ? 'Hide NCBI' : 'Fetch NCBI'}</button>
        <label class="upload-btn">
          {uploading ? 'uploading…' : '+ Upload'}
          <input
            type="file"
            accept=".fa,.fasta,.fna,.ffn,.faa,.frn,.txt,.fastq,.fq"
            onchange={onUpload}
            disabled={uploading}
            hidden
          />
        </label>
      </div>
    {/if}
  </div>

  {#if showAssemblies}
    <AssemblyPicker
      {projectSlug}
      {species}
      onAdded={() => load()}
    />
  {/if}

  {#if loading}
    <p class="dim mono hint">loading…</p>
  {:else if files.length === 0}
    <p class="dim hint">No FASTA attached yet.</p>
  {:else}
    <ul class="ref-list">
      {#each files as f (f.id)}
        <li class="ref-row">
          <div class="ref-info">
            <div class="ref-name mono">{f.name}</div>
            <div class="ref-meta mono dim">
              {f.source}
              {#if f.source_metadata?.accession}· {f.source_metadata.accession}{/if}
              {#if f.source_metadata?.gene}· {f.source_metadata.gene}{/if}
              · {bytesPretty(f.size)}
            </div>
          </div>
          <div class="ref-actions">
            <button class="mini" onclick={() => download(f)}>↓</button>
            {#if isOwner}
              <button class="mini danger" onclick={() => remove(f)}>×</button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  {#if error}<p class="error mono">{error}</p>{/if}
</div>

<style>
  .fasta-panel {
    margin-top: 0.6rem;
    padding-top: 0.6rem;
    border-top: 1px dashed var(--border);
  }
  .fasta-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.4rem;
  }
  .upload-btn {
    padding: 0.2rem 0.65rem;
    font-size: 0.7rem;
    font-family: var(--font-mono);
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
    display: inline-block;
  }
  .upload-btn:hover { color: var(--accent); border-color: var(--accent); }

  .hint { margin: 0.4rem 0 0; font-size: 0.78rem; }

  .ref-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.3rem; }
  .ref-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.4rem 0.5rem;
    background: var(--bg-inset);
    font-size: 0.8rem;
  }
  .ref-info { min-width: 0; flex: 1; }
  .ref-name { font-size: 0.82rem; color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ref-meta { font-size: 0.65rem; margin-top: 0.1rem; }

  .ref-actions { display: flex; gap: 0.2rem; flex-shrink: 0; }
  .mini {
    padding: 0.12rem 0.45rem;
    font-size: 0.75rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mini.danger:hover { color: #c93535; border-color: #c93535; }

  .error { color: #c93535; font-size: 0.78rem; margin: 0.4rem 0 0; }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }

  .fasta-actions { display: flex; gap: 0.3rem; }
</style>
