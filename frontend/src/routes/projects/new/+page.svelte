<script lang="ts">
  import { goto } from '$app/navigation';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import { createProject } from '$lib/api';

  let { data } = $props();

  let name = $state('');
  let description = $state('');
  let researchQuestion = $state('');
  let visibility = $state<'private' | 'unlisted' | 'public'>('private');
  let busy = $state(false);
  let error = $state<string | null>(null);

  async function submit() {
    if (!name.trim() || busy) return;
    busy = true;
    error = null;
    try {
      const c = await createProject({
        name: name.trim(),
        description: description.trim() || undefined,
        visibility,
        research_question: researchQuestion.trim() || undefined
      });
      goto(`/projects/${c.slug}`);
    } catch (e) {
      error = e instanceof Error ? e.message : 'failed';
      busy = false;
    }
  }
</script>

<div class="shell">
  <SiteHeader
    user={data.user}
    crumbs={[
      { label: 'projects', href: '/projects' },
      { label: 'new' }
    ]}
  /> 

  <main style="max-width: 640px;">
    <h1>New project</h1>
    <p class="tagline">Name it, describe what you're investigating, pick visibility.</p>

    <div class="field">
      <label for="name">Name</label>
      <input
        id="name"
        type="text"
        bind:value={name}
        placeholder="e.g. Pinnipedia cancer comparison"
        autofocus
      />
    </div>

    <div class="field">
      <label for="desc">Description <span class="dim mono">(optional)</span></label>
      <input id="desc" type="text" bind:value={description} placeholder="Short summary" />
    </div>
    <div class="field">
      <label for="rq">Research question <span class="dim mono">(optional, can add later)</span></label>
      <textarea id="rq" bind:value={researchQuestion} rows="2" placeholder="What are you trying to answer?"></textarea>
    </div>
    <div class="field">
      <label>Visibility</label>
      <div class="vis-row">
        {#each ['private', 'unlisted', 'public'] as v}
          <button
            class="vis-option"
            class:active={visibility === v}
            onclick={() => (visibility = v as typeof visibility)}
          >
            {v}
          </button>
        {/each}
      </div>
      <p class="dim mono help">
        {#if visibility === 'private'}Only you see it.
        {:else if visibility === 'unlisted'}Anyone with the link can view.
        {:else}Listed publicly for everyone.{/if}
      </p>
    </div>

    {#if error}<p class="error mono">{error}</p>{/if}

    <div class="actions">
      <button onclick={() => goto('/projects')}>Cancel</button>
      <button class="primary" onclick={submit} disabled={busy || !name.trim()}>
        {busy ? 'creating…' : 'Create'}
      </button>
    </div>
  </main>
</div>

<style>
  .tagline { color: var(--fg-muted); margin: 1rem 0 2.5rem; }

  .field { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1.5rem; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
  }
  .field input {
    padding: 0.75rem 1rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-body);
    font-size: 1rem;
    border-radius: 2px;
  }
  .field input:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }

  .vis-row { display: flex; gap: 0.5rem; }
  .vis-option {
    font-family: var(--font-mono);
    padding: 0.5rem 1rem;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
    text-transform: lowercase;
  }
  .vis-option.active {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent);
  }
  .help { margin-top: 0.4rem; font-size: 0.72rem; }

  .error { color: #c93535; font-size: 0.82rem; margin-bottom: 1rem; }
  .actions { display: flex; gap: 0.75rem; justify-content: flex-end; }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }
</style>
