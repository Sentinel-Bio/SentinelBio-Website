<script lang="ts">
  import { goto } from '$app/navigation';
  import ThemeToggle from '$lib/ThemeToggle.svelte';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import { listProjects, type Project } from '$lib/api';

  let { data } = $props();

  let projects = $state<project[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  $effect(() => {
    listProjects()
      .then((c) => { projects = c; loading = false; })
      .catch((e: Error) => { error = e.message; loading = false; });
  });

  const ownedByMe = $derived(
    projects.filter((c) => c.owner_id === data.user?.id)
  );
  const publicOnes = $derived(
    projects.filter((c) => c.owner_id !== data.user?.id)
  );

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString();
  }
</script>

<div class="shell">
  <SiteHeader user={data.user} crumbs={[{ label: 'projects' }]} />

  <main>
    <div class="title-row">
      <h1>projects</h1>
      <button class="primary" onclick={() => goto('/projects/new')}>New project</button>
    </div>
    <p class="tagline">
      Group species you're researching. Add notes. Share publicly or keep private.
    </p>

    {#if error}
      <p class="error mono">{error}</p>
    {/if}

    {#if loading}
      <p class="dim mono">loading…</p>
    {:else}
      <section>
        <h2 class="section-heading">Yours</h2>
        {#if ownedByMe.length === 0}
          <p class="dim">You haven't created any projects yet.</p>
        {:else}
          <div class="grid">
            {#each ownedByMe as c}
              <a class="card" href="/projects/{c.slug}">
                <div class="card-head">
                  <span class="vis mono">{c.visibility}</span>
                  <span class="dim mono">{formatDate(c.updated_at)}</span>
                </div>
                <div class="card-title">{c.name}</div>
                {#if c.description}
                  <p class="card-desc">{c.description}</p>
                {/if}
              </a>
            {/each}
          </div>
        {/if}
      </section>

      {#if publicOnes.length > 0}
        <section style="margin-top: 3rem;">
          <h2 class="section-heading">Public</h2>
          <div class="grid">
            {#each publicOnes as c}
              <a class="card" href="/projects/{c.slug}">
                <div class="card-head">
                  <span class="vis mono">public</span>
                  <span class="dim mono">{formatDate(c.updated_at)}</span>
                </div>
                <div class="card-title">{c.name}</div>
                {#if c.description}
                  <p class="card-desc">{c.description}</p>
                {/if}
              </a>
            {/each}
          </div>
        </section>
      {/if}
    {/if}
  </main>
</div>

<style>
  .title-row { display: flex; justify-content: space-between; align-items: baseline; }
  .tagline { color: var(--fg-muted); margin: 1rem 0 2.5rem; }

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

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 1rem;
  }
  .card {
    display: block;
    padding: 1.25rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    color: var(--fg);
    transition: all 0.2s var(--ease-out);
  }
  .card:hover { border-color: var(--accent); transform: translateY(-2px); color: var(--fg); }
  .card-head {
    display: flex;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }
  .vis {
    text-transform: uppercase;
    font-size: 0.65rem;
    letter-spacing: 0.15em;
    color: var(--accent);
  }
  .card-title {
    font-family: var(--font-display);
    font-size: 1.2rem;
    color: var(--fg);
    margin-bottom: 0.35rem;
  }
  .card-desc {
    color: var(--fg-muted);
    font-size: 0.88rem;
    line-height: 1.5;
    margin: 0;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .error { color: #c93535; font-size: 0.82rem; }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }
</style>
