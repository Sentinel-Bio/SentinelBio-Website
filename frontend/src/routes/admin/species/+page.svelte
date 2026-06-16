<script lang="ts">
  import { invalidateAll } from '$app/navigation';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import SpeciesFilters from '$lib/SpeciesFilters.svelte';
  import {
    adminListSpecies, adminPatchSpecies, adminDeleteSpecies,
    adminResyncSpecies, adminResyncAllSpecies, type Species
  } from '$lib/api';

  let { data } = $props();

  let species = $state<Species[]>([]);
  let loading = $state(true);
  let editing = $state<string | null>(null);
  let draft = $state<{ scientific_name: string; common_name: string; rank: string; needs_review: boolean }>({
    scientific_name: '', common_name: '', rank: '', needs_review: false
  });

  type Filters = {
    query: string;
    rank: string;
    needs_review: boolean | null;
    only_with_taxid: boolean;
  };

  let filters = $state<Filters>({
    query: '',
    rank: '',
    needs_review: null,
    only_with_taxid: false
  });

  async function load() {
    loading = true;
    species = await adminListSpecies(filters);
    loading = false;
  }
  $effect(() => { load(); });

  function startEdit(s: Species) {
    editing = s.id;
    draft = {
      scientific_name: s.scientific_name,
      common_name: s.common_name ?? '',
      rank: s.rank ?? '',
      needs_review: s.needs_review
    };
  }
  async function resyncAll() {
    if (!confirm(`Resync ${species.length} species from NCBI?\nThis takes ~2s per species.`)) return;
    try {
      const job = await adminResyncAllSpecies();
      window.location.href = `/admin/jobs/${job.id}`;
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }
  async function save(id: string) {
    try {
      const updated = await adminPatchSpecies(id, {
        scientific_name: draft.scientific_name,
        common_name: draft.common_name || null,
        rank: draft.rank || null,
        needs_review: draft.needs_review
      });
      species = species.map((s) => s.id === id ? updated : s);
      editing = null;
    } catch (e) {
      alert(e instanceof Error ? e.message : 'save failed');
    }
  }

  async function resync(s: Species) {
    try {
      await adminResyncSpecies(s.id);
      alert(`Resync queued for ${s.scientific_name}. Check /admin/jobs for progress.`);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  async function remove(s: Species) {
    if (!confirm(`Delete ${s.scientific_name} from the global pool?\nThis also removes it from all collections.`)) return;
    try {
      await adminDeleteSpecies(s.id);
      species = species.filter((x) => x.id !== s.id);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }
</script>

<div class="shell">
  <SiteHeader
    user={data.user}
    crumbs={[{ label: 'admin', href: '/admin' }, { label: 'species' }]}
  /> 

  <main>
    <h1>Species manager</h1>
        <SpeciesFilters
      {filters}
      onChange={(f) => { filters = f; load(); }}
    />
    <div style="display: flex; gap: 0.5rem; margin-bottom: 1rem;">
      <button class="danger-soft" onclick={resyncAll}>Resync all</button>
    </div>
    {#if loading}
      <p class="dim mono">loading…</p>
    {:else if species.length === 0}
      <p class="dim">No species match.</p>
    {:else}
      <table>
        <thead>
          <tr>
            <th>Scientific name</th>
            <th>Common</th>
            <th>Rank</th>
            <th>TaxID</th>
            <th>Flags</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each species as s}
              <tr>
                <td class="sci">{s.scientific_name}</td>
                <td>{s.common_name ?? '—'}</td>
                <td class="mono dim">{s.rank ?? '—'}</td>
                <td class="mono dim">{s.ncbi_tax_id ?? '—'}</td>
                <td>{s.needs_review ? '⚠ review' : ''}</td>
                <td class="actions-cell">
                  <a href="/admin/species/{s.id}" class="btn-link">Edit</a>
                  <button onclick={() => resync(s)}>Resync</button>
                  <button class="danger" onclick={() => remove(s)}>Delete</button>
                </td>
              </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </main>
</div>

<style>
  .toolbar { display: flex; gap: 0.5rem; margin: 1.5rem 0; }
  .toolbar input {
    flex: 1;
    padding: 0.6rem 0.9rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.85rem;
  }

  table { width: 100%; border-collapse: collapse; }
  thead th {
    text-align: left;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
    padding: 0.75rem 0.5rem;
    border-bottom: 1px solid var(--border-strong);
  }
  tbody td {
    padding: 0.75rem 0.5rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.9rem;
  }
  tr.editing { background: var(--bg-inset); }
  tr.editing input {
    width: 100%;
    padding: 0.3rem 0.5rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-body);
  }
  .sci { font-family: var(--font-display); font-style: italic; }
  .actions-cell { display: flex; gap: 0.3rem; justify-content: flex-end; }
  .actions-cell button { padding: 0.35rem 0.7rem; font-size: 0.72rem; }
  .check { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; }
  button.danger:hover { border-color: #c93535; color: #c93535; background: transparent; }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }

  button.danger-soft {
    border-color: var(--border-strong);
    color: var(--fg-muted);
  }
  button.danger-soft:hover {
    border-color: #c93535;
    color: #c93535;
  }

.btn-link {
    display: inline-block;
    padding: 0.35rem 0.7rem;
    font-size: 0.72rem;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border: 1px solid var(--border-strong);
    color: var(--fg);
    text-decoration: none;
  }
  .btn-link:hover { border-color: var(--accent); color: var(--accent); }
</style>
