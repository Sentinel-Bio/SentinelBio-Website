<script lang="ts" module>
  export interface SelectedFile {
    id: string;          // ProjectFile.id
    name: string;
    species_name: string;
    species_id: string;
    size: number;
    gene?: string;
    kind?: string;
    tool?: string;
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listProjectFiles, getProjectFileFacets,
    type Project, type ProjectFile, type ProjectSpecies, type ProjectFileFacets,
  } from '$lib/api';

  interface Props {
    project: Project;
    onchange: (selection: SelectedFile[]) => void;
  }
  let { project, onchange }: Props = $props();

  let files = $state<ProjectFile[]>([]);
  let facets = $state<ProjectFileFacets | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let selected = $state<Set<string>>(new Set());

  // Filter state
  let filterGene = $state<string>('');     // '' = no filter
  let filterKind = $state<string>('');
  let filterTool = $state<string>('');
  let filterSpeciesIds = $state<Set<string>>(new Set());
  let filterText = $state<string>('');     // substring on filename

  onMount(load);

  async function load() {
    loading = true;
    error = null;
    try {
      const [all, fac] = await Promise.all([
        listProjectFiles(project.slug, { kind: 'fasta' }),
        getProjectFileFacets(project.slug),
      ]);
      files = all;
      facets = fac;
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
    loading = false;
  }

  const speciesById = $derived.by(() => {
    const m = new Map<string, ProjectSpecies>();
    for (const s of (project.species ?? []) as ProjectSpecies[]) {
      m.set(s.id, s);
    }
    return m;
  });

  // Build the full list with metadata, then filter.
  const allRefs = $derived.by(() => {
    const list: (SelectedFile & { matches: boolean })[] = [];
    const q = filterText.toLowerCase().trim();

    for (const f of files) {
      let speciesName = '(project)';
      if (f.species_id) {
        const sp = speciesById.get(f.species_id);
        if (sp) speciesName = sp.scientific_name;
      }
      const meta = (f.source_metadata ?? {}) as Record<string, any>;
      const ref: SelectedFile & { matches: boolean } = {
        id: f.id,
        name: f.name,
        species_name: speciesName,
        species_id: f.species_id ?? '',
        size: f.size,
        gene: meta.gene,
        kind: meta.kind,
        tool: meta.tool,
        matches: true,
      };

      // Apply filters
      if (filterGene && (ref.gene ?? '').toUpperCase() !== filterGene.toUpperCase()) {
        ref.matches = false;
      }
      if (filterKind && (ref.kind ?? '').toLowerCase() !== filterKind.toLowerCase()) {
        ref.matches = false;
      }
      if (filterTool && (ref.tool ?? '').toLowerCase() !== filterTool.toLowerCase()) {
        ref.matches = false;
      }
      if (filterSpeciesIds.size > 0 && !filterSpeciesIds.has(ref.species_id)) {
        ref.matches = false;
      }
      if (q && !ref.name.toLowerCase().includes(q)) {
        ref.matches = false;
      }
      list.push(ref);
    }
    return list;
  });

  const matchedRefs = $derived(allRefs.filter((r) => r.matches));
  const nFiltered = $derived(matchedRefs.length);

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selected = next;
    emit();
  }

  function emit() {
    onchange(allRefs.filter((r) => selected.has(r.id)));
  }

  function selectAllVisible() {
    const next = new Set(selected);
    for (const r of matchedRefs) next.add(r.id);
    selected = next;
    emit();
  }

  function clearAll() {
    selected = new Set();
    emit();
  }

  function selectOnlyVisible() {
    selected = new Set(matchedRefs.map((r) => r.id));
    emit();
  }

  function clearFilters() {
    filterGene = '';
    filterKind = '';
    filterTool = '';
    filterSpeciesIds = new Set();
    filterText = '';
  }

  function toggleSpeciesFilter(id: string) {
    const next = new Set(filterSpeciesIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    filterSpeciesIds = next;
  }

  const hasAnyFilter = $derived(
    !!filterGene || !!filterKind || !!filterTool ||
    filterSpeciesIds.size > 0 || !!filterText
  );

  function bytesPretty(n: number): string {
    if (n > 1e9) return (n / 1e9).toFixed(2) + ' GB';
    if (n > 1_000_000) return (n / 1_000_000).toFixed(1) + ' MB';
    if (n > 1_000) return (n / 1_000).toFixed(0) + ' KB';
    return n + ' B';
  }

  // Files over this size can't be loaded by alignment tools (RAM-bound).
  const MAFFT_LIMIT_BYTES = 100 * 1024 * 1024; // 100 MB
  // Files over this are still readable but slow / awkward for alignment.
  const WARN_THRESHOLD_BYTES = 50 * 1024 * 1024; // 50 MB

  function sizeBadge(n: number): { label: string; level: 'ok' | 'warn' | 'block' } | null {
    if (n >= MAFFT_LIMIT_BYTES) return { label: 'too large to align', level: 'block' };
    if (n >= WARN_THRESHOLD_BYTES) return { label: 'large', level: 'warn' };
    return null;
  }
</script>

<div class="picker">
  <div class="picker-head">
    <span class="mono dim">
      FASTA files: {selected.size} selected / {nFiltered} matched
      {#if hasAnyFilter}/ {allRefs.length} total{/if}
    </span>
    <div class="action-buttons">
      <button class="mini" onclick={selectAllVisible} disabled={loading || nFiltered === 0}>
        Add matched
      </button>
      <button class="mini" onclick={selectOnlyVisible} disabled={loading || nFiltered === 0}>
        Only matched
      </button>
      <button class="mini" onclick={clearAll} disabled={loading || selected.size === 0}>
        Clear
      </button>
    </div>
  </div>

  {#if !loading && facets && (facets.genes.length + facets.kinds.length + facets.tools.length > 0)}
    <div class="filter-bar mono small">
      <select bind:value={filterGene}>
        <option value="">all genes</option>
        {#each facets.genes as g}<option value={g}>{g}</option>{/each}
      </select>
      <select bind:value={filterKind}>
        <option value="">all kinds</option>
        {#each facets.kinds as k}<option value={k}>{k}</option>{/each}
      </select>
      <select bind:value={filterTool}>
        <option value="">all tools</option>
        {#each facets.tools as t}<option value={t}>{t}</option>{/each}
      </select>
      <input
        type="text"
        placeholder="filename contains…"
        bind:value={filterText}
        class="filter-text"
      />
      {#if hasAnyFilter}
        <button class="mini" onclick={clearFilters}>× clear filters</button>
      {/if}
    </div>
    {#if facets.species.length > 0}
      <div class="species-chips mono small">
        <span class="dim">species:</span>
        {#each facets.species as sp}
          <button
            class="chip"
            class:active={filterSpeciesIds.has(sp.id)}
            onclick={() => toggleSpeciesFilter(sp.id)}
            title={sp.name}
          >{sp.code}</button>
        {/each}
      </div>
    {/if}
  {/if}

  {#if loading}
    <p class="dim mono small">loading…</p>
  {:else if error}
    <p class="error mono small">{error}</p>
  {:else if allRefs.length === 0}
    <p class="dim mono small">
      No FASTA files in this project yet. Upload one on a species card (Overview tab),
      or run an annotation tool to produce one.
    </p>
  {:else if matchedRefs.length === 0}
    <p class="dim mono small">No files match the current filters.</p>
  {:else}
    <ul class="ref-list">
      {#each matchedRefs as ref (ref.id)}
        {@const badge = sizeBadge(ref.size)}
        <li>
          <label class="ref-row" class:disabled={badge?.level === 'block'}>
            <input
              type="checkbox"
              checked={selected.has(ref.id)}
              disabled={badge?.level === 'block'}
              onchange={() => toggle(ref.id)}
            />
            <div class="info">
              <div class="mono name-line">
                {ref.name}
                {#if ref.gene}<span class="tag tag-gene mono">{ref.gene}</span>{/if}
                {#if ref.kind}<span class="tag tag-kind mono">{ref.kind}</span>{/if}
                {#if ref.tool}<span class="tag tag-tool mono">{ref.tool}</span>{/if}
                {#if badge}<span class="badge badge-{badge.level} mono">{badge.label}</span>{/if}
              </div>
              <div class="mono dim small">{ref.species_name} · {bytesPretty(ref.size)}</div>
            </div>
          </label>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .picker {
    padding: 0.7rem;
    background: var(--bg-base);
    border: 1px dashed var(--border);
  }
  .picker-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .action-buttons { display: flex; gap: 0.2rem; flex-wrap: wrap; }
  .filter-bar {
    display: flex;
    gap: 0.3rem;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 0.4rem;
    padding: 0.4rem 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
  }
  .filter-bar select, .filter-bar input {
    padding: 0.2rem 0.4rem;
    background: var(--bg-base);
    border: 1px solid var(--border);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.7rem;
  }
  .filter-text { flex: 1; min-width: 8rem; }
  .species-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    align-items: center;
    margin-bottom: 0.5rem;
  }
  .chip {
    padding: 0.1rem 0.45rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.66rem;
    cursor: pointer;
  }
  .chip:hover { border-color: var(--accent); color: var(--accent); }
  .chip.active {
    background: var(--accent-dim);
    color: var(--accent);
    border-color: var(--accent);
  }
  .ref-list {
    list-style: none;
    padding: 0;
    margin: 0;
    max-height: 320px;
    overflow-y: auto;
  }
  .ref-row {
    display: flex;
    gap: 0.5rem;
    padding: 0.35rem 0.4rem;
    align-items: flex-start;
    cursor: pointer;
  }
  .ref-row:hover { background: var(--bg-inset); }
  .ref-row.disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
  .info { min-width: 0; flex: 1; }
  .name-line {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    flex-wrap: wrap;
  }
  .tag {
    font-size: 0.58rem;
    padding: 0.04rem 0.35rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border: 1px solid;
  }
  .tag-gene { color: #b896e3; border-color: #b896e3; }
  .tag-kind { color: var(--fg-muted); border-color: var(--border); }
  .tag-tool { color: #6db38c; border-color: #6db38c; }
  .badge {
    font-size: 0.6rem;
    padding: 0.05rem 0.35rem;
    border: 1px solid var(--border);
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }
  .badge-warn { color: #e0b060; border-color: #e0b060; }
  .badge-block { color: #c93535; border-color: #c93535; }
  .small { font-size: 0.7rem; }
  .mini {
    padding: 0.15rem 0.5rem;
    font-size: 0.66rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
    font-family: var(--font-mono);
    cursor: pointer;
  }
  .mini:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .mini:disabled { opacity: 0.4; cursor: not-allowed; }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
  .error { color: #c93535; }
</style>
