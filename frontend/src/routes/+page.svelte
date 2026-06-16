<script lang="ts">
  import { goto } from '$app/navigation';
  import LeafletTree from '$lib/LeafletTree.svelte';
  import { supabaseBrowser } from '$lib/supabase';
  import SiteHeader from '$lib/SiteHeader.svelte';
  let { data } = $props();
  const supabase = supabaseBrowser();

  import { checkAdmin } from '$lib/api';

  let isAdmin = $state(false);
  $effect(() => {
    if (data.user) checkAdmin().then((ok) => (isAdmin = ok));
  });

  function onNodeClick(node: { taxid: number | null; name: string }) {
    if (!node.taxid) return;
    const slug = node.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    goto(`/species/${node.taxid}/${slug}`);
  }
  async function loginGoogle() {
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`
      }
    });
  }

  async function logout() {
    await supabase.auth.signOut();
    goto('/', { invalidateAll: true });
  }
</script>

<div class="shell">
   <SiteHeader user={data.user}>
    {#snippet actions()}
      {#if !data.user}
        <button class="primary" onclick={loginGoogle}>Sign in with Google</button>
      {/if}
    {/snippet}
  </SiteHeader> 
  <main>
    <h1>Sentinel Bio</h1>
    <p class="tagline">
      A platform for exploring life — species data, research projects, analysis
      tools, and games.
    </p>
  </main>

  <section class="tree-section full-bleed">
    <div class="tree-eyebrow mono">Tree of life · from our database</div>
    <LeafletTree onSelect={onNodeClick} height={760} />
  </section>

  <main>
    <div class="quick-actions">
      <a href="/species" class="action-card primary-action">
        <div class="action-eyebrow mono">Database</div>
        <div class="action-title">Search & fetch species</div>
        <div class="action-desc">By NCBI TaxID or scientific name.</div>
      </a>
      <a href="/projects" class="action-card secondary-action">
        <div class="action-eyebrow mono">Organize</div>
        <div class="action-title">My Projects</div>
        <div class="action-desc">Group species and write research notes.</div>
      </a>
    </div> 
    <section class="sections">
      <div class="sections-eyebrow mono">Environments</div>
      <div class="sections-grid">
        <a href="/marine" class="section-card">
          <h3>Aquatic</h3>
          <p>Marine and freshwater species — our deepest coverage.</p>
        </a>
        <div class="section-card disabled" aria-disabled="true">
          <h3>Air</h3>
          <p>Birds and flying insects.</p>
        </div>
        <div class="section-card disabled" aria-disabled="true">
          <h3>Earth</h3>
          <p>Terrestrial vertebrates and invertebrates.</p>
        </div>
        <div class="section-card disabled" aria-disabled="true">
          <h3>Microbial</h3>
          <p>Bacteria, archaea, fungi, protists.</p>
        </div>
      </div>
    </section>
  </main>
</div>

<style>
  .tagline {
    max-width: 40rem;
    color: var(--fg-muted);
    font-size: 1.1rem;
    margin: 1rem 0 3rem;
    line-height: 1.6;
  }
  h1 {
    font-size: clamp(2rem, 4vw, 3rem);
    line-height: 1.05;
  }
.quick-actions {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 1rem;
    margin-bottom: 3rem;
  }
  .secondary-action {
    background: var(--bg-raised);
    color: var(--fg);
    border-color: var(--border-strong);
  }
  .secondary-action:hover {
    border-color: var(--accent);
    background: var(--bg-raised);
    color: var(--fg);
    transform: translateY(-2px);
  }
  .action-card {
    display: block;
    padding: 2rem;
    background: var(--accent);
    color: var(--fg-inverse);
    border: 1px solid var(--accent);
    transition: all 0.25s var(--ease-out);
  }
  .action-card:hover {
    background: var(--accent-bright);
    border-color: var(--accent-bright);
    color: var(--fg-inverse);
    transform: translateY(-2px);
  }
  .action-eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.7rem;
    opacity: 0.8;
    margin-bottom: 0.5rem;
  }
  .action-title {
    font-family: var(--font-display);
    font-size: 1.6rem;
    margin-bottom: 0.3rem;
  }
  .action-desc {
    font-size: 0.95rem;
    opacity: 0.85;
  }

  
  .tree-eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.7rem;
    color: var(--fg-dim);
    margin-bottom: 1rem;
  }
   .sections-eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.7rem;
    color: var(--fg-dim);
    margin-bottom: 1rem;
  }
  .sections-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1rem;
  }

  .section-card {
    display: block;
    padding: 2rem 1.5rem;
    border: 1px solid var(--border);
    background: var(--bg-raised);
    color: var(--fg);
    transition: all 0.25s var(--ease-out);
  }
  .section-card:hover:not(.disabled) {
    border-color: var(--accent);
    transform: translateY(-2px);
    color: var(--fg);
  }
  .section-card.disabled {
    opacity: 0.4;
    cursor: not-allowed;
    pointer-events: none;
  }
  .section-card h3 { font-size: 1.3rem; margin-bottom: 0.5rem; }
  .section-card p { color: var(--fg-muted); font-size: 0.95rem; margin: 0; }

  .mono {
    font-family: var(--font-mono);
    font-size: 0.75rem;
  }
  .dim { color: var(--fg-dim); }
  .full-bleed {
    position: relative;
    margin-left: calc(50% - 50vw);
    margin-right: calc(50% - 50vw);
    padding-left: max(1rem, calc((100vw - 1400px) / 2));
    padding-right: max(1rem, calc((100vw - 1400px) / 2));
    margin-bottom: 3rem;
  }
  .tree-section {
    padding-top: 1.5rem;
    padding-bottom: 1rem;
  }
  .tree-eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.7rem;
    color: var(--fg-dim);
    margin-bottom: 1.25rem;
    text-align: center;
  }
  .quick-actions {
    margin-top: 2rem;
  }
</style>
