<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listProjectFiles, downloadProjectFile, deleteProjectFile, renameProjectFile,
    bulkDownloadFiles, getProjectFileText,
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
  let success = $state<string | null>(null);
  let kindFilter = $state<'all' | 'fasta' | 'fastq' | 'pdb' | 'cif' | 'newick' | 'annotation' | 'genbank' | 'other'>('all');

  // Multi-select state
  let selectedIds = $state<Set<string>>(new Set());
  let bulkDeleting = $state(false);
  let bulkProgress = $state({ done: 0, total: 0 });

  function toggleSelected(id: string) {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedIds = next;
  }

  function selectAllVisible() {
    selectedIds = new Set(filtered.map((f) => f.id));
  }

  function clearSelection() {
    selectedIds = new Set();
  }

  async function bulkDownload(format: 'zip' | 'tar.gz') {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    bulkDownloadBusy = true;
    error = null;
    try {
      await bulkDownloadFiles(project.slug, ids, format);
      success = `Downloaded ${ids.length} file${ids.length === 1 ? '' : 's'} as .${format}`;
      setTimeout(() => { success = null; }, 2500);
    } catch (e) {
      error = e instanceof Error ? e.message : 'download failed';
    }
    bulkDownloadBusy = false;
  }

  let bulkDownloadBusy = $state(false);

  async function bulkDelete() {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    if (!confirm(
      `Delete ${ids.length} file${ids.length === 1 ? '' : 's'}? This cannot be undone.`
    )) return;
    bulkDeleting = true;
    bulkProgress = { done: 0, total: ids.length };
    error = null;
    // Parallel delete with a small concurrency cap to be polite.
    const CONCURRENCY = 6;
    let cursor = 0;
    const failures: string[] = [];
    async function worker() {
      while (cursor < ids.length) {
        const idx = cursor++;
        const id = ids[idx];
        try {
          await deleteProjectFile(project.slug, id);
        } catch (e) {
          failures.push(id);
        }
        bulkProgress = { done: bulkProgress.done + 1, total: ids.length };
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));
    if (failures.length > 0) {
      error = `${failures.length} delete${failures.length === 1 ? '' : 's'} failed.`;
    }
    selectedIds = new Set();
    bulkDeleting = false;
    await load();
  }

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

  // ── View mode (grouping) ──────────────────────────────────────
  type ViewMode = 'by-species' | 'by-kind' | 'flat';
  let viewMode = $state<ViewMode>('by-species');

  // Per-group collapse state. Default: all groups expanded.
  let collapsedGroups = $state<Set<string>>(new Set());

  function toggleGroup(key: string) {
    const next = new Set(collapsedGroups);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    collapsedGroups = next;
  }

  function expandAll() { collapsedGroups = new Set(); }
  function collapseAll() {
    collapsedGroups = new Set(grouped.map((g) => g.key));
  }

  // Map species_id -> species record for label lookups.
  const speciesById = $derived.by(() => {
    const m = new Map<string, { id: string; scientific_name: string; common_name?: string }>();
    for (const s of (project.species ?? [])) {
      m.set(s.id, s);
    }
    return m;
  });

  function shortCode(scientificName: string): string {
    if (!scientificName) return 'Sp';
    const parts = scientificName.trim().split(/\s+/);
    if (parts.length < 2) return parts[0].slice(0, 4);
    return (parts[0][0]?.toUpperCase() ?? '') + parts[1].slice(0, 3).toLowerCase();
  }

  interface FileGroup {
    key: string;
    label: string;
    sublabel?: string;
    files: ProjectFile[];
  }

  const grouped = $derived.by<FileGroup[]>(() => {
    if (viewMode === 'flat') {
      return [{ key: '__all', label: `All files (${filtered.length})`, files: filtered }];
    }

    if (viewMode === 'by-kind') {
      const byKind = new Map<string, ProjectFile[]>();
      for (const f of filtered) {
        const meta = (f.source_metadata ?? {}) as Record<string, any>;
        const k = meta.kind || f.mime_hint || 'other';
        if (!byKind.has(k)) byKind.set(k, []);
        byKind.get(k)!.push(f);
      }
      // Sort: protein, cds, mrna, region, alignment, then alphabetic
      const priority = ['protein', 'cds', 'mrna', 'region', 'alignment', 'tree', 'gff', 'genbank'];
      const sorted = Array.from(byKind.entries()).sort((a, b) => {
        const ai = priority.indexOf(a[0]);
        const bi = priority.indexOf(b[0]);
        if (ai !== -1 && bi !== -1) return ai - bi;
        if (ai !== -1) return -1;
        if (bi !== -1) return 1;
        return a[0].localeCompare(b[0]);
      });
      return sorted.map(([k, fs]) => ({
        key: `kind:${k}`,
        label: k,
        sublabel: `${fs.length} file${fs.length === 1 ? '' : 's'}`,
        files: fs,
      }));
    }

    // by-species (default)
    const bySpecies = new Map<string, ProjectFile[]>();
    const unassigned: ProjectFile[] = [];
    for (const f of filtered) {
      if (!f.species_id) {
        unassigned.push(f);
        continue;
      }
      const sp = speciesById.get(f.species_id);
      if (!sp) {
        // Species exists in DB but not in this project's species list — treat as unassigned
        unassigned.push(f);
        continue;
      }
      const key = sp.id;
      if (!bySpecies.has(key)) bySpecies.set(key, []);
      bySpecies.get(key)!.push(f);
    }
    // Sort species groups alphabetically by short code.
    const speciesGroups: FileGroup[] = Array.from(bySpecies.entries())
      .map(([sid, fs]) => {
        const sp = speciesById.get(sid)!;
        const code = shortCode(sp.scientific_name);
        return {
          key: `sp:${sid}`,
          label: code,
          sublabel: `${sp.scientific_name} · ${fs.length} file${fs.length === 1 ? '' : 's'}`,
          files: fs,
        };
      })
      .sort((a, b) => a.label.localeCompare(b.label));

    if (unassigned.length > 0) {
      speciesGroups.push({
        key: '__unassigned',
        label: 'Project-wide / multi-species',
        sublabel: `${unassigned.length} file${unassigned.length === 1 ? '' : 's'}`,
        files: unassigned,
      });
    }
    return speciesGroups;
  });

  function selectAllInGroup(g: FileGroup) {
    const next = new Set(selectedIds);
    for (const f of g.files) next.add(f.id);
    selectedIds = next;
  }

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

  // Inline rename state
  let renamingId = $state<string | null>(null);
  let renameDraft = $state('');

  function startRename(f: ProjectFile) {
    renamingId = f.id;
    renameDraft = f.name;
  }

  function cancelRename() {
    renamingId = null;
    renameDraft = '';
  }

  async function commitRename(f: ProjectFile) {
    const newName = renameDraft.trim();
    if (!newName || newName === f.name) {
      cancelRename();
      return;
    }
    try {
      await renameProjectFile(project.slug, f.id, newName);
      await load();
      success = `Renamed to "${newName}"`;
      setTimeout(() => {
        success = null;
      }, 2500);
    } catch (e) {
      error = e instanceof Error ? e.message : 'rename failed';
    }
    cancelRename();
  }

  // Svelte action: focus + select an input when it mounts.
  function focusOnMount(node: HTMLInputElement) {
    node.focus();
    node.select();
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

  <div class="view-mode-bar mono small">
    <span class="dim">group by:</span>
    <button class="mini" class:active={viewMode === 'by-species'} onclick={() => (viewMode = 'by-species')}>species</button>
    <button class="mini" class:active={viewMode === 'by-kind'} onclick={() => (viewMode = 'by-kind')}>type</button>
    <button class="mini" class:active={viewMode === 'flat'} onclick={() => (viewMode = 'flat')}>none (flat)</button>
    {#if viewMode !== 'flat' && grouped.length > 1}
      <span class="dim">|</span>
      <button class="mini" onclick={expandAll}>expand all</button>
      <button class="mini" onclick={collapseAll}>collapse all</button>
    {/if}
  </div>

  {#if isOwner && filtered.length > 0}
    <div class="bulk-bar">
      <label class="bulk-check mono small">
        <input
          type="checkbox"
          checked={selectedIds.size > 0 && selectedIds.size === filtered.length}
          indeterminate={selectedIds.size > 0 && selectedIds.size < filtered.length}
          onchange={(e) => {
            if ((e.target as HTMLInputElement).checked) selectAllVisible();
            else clearSelection();
          }}
        />
        {#if selectedIds.size === 0}
          select files for bulk actions
        {:else}
          {selectedIds.size} selected
        {/if}
      </label>
      {#if selectedIds.size > 0}
        <div class="bulk-actions">
          {#if bulkDeleting}
            <span class="mono small dim">deleting {bulkProgress.done}/{bulkProgress.total}…</span>
          {:else if bulkDownloadBusy}
            <span class="mono small dim">archiving…</span>
          {:else}
            <button class="mini" onclick={() => bulkDownload('zip')} title="Download as .zip">
              ↓ .zip
            </button>
            <button class="mini" onclick={() => bulkDownload('tar.gz')} title="Download as .tar.gz">
              ↓ .tar.gz
            </button>
            <button class="mini danger" onclick={bulkDelete}>Delete {selectedIds.size}</button>
            <button class="mini" onclick={clearSelection}>Clear</button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}

  {#if loading}
    <p class="dim">Loading…</p>
  {:else if error}
    <p class="error mono">{error}</p>
  {:else if success}
    <p class="success mono">{success}</p>
  {:else if filtered.length === 0}
    <p class="dim">No files yet. Attach some via species cards on the Overview tab.</p>
  {:else}
    <div class="file-groups">
      {#each grouped as g (g.key)}
        {@const isCollapsed = collapsedGroups.has(g.key)}
        <section class="file-group" class:collapsed={isCollapsed}>
          <header class="group-head" onclick={() => toggleGroup(g.key)}>
            <span class="chevron mono" aria-hidden="true">{isCollapsed ? '▶' : '▼'}</span>
            <span class="group-label mono">
              {g.label}
              {#if g.sublabel}<span class="group-sub dim"> · {g.sublabel}</span>{/if}
            </span>
            {#if isOwner && !isCollapsed && g.files.length > 1}
              <button
                class="mini group-select"
                onclick={(e) => { e.stopPropagation(); selectAllInGroup(g); }}
                title="Select all files in this group"
              >Select all</button>
            {/if}
          </header>
          {#if !isCollapsed}
            <ul class="file-list">
              {#each g.files as f (f.id)}
                <li class="file-row" class:selected={selectedIds.has(f.id)}>
                  {#if isOwner}
                    <input
                      type="checkbox"
                      class="row-check"
                      checked={selectedIds.has(f.id)}
                      onchange={() => toggleSelected(f.id)}
                      aria-label="select file"
                    />
                  {/if}
                  <div class="file-info">
                    {#if renamingId === f.id}
                      <input
                        type="text"
                        class="rename-input mono"
                        bind:value={renameDraft}
                        onkeydown={(e) => {
                          if (e.key === 'Enter') commitRename(f);
                          else if (e.key === 'Escape') cancelRename();
                        }}
                        onblur={() => commitRename(f)}
                        use:focusOnMount
                      />
                    {:else}
                      <div
                        class="file-name mono"
                        ondblclick={() => isOwner && startRename(f)}
                        title={isOwner ? 'Double-click to rename' : ''}
                      >{f.name}</div>
                    {/if}
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
        </section>
      {/each}
    </div>
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

  .view-mode-bar {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    flex-wrap: wrap;
    padding: 0.35rem 0.5rem;
    margin-bottom: 0.4rem;
    background: var(--bg-base);
    border-bottom: 1px solid var(--border);
  }
  .file-groups {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
  }
  .file-group {
    border: 1px solid var(--border);
    background: var(--bg-base);
  }
  .group-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.45rem 0.6rem;
    background: var(--bg-inset);
    cursor: pointer;
    user-select: none;
    border-bottom: 1px solid var(--border);
  }
  .file-group.collapsed .group-head { border-bottom-color: transparent; }
  .group-head:hover { background: var(--bg-raised); }
  .chevron {
    color: var(--fg-muted);
    font-size: 0.65rem;
    width: 0.9rem;
    text-align: center;
  }
  .group-label {
    flex: 1;
    font-size: 0.85rem;
    color: var(--fg);
  }
  .group-sub { font-size: 0.7rem; }
  .group-select { margin-left: auto; }
  .file-group .file-list {
    padding: 0.5rem 0.6rem;
  }

  .mini.active {
    color: var(--accent);
    border-color: var(--accent);
    background: var(--accent-dim);
  }

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
  .file-row.selected {
    border-color: var(--accent);
    background: var(--accent-dim);
  }
  .row-check {
    flex-shrink: 0;
    margin-right: 0.1rem;
    cursor: pointer;
  }

  .bulk-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0.7rem;
    margin-bottom: 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
  }
  .bulk-check {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--fg-muted);
    cursor: pointer;
  }
  .bulk-actions { display: flex; gap: 0.3rem; align-items: center; }
  .small { font-size: 0.7rem; }
  .file-info { min-width: 0; flex: 1 1 14rem; }
  .file-name {
    font-size: 0.85rem;
    color: var(--fg);
    cursor: text;
    user-select: text;
  }
  .file-name:hover { color: var(--accent); }
  .file-meta { font-size: 0.66rem; margin-top: 0.15rem; }

  .rename-input {
    width: 100%;
    padding: 0.18rem 0.4rem;
    background: var(--bg-base);
    border: 1px solid var(--accent);
    color: var(--fg);
    font-size: 0.85rem;
    font-family: var(--font-mono);
  }
  .rename-input:focus { outline: none; }

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
  .success { color: #2d7a3a; }
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
