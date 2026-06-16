<script lang="ts">
  import { page } from '$app/state';
  import ThemeToggle from '$lib/ThemeToggle.svelte';
  import { adminGetJob, type Job } from '$lib/api';
  import { adminCancelJob } from '$lib/api';

  let { data } = $props();
  const id = $derived(page.params.id);

  let job = $state<Job | null>(null);
  let error = $state<string | null>(null);

  async function load() {
    try {
      job = await adminGetJob(id);
    } catch (e) {
      error = e instanceof Error ? e.message : 'failed';
    }
  }
  async function cancel() {
    if (!job) return;
    if (!confirm('Cancel this job? Work already completed won\'t be undone.')) return;
    try {
      await adminCancelJob(job.id);
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'cancel failed');
    }
  }

  $effect(() => {
    load();
    const int = setInterval(() => {
      if (job && (job.status === 'done' || job.status === 'failed')) return;
      load();
    }, 3000);
    return () => clearInterval(int);
  });
</script>

<div class="shell">
  <header class="header">
    <a href="/" class="brand"><em>Sentinel</em> Bio</a>
    <div style="display: flex; gap: 0.75rem; align-items: center;">
      <a href="/admin/jobs" class="mono dim" style="text-decoration: underline dotted;">all jobs</a>
      <ThemeToggle />
      {#if data.user}<span class="mono dim">{data.user.email}</span>{/if}
    </div>
  </header>

  <main style="max-width: 780px;">
    {#if error}
      <p class="error mono">{error}</p>
    {:else if !job}
      <p class="dim mono">loading…</p>
    {:else}
      <h1>{job.kind}</h1>
      <div class="status-row">
        <span class="pill pill-{job.status}">{job.status}</span>
        {#if job.status === 'running' || job.status === 'queued'}
            <button class="danger-soft" onclick={cancel}>Cancel job</button>
        {/if}
        <span class="mono dim">{job.id}</span>
      </div>

      <div class="bar">
        <div class="fill" style="width: {job.progress}%"></div>
      </div>
      <p class="mono dim">{job.progress}% complete</p>
      
      <div class="fields">
        <div><span class="label mono">params</span><pre>{JSON.stringify(job.params, null, 2)}</pre></div>
        {#if job.result}
          <div><span class="label mono">result</span><pre>{JSON.stringify(job.result, null, 2)}</pre></div>
        {/if}
        {#if job.error}
          <div><span class="label mono">error</span><pre class="err">{job.error}</pre></div>
        {/if}
      </div>
    {/if}
  </main>
</div>

<style>
  .status-row { display: flex; gap: 1rem; align-items: center; margin: 1rem 0 1.5rem; }
  .pill {
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

  .bar { width: 100%; height: 10px; background: var(--bg-inset); margin-bottom: 0.5rem; }
  .fill { height: 100%; background: var(--accent); transition: width 0.3s; }

  .fields { display: flex; flex-direction: column; gap: 1rem; margin-top: 2rem; }
  .label {
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.7rem;
    color: var(--fg-dim);
    display: block;
    margin-bottom: 0.3rem;
  }
  pre {
    background: var(--bg-inset);
    padding: 1rem;
    font-family: var(--font-mono);
    font-size: 0.82rem;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }
  pre.err { color: #e88; }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }
  .error { color: #c93535; }

button.danger-soft {
    border-color: var(--border-strong);
    color: var(--fg-muted);
  }
  button.danger-soft:hover {
    border-color: #c93535;
    color: #c93535;
  }
</style>
