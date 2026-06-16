<script lang="ts">
  import SiteHeader from '$lib/SiteHeader.svelte';
  let { data } = $props();
</script>

<div class="shell">
  <SiteHeader user={data.user} crumbs={[{ label: 'aquatic' }]} />
  <main>
    <h1>Aquatic species</h1>
    <p class="tagline">
      Marine and freshwater organisms. Memory game, deck browser, and species dossiers.
    </p>
    <p class="dim mono">Content migrating from biomarine-memory in a future session.</p>
  </main>
</div>

<style>
  .tagline { max-width: 40rem; color: var(--fg-muted); margin: 1rem 0 3rem; }
  .mono { font-family: var(--font-mono); font-size: 0.85rem; }
  .dim { color: var(--fg-dim); }
</style>
