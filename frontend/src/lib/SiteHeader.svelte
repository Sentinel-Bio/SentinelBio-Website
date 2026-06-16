<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { checkAdmin } from '$lib/api';
  import { supabaseBrowser } from '$lib/supabase';
  import ThemeToggle from '$lib/ThemeToggle.svelte';

  interface Crumb {
    label: string;
    href?: string;
  }

  interface Props {
    user: { email?: string | null } | null;
    /** Optional breadcrumb trail, rendered between the brand and the right-side tools. */
    crumbs?: Crumb[];
    /** Extra action buttons/links rendered right before the theme toggle. */
    actions?: import('svelte').Snippet;
  }

  let { user, crumbs = [], actions }: Props = $props();

  let isAdmin = $state(false);
  $effect(() => {
    if (user) checkAdmin().then((ok) => (isAdmin = ok));
  });

  async function logout() {
    await supabaseBrowser().auth.signOut();
    goto('/', { invalidateAll: true });
  }
</script>

<header class="site-header">
  <div class="left">
    <a href="/" class="brand"><em>Sentinel</em> Bio</a>
    {#each crumbs as c}
      <span class="sep">/</span>
      {#if c.href}
        <a href={c.href} class="crumb">{c.label}</a>
      {:else}
        <span class="crumb current">{c.label}</span>
      {/if}
    {/each}
  </div>

  <div class="right">
    {#if actions}{@render actions()}{/if}
    {#if isAdmin && !page.url.pathname.startsWith('/admin')}
      <a href="/admin" class="crumb">admin</a>
    {/if}
    <ThemeToggle />
    {#if user}
      <span class="user mono">{user.email}</span>
      <button class="ghost" onclick={logout}>Log out</button>
    {/if}
  </div>
</header>

<style>
  .site-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1.25rem 0 1.5rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 2rem;
    gap: 1rem;
    flex-wrap: wrap;
  }

  .left, .right {
    display: flex;
    align-items: center;
    gap: 0.9rem;
  }

  .brand {
    font-family: var(--font-display);
    font-size: 1.4rem;
    color: var(--fg);
    font-weight: 500;
  }
  .brand em { color: var(--accent); font-style: italic; }

  .sep {
    color: var(--fg-dim);
    font-family: var(--font-mono);
    font-size: 0.9rem;
  }

  .crumb {
    font-family: var(--font-mono);
    font-size: 0.85rem;
    color: var(--fg-muted);
    text-decoration: none;
    letter-spacing: 0.02em;
  }
  .crumb:hover { color: var(--accent); }
  .crumb.current { color: var(--fg); cursor: default; }

  .user {
    font-size: 0.82rem;
    color: var(--fg-dim);
  }

  button.ghost {
    padding: 0.4em 0.9em;
    font-size: 0.72rem;
  }
</style>
