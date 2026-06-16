<script lang="ts">
  import { checkAdmin } from '$lib/api';
  import { goto } from '$app/navigation';

  let { children, data } = $props();
  let authorized = $state<boolean | null>(null);

  $effect(() => {
    if (!data.user) {
      goto('/');
      return;
    }
    checkAdmin().then((ok) => {
      authorized = ok;
      if (!ok) goto('/');
    });
  });
</script>

{#if authorized === null}
  <div class="shell">
    <p class="dim mono">Checking permissions…</p>
  </div>
{:else if authorized}
  {@render children()}
{/if}

<style>
  .mono { font-family: var(--font-mono); font-size: 0.8rem; }
  .dim { color: var(--fg-dim); }
</style>
