<script lang="ts">
  import { goto } from '$app/navigation';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import { adminFetchTaxon, resolveName } from '$lib/api';

  let { data } = $props();

  let rootInput = $state('');
  const AVAILABLE_RANKS = [
    'domain', 'superkingdom', 'kingdom', 'subkingdom',
    'phylum', 'subphylum', 'class', 'subclass', 'superorder',
    'order', 'suborder', 'infraorder',
    'superfamily', 'family', 'subfamily',
    'tribe', 'subtribe',
    'genus', 'subgenus',
    'species', 'subspecies'
  ] as const;

  let stopRank = $state<string>('species');
  let maxSpecies = $state(200);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let resolvedTaxid = $state<number | null>(null);

  async function submit() {
    busy = true;
    error = null;
    try {
      let taxid: number;
      const asNum = Number(rootInput.trim());
      if (Number.isInteger(asNum) && asNum > 0) {
        taxid = asNum;
      } else {
        const r = await resolveName(rootInput.trim());
        taxid = r.taxid;
      }
      resolvedTaxid = taxid;
      const job = await adminFetchTaxon({
        root_taxid: taxid,
        stop_rank: stopRank,
        max_species: maxSpecies
      });
      goto(`/admin/jobs/${job.id}`);
    } catch (e) {
      error = e instanceof Error ? e.message : 'failed';
    }
    busy = false;
  }
</script>

<div class="shell">
    <SiteHeader
    user={data.user}
    crumbs={[{ label: 'admin', href: '/admin' }, { label: 'recursive fetch' }]}
  />
  <main style="max-width: 640px;">
    <h1>Recursive fetch</h1>
    <p class="tagline">
      Walk a taxon subtree on NCBI and upsert all descendants into the global
      species pool. Bounded by max species count. Runs as a background job.
    </p>

    <div class="field">
      <label>Root taxon</label>
      <input
        bind:value={rootInput}
        placeholder="NCBI TaxID or scientific name (e.g. 9706 or Pinnipedia)"
      />
    </div>

    <div class="field">
      <label>Stop rank</label>
      <div class="opts">
        {#each AVAILABLE_RANKS as r}
          <button
            class="opt"
            class:active={stopRank === r}
            onclick={() => (stopRank = r)}
          >{r}</button>
        {/each}
      </div>
      <p class="dim mono help">
        Keep only nodes whose rank is at or above this.
      </p>
    </div>

    <div class="field">
      <label>Max species (hard cap)</label>
      <input type="number" bind:value={maxSpecies} min="1" max="5000" />
    </div>

    {#if error}
      <p class="error mono">{error}</p>
    {/if}

    <div class="actions">
      <button onclick={() => goto('/admin')}>Cancel</button>
      <button class="primary" onclick={submit} disabled={busy || !rootInput.trim()}>
        {busy ? 'queuing…' : 'Start fetch'}
      </button>
    </div>

    <p class="dim mono" style="margin-top: 2rem;">
      Reminder: this runs in a background worker. With the default Postgres
      backend, have <code>uv run python -m app.job_worker</code> running in a
      terminal. If you've set <code>JOB_BACKEND=redis</code>, run
      <code>uv run arq app.worker.WorkerSettings</code> instead. Without a worker
      the job stays queued forever.
    </p>
  </main>
</div>

<style>
  .tagline { color: var(--fg-muted); margin: 1rem 0 2rem; }
  .field { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1.5rem; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
  }
  .field input {
    padding: 0.7rem 0.9rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.9rem;
  }
  .opts { display: flex; gap: 0.3rem; flex-wrap: wrap; }
  .opt {
    font-family: var(--font-mono);
    padding: 0.4rem 0.8rem;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
    font-size: 0.78rem;
  }
  .opt.active {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent);
  }
  .help { margin-top: 0.4rem; font-size: 0.72rem; }
  .error { color: #c93535; font-size: 0.82rem; margin-bottom: 1rem; }
  .actions { display: flex; gap: 0.75rem; justify-content: flex-end; }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }
  code { background: var(--bg-inset); padding: 0.1em 0.4em; font-size: 0.85em; }
</style>
