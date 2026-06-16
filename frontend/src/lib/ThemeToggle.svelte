<script lang="ts">
  import { appearance } from './appearance.svelte';
  import { page } from '$app/state';
  const onBiomarine = $derived(page.url.pathname.startsWith('/marine'));
</script>

{#if !onBiomarine}
<button
  class="theme-toggle"
  onclick={() => appearance.toggle()}
  aria-label="Toggle dark mode"
  title={appearance.mode === 'dark' ? 'Switch to light' : 'Switch to dark'}
>
  {#if appearance.mode === 'dark'}
    <!-- Sun icon -->
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" stroke-linecap="round" />
    </svg>
  {:else}
    <!-- Moon icon -->
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" stroke-linejoin="round" />
    </svg>
  {/if}
</button>
{/if}

<style>
  .theme-toggle {
    display: inline-grid;
    place-items: center;
    width: 36px;
    height: 36px;
    padding: 0;
    border: 1px solid var(--border-strong);
    background: transparent;
    color: var(--fg-muted);
    cursor: pointer;
    border-radius: 999px;
    transition: all 0.2s var(--ease-out);
  }
  .theme-toggle:hover {
    color: var(--accent);
    border-color: var(--accent);
    background: var(--accent-dim);
  }
</style>
