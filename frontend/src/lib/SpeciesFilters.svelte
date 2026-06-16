<script lang="ts">
  import type { SpeciesFilters } from '$lib/api';

  interface Props {
    filters: SpeciesFilters;
    onChange: (f: SpeciesFilters) => void;
  }

  let { filters, onChange }: Props = $props();

  const ranks = [
    'domain', 'superkingdom', 'kingdom', 'subkingdom',
    'phylum', 'subphylum', 'class', 'subclass',
    'superorder', 'order', 'suborder', 'infraorder',
    'superfamily', 'family', 'subfamily', 'tribe',
    'genus', 'subgenus',
    'species', 'subspecies'
  ];

  let local = $state<SpeciesFilters>({ ...filters });
  let expanded = $state(false);

  // Mirror filter changes back to the parent.
  function apply() {
    onChange({ ...local });
  }

  function clear() {
    local = {};
    onChange({});
  }

  const activeCount = $derived(
    Object.entries(local).filter(([, v]) => v && String(v).trim()).length
  );
</script>

<div class="filter-panel">
  <div class="filter-head">
    <input
      type="text"
      class="main-search"
      placeholder="Search by scientific name"
      bind:value={local.q}
      onkeydown={(e) => e.key === 'Enter' && apply()}
    />
    <button onclick={apply}>Search</button>
    <button class="ghost" onclick={() => (expanded = !expanded)}>
      {expanded ? 'Hide filters' : `Filters${activeCount > 0 ? ` (${activeCount})` : ''}`}
    </button>
    {#if activeCount > 0}
      <button class="ghost" onclick={clear}>Clear</button>
    {/if}
  </div>

  {#if expanded}
    <div class="filter-grid">
      <div class="field">
        <label>Rank</label>
        <select bind:value={local.rank} onchange={apply}>
          <option value="">any</option>
          {#each ranks as r}
            <option value={r}>{r}</option>
          {/each}
        </select>
      </div>

      <div class="field">
        <label>Kingdom</label>
        <input type="text" bind:value={local.kingdom} placeholder="e.g. Metazoa" onkeydown={(e) => e.key === 'Enter' && apply()} />
      </div>

      <div class="field">
        <label>Phylum</label>
        <input type="text" bind:value={local.phylum} placeholder="e.g. Chordata" onkeydown={(e) => e.key === 'Enter' && apply()} />
      </div>

      <div class="field">
        <label>Class</label>
        <input type="text" bind:value={local.class} placeholder="e.g. Mammalia" onkeydown={(e) => e.key === 'Enter' && apply()} />
      </div>

      <div class="field">
        <label>Order</label>
        <input type="text" bind:value={local.order} placeholder="e.g. Carnivora" onkeydown={(e) => e.key === 'Enter' && apply()} />
      </div>

      <div class="field">
        <label>Family</label>
        <input type="text" bind:value={local.family} placeholder="e.g. Otariidae" onkeydown={(e) => e.key === 'Enter' && apply()} />
      </div>

      <div class="field">
        <label>Genus</label>
        <input type="text" bind:value={local.genus} placeholder="e.g. Arctocephalus" onkeydown={(e) => e.key === 'Enter' && apply()} />
      </div>
    </div>
  {/if}
</div>

<style>
  .filter-panel { margin-bottom: 1.5rem; }
  .filter-head {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .main-search {
    flex: 1;
    min-width: 280px;
    padding: 0.7rem 0.9rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.9rem;
  }
  .main-search:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }

  .filter-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-top: 1rem;
    padding: 1rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
  }
  .field { display: flex; flex-direction: column; gap: 0.35rem; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
  }
  .field input, .field select {
    padding: 0.5rem 0.7rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.82rem;
  }

  button.ghost {
    background: transparent;
    border-color: transparent;
    color: var(--fg-muted);
  }
  button.ghost:hover {
    color: var(--accent);
    background: var(--accent-dim);
    border-color: var(--accent-dim);
  }
</style>
