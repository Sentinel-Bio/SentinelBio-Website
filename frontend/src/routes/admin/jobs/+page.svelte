<script lang="ts">
  import ThemeToggle from '$lib/ThemeToggle.svelte';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import { adminListJobs, type Job } from '$lib/api';

  let { data } = $props();
  let jobs = $state<Job[]>([]);
  let loading = $state(true);

  async function load() {
    jobs = await adminListJobs();
    loading = false;
  }
  $effect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  });

  function when(iso: string | null) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString();
  }
</script>

<div class="shell">
    <SiteHeader
    user={data.user}
    crumbs={[
      { label: 'admin', href: '/admin' },
      { label: 'jobs', href: '/admin/jobs' }
    ]}
  />
  
  <main>
    <h1>Jobs</h1>
    <p class="tagline">Auto-refreshes every 5 seconds.</p>

    {#if loading && jobs.length === 0}
      <p class="dim mono">loading…</p>
    {:else if jobs.length === 0}
      <p class="dim">No jobs yet.</p>
    {:else}
      <table>
        <thead>
          <tr><th>Kind</th><th>Status</th><th>Progress</th><th>Started</th><th>Finished</th><th></th></tr>
        </thead>
        <tbody>
          {#each jobs as j}
            <tr>
              <td class="mono">{j.kind}</td>
              <td><span class="pill pill-{j.status}">{j.status}</span></td>
              <td>
                <div class="bar">
                  <div class="fill" style="width: {j.progress}%"></div>
                </div>
                <span class="mono dim">{j.progress}%</span>
              </td>
              <td class="mono dim">{when(j.started_at)}</td>
              <td class="mono dim">{when(j.finished_at)}</td>
              <td><a href="/admin/jobs/{j.id}" class="mono">details →</a></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </main>
</div>

<style>
  .tagline { color: var(--fg-muted); margin: 1rem 0 2rem; }
  table { width: 100%; border-collapse: collapse; }
  thead th {
    text-align: left;
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
    padding: 0.75rem 0.5rem;
    border-bottom: 1px solid var(--border-strong);
  }
  tbody td {
    padding: 0.75rem 0.5rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.88rem;
    vertical-align: middle;
  }
  .pill {
    display: inline-block;
    padding: 0.2rem 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }
  .pill-queued { background: var(--bg-inset); color: var(--fg-muted); }
  .pill-running { background: var(--accent-dim); color: var(--accent); }
  .pill-done { background: #1f5c3f; color: #bfe8cf; }
  .pill-failed { background: #5c1f1f; color: #e8bfbf; }
  .bar {
    width: 120px;
    height: 6px;
    background: var(--bg-inset);
    display: inline-block;
    vertical-align: middle;
    margin-right: 0.5rem;
  }
  .fill { height: 100%; background: var(--accent); transition: width 0.3s; }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }
</style>
