<script lang="ts">
  import '$lib/styles/app.css';
  import { page } from '$app/state';
  import { appearance } from '$lib/appearance.svelte';

  const theme = $derived(
    page.url.pathname.startsWith('/marine') ? 'biomarine' : 'neutral'
  );

  let { children } = $props();
</script>

<div data-theme={theme} data-appearance={appearance.mode} class="app-root">
  {@render children()}
</div>
