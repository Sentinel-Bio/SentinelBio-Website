<script lang="ts">
  import {
    updateStructureRefs, searchStructuresByGene, getStructureMeta,
    uploadProjectFile, listProjectFiles, getProjectFileText, downloadProjectFile,
    type ProjectSpecies, type StructureRef, type StructureMeta, type ProjectFile,
  } from '$lib/api';
  import StructureViewer from '$lib/StructureViewer.svelte';

  interface Props {
    projectSlug: string;
    species: ProjectSpecies;
    isOwner: boolean;
  }
  let { projectSlug, species, isOwner }: Props = $props();

  // RCSB-by-pdb-id refs are stored in project_species.structure_refs (jsonb).
  let refs = $state<StructureRef[]>([...(species._structure_refs ?? [])]);
  // Uploaded PDB/CIF files are listed via project_files filtered by species_id + mime_hint.
  let uploadedFiles = $state<ProjectFile[]>([]);

  let showPanel = $state(false);
  let pdbIdInput = $state('');
  let geneSearchInput = $state('');
  let searchResults = $state<StructureMeta[]>([]);
  let searching = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);

  let viewingPdbId = $state<string | null>(null);
  let viewingFile = $state<ProjectFile | null>(null);
  let viewerStructureData = $state<string | null>(null);
  let viewerFormat = $state<'pdb' | 'cif' | 'mmcif'>('cif');
  let viewerName = $state('');

  $effect(() => {
    void species.id;
    loadFiles();
  });

  async function loadFiles() {
    try {
      const all = await listProjectFiles(projectSlug, { species_id: species.id });
      uploadedFiles = all.filter((f) => f.mime_hint === 'pdb' || f.mime_hint === 'cif');
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
  }

  async function saveRefs(next: StructureRef[]) {
    refs = next;
    await updateStructureRefs(projectSlug, species.id, next);
  }

  async function addByPdbId() {
    const id = pdbIdInput.trim().toUpperCase();
    if (!id) return;
    busy = true;
    error = null;
    try {
      const meta = await getStructureMeta(id);
      const ref: StructureRef = {
        name: `${id} · ${meta.title.slice(0, 60)}`,
        source: 'rcsb',
        pdb_id: id,
        format: 'cif',
        description: meta.title,
        added_at: new Date().toISOString(),
      };
      await saveRefs([...refs.filter((r) => r.pdb_id !== id), ref]);
      pdbIdInput = '';
    } catch (e) {
      error = e instanceof Error ? e.message : 'failed';
    }
    busy = false;
  }

  async function searchByGene() {
    const gene = geneSearchInput.trim();
    if (!gene) return;
    searching = true;
    error = null;
    try {
      searchResults = await searchStructuresByGene(gene);
    } catch (e) {
      error = e instanceof Error ? e.message : 'search failed';
    }
    searching = false;
  }

  async function addSearchResult(meta: StructureMeta) {
    const ref: StructureRef = {
      name: `${meta.pdb_id} · ${meta.title.slice(0, 60)}`,
      source: 'rcsb',
      pdb_id: meta.pdb_id,
      format: 'cif',
      description: meta.title,
      added_at: new Date().toISOString(),
    };
    await saveRefs([...refs.filter((r) => r.pdb_id !== meta.pdb_id), ref]);
  }

  async function uploadFile(e: Event) {
    const input = e.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    busy = true;
    error = null;
    try {
      await uploadProjectFile(projectSlug, { file, species_id: species.id });
      await loadFiles();
    } catch (e) {
      error = e instanceof Error ? e.message : 'upload failed';
    }
    busy = false;
    input.value = '';
  }

  async function viewRcsb(ref: StructureRef) {
    if (!ref.pdb_id) return;
    viewingPdbId = ref.pdb_id;
    viewingFile = null;
    viewerStructureData = null;
    viewerFormat = ref.format;
    viewerName = ref.name;
  }

  async function viewFile(f: ProjectFile) {
    try {
      const data = await getProjectFileText(projectSlug, f.id);
      viewingFile = f;
      viewingPdbId = null;
      viewerStructureData = data.text;
      viewerFormat = f.mime_hint === 'cif' ? 'cif' : 'pdb';
      viewerName = f.name;
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
  }

  function closeViewer() {
    viewingPdbId = null;
    viewingFile = null;
    viewerStructureData = null;
  }

  async function removeRcsbRef(ref: StructureRef) {
    if (!confirm(`Remove ${ref.name}?`)) return;
    await saveRefs(refs.filter((r) => r.pdb_id !== ref.pdb_id));
  }

  async function removeUploadedFile(f: ProjectFile) {
    if (!confirm(`Remove ${f.name}? File will be deleted from the server.`)) return;
    try {
      const { deleteProjectFile } = await import('$lib/api');
      await deleteProjectFile(projectSlug, f.id);
      await loadFiles();
    } catch (e) {
      error = e instanceof Error ? e.message : 'delete failed';
    }
  }
</script>

<div class="structure-panel">
  <div class="structure-head">
    <span class="mono dim">Structures ({refs.length + uploadedFiles.length})</span>
    {#if isOwner}
      <button class="upload-btn" onclick={() => (showPanel = !showPanel)}>
        {showPanel ? 'Close' : '+ Add'}
      </button>
    {/if}
  </div>

  {#if showPanel && isOwner}
    <div class="add-section">
      <div class="add-row">
        <input
          type="text"
          bind:value={pdbIdInput}
          placeholder="PDB ID (e.g. 1TUP)"
          onkeydown={(e) => e.key === 'Enter' && addByPdbId()}
        />
        <button class="fetch-btn" onclick={addByPdbId} disabled={busy}>Add</button>
      </div>

      <div class="add-row">
        <input
          type="text"
          bind:value={geneSearchInput}
          placeholder="Search RCSB by gene symbol"
          onkeydown={(e) => e.key === 'Enter' && searchByGene()}
        />
        <button class="fetch-btn" onclick={searchByGene} disabled={searching}>
          {searching ? '…' : 'Search'}
        </button>
      </div>

      {#if searchResults.length > 0}
        <ul class="search-results">
          {#each searchResults as r}
            <li class="result-row">
              <div>
                <div class="mono accent">{r.pdb_id}</div>
                <div class="title dim">{r.title.slice(0, 70)}</div>
                <div class="mono dim small">
                  {r.method}
                  {#if r.resolution}· {r.resolution.toFixed(2)} Å{/if}
                  {#if r.year}· {r.year}{/if}
                </div>
              </div>
              <button class="mini" onclick={() => addSearchResult(r)}>+ Add</button>
            </li>
          {/each}
        </ul>
      {/if}

      <label class="upload-btn file-upload">
        Upload PDB/CIF
        <input type="file" accept=".pdb,.cif,.mmcif" onchange={uploadFile} hidden />
      </label>
    </div>
  {/if}

  {#if refs.length > 0 || uploadedFiles.length > 0}
    <ul class="struct-list">
      {#each refs as ref}
        <li class="struct-row">
          <div class="struct-info">
            <div class="mono">{ref.name}</div>
            <div class="mono dim small">RCSB · {ref.format}</div>
          </div>
          <div class="struct-actions">
            <button class="mini" onclick={() => viewRcsb(ref)}>View</button>
            {#if isOwner}
              <button class="mini danger" onclick={() => removeRcsbRef(ref)}>×</button>
            {/if}
          </div>
        </li>
      {/each}
      {#each uploadedFiles as f}
        <li class="struct-row">
          <div class="struct-info">
            <div class="mono">{f.name}</div>
            <div class="mono dim small">upload · {f.mime_hint}</div>
          </div>
          <div class="struct-actions">
            <button class="mini" onclick={() => viewFile(f)}>View</button>
            <button class="mini" onclick={() => downloadProjectFile(projectSlug, f)}>↓</button>
            {#if isOwner}
              <button class="mini danger" onclick={() => removeUploadedFile(f)}>×</button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  {#if error}<p class="error mono">{error}</p>{/if}
</div>

{#if viewingPdbId || viewingFile}
  <div class="viewer-backdrop" onclick={closeViewer}>
    <div class="viewer-modal" onclick={(e) => e.stopPropagation()}>
      <div class="viewer-head">
        <div class="mono">{viewerName}</div>
        <button onclick={closeViewer}>Close</button>
      </div>
      <div class="viewer-body">
        <StructureViewer
          pdbId={viewingPdbId}
          structureData={viewerStructureData}
          format={viewerFormat}
        />
      </div>
    </div>
  </div>
{/if}

<style>
  .structure-panel { margin-top: 0.6rem; padding-top: 0.6rem; border-top: 1px dashed var(--border); }
  .structure-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; }

  .add-section {
    padding: 0.7rem;
    background: var(--bg-inset);
    margin-bottom: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .add-row { display: flex; gap: 0.3rem; }
  .add-row input {
    flex: 1;
    padding: 0.35rem 0.55rem;
    background: var(--bg-base);
    border: 1px solid var(--border);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.78rem;
  }
  .fetch-btn, .upload-btn {
    padding: 0.25rem 0.7rem;
    font-size: 0.72rem;
    font-family: var(--font-mono);
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
  }
  .fetch-btn:hover, .upload-btn:hover { color: var(--accent); border-color: var(--accent); }
  .file-upload { display: inline-block; text-align: center; }

  .search-results { list-style: none; padding: 0; margin: 0; max-height: 200px; overflow-y: auto; }
  .result-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 0.4rem 0;
    border-bottom: 1px dashed var(--border);
    gap: 0.5rem;
  }
  .result-row .accent { color: var(--accent); font-weight: 500; }
  .result-row .title { font-size: 0.8rem; }
  .result-row .small { font-size: 0.62rem; }

  .struct-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.3rem; }
  .struct-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.4rem 0.5rem;
    background: var(--bg-inset);
    font-size: 0.8rem;
  }
  .struct-info { min-width: 0; flex: 1; }
  .struct-actions { display: flex; gap: 0.2rem; flex-shrink: 0; }

  .mini {
    padding: 0.12rem 0.45rem;
    font-size: 0.72rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mini.danger:hover { color: #c93535; border-color: #c93535; }

  .small { font-size: 0.62rem; }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
  .error { color: #c93535; font-size: 0.78rem; margin: 0.3rem 0 0; }

  .viewer-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    z-index: 50;
    display: grid;
    place-items: center;
    padding: 1rem;
  }
  .viewer-modal {
    width: 90vw;
    max-width: 1200px;
    height: 85vh;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    display: flex;
    flex-direction: column;
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
