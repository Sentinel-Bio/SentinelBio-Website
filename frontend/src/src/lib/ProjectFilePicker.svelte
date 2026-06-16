<script lang="ts" module>
  export interface FilePickerSelection {
    id: string;
    name: string;
    size: number;
    mime_hint: string;
    species_id: string | null;
  }
</script>

<script lang="ts">
  /**
   * Generic multi-select picker for project files.
   *
   * Filter by mime_hint (comma-separated for multiple types) and/or category.
   * Used for structure pickers (mime=pdb,cif), tree pickers (mime=newick),
   * etc.
   */
  import { onMount } from 'svelte';
  import { listProjectFiles, type ProjectFile } from '$lib/api';

  interface Props {
    projectSlug: string;
    kind?: string;            // comma-separated mime_hints
    category?: string;
    minCount?: number;
    maxCount?: number;
    label?: string;
    onchange: (selection: FilePickerSelection[]) => void;
  }

  let {
    projectSlug,
    kind,
    category,
    minCount = 1,
    maxCount = 999,
    label = 'Select files',
    onchange,
  }: Props = $props();

  let files = $state<ProjectFile[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let selected = $state<Set<string>>(new Set());

  onMount(load);

  async function load() {
    loading = true;
    error = null;
    try {
      files = await listProjectFiles(projectSlug, { kind, category });
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
    loading = false;
  }

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) {
      next.delete(id);
    } else {
      if (next.size >= maxCount) {
        if (maxCount === 1) next.clear();
        else return;
      }
      next.add(id);
    }
    selected = next;
    emit();
  }

  function selectAll() {
    if (files.length > maxCount) {
      selected = new Set(files.slice(0, maxCount).map((f) => f.id));
    } else {
      selected = new Set(files.map((f) => f.id));
    }
    emit();
  }

  function clearAll() {
    selected = new Set();
    emit();
  }

  function emit() {
    const out: FilePickerSelection[] = files
      .filter((f) => selected.has(f.id))
      .map((f) => ({
        id: f.id, name: f.name, size: f.size,
        mime_hint: f.mime_hint, species_id: f.species_id,
      }));
    onchange(out);
  }

  function bytesPretty(n: number): string {
    if (n > 1e9) return (n / 1e9).toFixed(2) + ' GB';
    if (n > 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n > 1e3) return (n / 1e3).toFixed(0) + ' KB';
    return n + ' B';
  }
</script>

<div class="picker">
  <div class="head">
    <span class="mono dim">{label} ({selected.size}/{files.length})</span>
    <div>
      {#if maxCount > 1}
        <button class="mini" onclick={selectAll}>All</button>
      {/if}
      <button class="mini" onclick={clearAll}>None</button>
    </div>
  </div>

  {#if loading}
    <p class="dim mono small">loading…</p>
  {:else if error}
    <p class="error mono small">{error}</p>
  {:else if files.length === 0}
    <p class="dim mono small">
      No matching files. Upload one in the Files tab.
    </p>
  {:else}
    <ul class="list">
      {#each files as f (f.id)}
        <li>
          <label class="row">
            <input
              type="checkbox"
              checked={selected.has(f.id)}
              onchange={() => toggle(f.id)}
            />
            <div class="info">
              <div class="mono name">{f.name}</div>
              <div class="mono dim small">
                {f.mime_hint} · {bytesPretty(f.size)}
                {#if f.source_metadata?.category}· {f.source_metadata.category}{/if}
              </div>
            </div>
          </label>
        </li>
      {/each}
    </ul>
  {/if}

  {#if selected.size > 0 && selected.size < minCount}
    <p class="warn mono small">Need at least {minCount} file{minCount === 1 ? '' : 's'}.</p>
  {/if}
</div>

<style>
  .picker {
    padding: 0.7rem;
    background: var(--bg-base);
    border: 1px dashed var(--border);
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
  }
  .list {
    list-style: none;
    padding: 0;
    margin: 0;
    max-height: 280px;
    overflow-y: auto;
  }
  .row {
    display: flex;
    gap: 0.5rem;
    padding: 0.35rem 0.4rem;
    align-items: flex-start;
    cursor: pointer;
  }
  .row:hover { background: var(--bg-inset); }
  .info { min-width: 0; flex: 1; }
  .name {
    font-size: 0.82rem;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .small { font-size: 0.66rem; }
  .mini {
    padding: 0.15rem 0.5rem;
    font-size: 0.66rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
    margin-left: 0.2rem;
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
  .error { color: #c93535; }
  .warn { color: #e0b060; margin: 0.4rem 0 0; }
</style>
