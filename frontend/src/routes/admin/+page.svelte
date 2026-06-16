<script lang="ts">
  import { goto } from '$app/navigation';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import { adminBackfillLineages } from '$lib/api';

  let { data } = $props();
  let backfillBusy = $state(false);

  async function startBackfill() {
    if (!confirm('Re-fetch taxonomy for every species in the DB? Takes ~0.5s per species.')) return;
    backfillBusy = true;
    try {
      const job = await adminBackfillLineages();
      goto(`/admin/jobs/${job.id}`);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
      backfillBusy = false;
    }
  }
</script>

<div class="shell">
  <SiteHeader user={data.user} crumbs={[{ label: 'admin' }]} /> 
<main>
    <h1>Admin</h1>
    <p class="tagline">Data management. Don't break things.</p>

    <div class="grid">
      <a class="card" href="/admin/species">
        <h3>Species</h3>
        <p>Edit, resync, or remove any species in the global pool.</p>
      </a>
      <a class="card" href="/admin/fetch">
        <h3>Recursive fetch</h3>
        <p>Walk a taxon subtree and bulk-import from NCBI.</p>
      </a>
      <a class="card" href="/admin/jobs">
        <h3>Jobs</h3>
        <p>Monitor background work.</p>
      </a>
    </div>

    <section style="margin-top: 3rem;">
      <h2 class="section-heading">Maintenance</h2>
      <div class="card-row">
        <div class="maint-card">
          <h4>Backfill lineage taxids</h4>
          <p class="muted">
            One-time migration: refetch each species' taxonomy from NCBI to
            store every ancestor's TaxID. Needed after upgrading enrichment
            code so the tree renders properly.
          </p>
          <button onclick={startBackfill} disabled={backfillBusy}>
            {backfillBusy ? 'queued…' : 'Start backfill'}
          </button>
        </div>
      </div>
    </section>
  </main>

</div>

<style>
  .tagline { color: var(--fg-muted); margin: 1rem 0 2.5rem; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 1rem;
  }
  .card {
    display: block;
    padding: 1.5rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    color: var(--fg);
    transition: all 0.2s var(--ease-out);
  }
  .card:hover { border-color: var(--accent); transform: translateY(-2px); color: var(--fg); }
  .card h3 { font-size: 1.2rem; margin-bottom: 0.5rem; }
  .card p { color: var(--fg-muted); font-size: 0.9rem; margin: 0; }

.section-heading {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
  }
  .maint-card {
    padding: 1.25rem 1.5rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
  }
  .maint-card h4 {
    font-family: var(--font-display);
    font-size: 1.1rem;
    margin: 0 0 0.5rem;
    color: var(--fg);
  }
  .maint-card .muted {
    color: var(--fg-muted);
    font-size: 0.9rem;
    line-height: 1.5;
    margin: 0 0 1rem;
  }
</style>
