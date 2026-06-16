<script lang="ts">
  import { goto } from '$app/navigation';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import SpeciesFilters from '$lib/SpeciesFilters.svelte';
  import LeafletTree from '$lib/LeafletTree.svelte';
  import {
    listSpecies, fetchSpeciesByTaxid, resolveName, getLifeTree,
    type Species, type SpeciesFilters as Filters, type TreeNode
  } from '$lib/api';

  let { data } = $props();

  let results = $state<Species[]>([]);
  let filters = $state<Filters>({});
  let busy = $state(false);
  let error = $state<string | null>(null);

  // Tree state
  let selectedTaxid = $state<number | null>(null);
  let selectedName = $state<string | null>(null);

  async function doSearch() {
    busy = true;
    error = null;
    try {
      const all = await listSpecies(filters);
      if (selectedName) {
        results = all.filter(
          (s) => s.scientific_name === selectedName
            || Object.values(s.taxonomy || {}).includes(selectedName!)
        );
      } else {
        results = all;
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'search failed';
    }
    busy = false;
  }

  $effect(() => { doSearch(); });

  function slugify(name: string) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  async function handleFetch() {
    const q = filters.q?.trim();
    if (!q) return;
    busy = true;
    error = null;
    try {
      let taxid: number;
      const asNum = Number(q);
      if (Number.isInteger(asNum) && asNum > 0) {
        taxid = asNum;
      } else {
        const resolved = await resolveName(q);
        taxid = resolved.taxid;
      }
      const { species, slug } = await fetchSpeciesByTaxid(taxid);
      goto(`/species/${species.ncbi_tax_id}/${slug}`);
    } catch (e) {
      error = e instanceof Error ? e.message : 'fetch failed';
    }
    busy = false;
  }

  function onTreeSelect(node: TreeNode) {
    selectedTaxid = node.taxid;
    selectedName = node.name;
    doSearch();
  }

  function clearSelection() {
    selectedTaxid = null;
    selectedName = null;
    doSearch();
  }
</script>

<div class="shell">
  <SiteHeader user={data.user} crumbs={[{ label: 'species' }]} /> 
  <main>
    <h1>Species</h1>
    <p class="tagline">
      Search the database, drill into the tree of life, or fetch a new organism from NCBI.
    </p>

    <SpeciesFilters
      {filters}
      onChange={(f) => { filters = { ...f }; doSearch(); }}
    />

    {#if data.user && filters.q}
      <button class="primary fetch-btn" onclick={handleFetch} disabled={busy}>
        {busy ? '…' : `Fetch "${filters.q}" from NCBI`}
      </button>
    {/if}

    {#if selectedName}
      <div class="selection-bar">
        <span class="mono dim">filtering by tree node:</span>
        <span class="sci">{selectedName}</span>
        <button onclick={clearSelection}>Clear</button>
      </div>
    {/if}
    <section class="tree-section">
      <div class="tree-eyebrow mono">Tree of life · click a clade to filter</div>
      <LeafletTree
        selectedTaxid={selectedTaxid}
        onSelect={onTreeSelect}
        onBackgroundClick={clearSelection}
        height={520}
      />
    </section>
    {#if error}
      <p class="error mono">{error}</p>
    {/if}

    {#if results.length === 0 && !busy}
      <p class="dim mono">No species match.</p>
    {/if}

    <div class="species-grid">
      {#each results as s}
        <a class="species-card" href="/species/{s.ncbi_tax_id}/{slugify(s.scientific_name)}">
          <div class="thumb">
            {#if s.image?.url}
              <img src={s.image.url} alt={s.scientific_name} loading="lazy" />
            {:else}
              <div class="thumb-placeholder mono dim">no image</div>
            {/if}
          </div>
          <div class="info">
            <div class="sci">{s.scientific_name}</div>
            {#if s.common_name}
              <div class="common">{s.common_name}</div>
            {/if}
            <div class="mono dim">
              {s.rank ?? ''}{#if s.ncbi_tax_id} · tx{s.ncbi_tax_id}{/if}
            </div>
          </div>
        </a>
      {/each}
    </div>
  </main>
</div>

<style>
  .selection-bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 1rem;
    background: var(--accent-dim);
    border: 1px solid var(--accent);
    margin-bottom: 1.5rem;
    font-size: 0.88rem;
  }
  .selection-bar .sci {
    font-family: var(--font-display);
    font-style: italic;
    color: var(--fg);
    flex: 1;
  }
  .selection-bar button { padding: 0.35rem 0.8rem; font-size: 0.7rem; }

  .tree-section {
    margin: 1.5rem 0 2.5rem;
  }
  .tree-eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.68rem;
    color: var(--fg-dim);
    margin-bottom: 0.75rem;
    text-align: center;
  }

  .tagline { color: var(--fg-muted); margin: 1rem 0 2rem; max-width: 40rem; }
  .error { color: #c93535; margin-bottom: 1rem; font-size: 0.82rem; }

  .species-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 1rem;
  }

  .species-card {
    display: block;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    transition: all 0.2s var(--ease-out);
    color: var(--fg);
  }
  .species-card:hover {
    border-color: var(--accent);
    transform: translateY(-2px);
  }

  .thumb {
    aspect-ratio: 16 / 10;
    background: var(--bg-inset);
    overflow: hidden;
  }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb-placeholder {
    width: 100%; height: 100%; display: grid; place-items: center;
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em;
  }

  .info { padding: 0.9rem 1rem; }
  .sci {
    font-family: var(--font-display);
    font-style: italic;
    font-size: 1.05rem;
    color: var(--fg);
  }
  .common {
    color: var(--fg-muted);
    font-size: 0.85rem;
    margin-top: 0.15rem;
  }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); margin-top: 0.4rem; }

  .fetch-btn { margin-bottom: 1.5rem; }
</style>
