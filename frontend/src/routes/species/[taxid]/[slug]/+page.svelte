<script lang="ts">
  import {
    listProjects, addSpeciesToProject,
    type Project
  } from '$lib/api';
  import { getDescendantsSample } from '$lib/api';
  import { renderMarkdown } from '$lib/markdown';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import { type Species } from '$lib/api';
  import LeafletTree from '$lib/LeafletTree.svelte';
  import { SubtreeSource } from '$lib/treeSource';
  import TaxonomyBreadcrumb from '$lib/TaxonomyBreadcrumb.svelte';
  import { type TreeNode } from '$lib/api';
  import { goto } from '$app/navigation';
  import { checkAdmin } from '$lib/api';
  let { data } = $props();

  
  let isAdmin = $state(false);

  $effect(() => {
    if (data.user) {
      checkAdmin().then((v) => (isAdmin = v));
    }
  });
  let viewMode = $state<'radial' | 'dendrogram'>('radial');
  function onSubtreeClick(node: TreeNode) {
    if (!node.taxid) return;
    const slug = node.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    goto(`/species/${node.taxid}/${slug}`);
  }

  interface GalleryImage {
    url: string;
    caption?: string;
    attribution?: string;
    license?: string;
    source?: string;
    source_url?: string;
  }
  const s = $derived(data.species);
  const traits = $derived(
    (s.data?.traits as Array<{label: string; value: string; unit?: string | null; source?: string}> | null) ?? []
  );
  const galleryImages = $derived((s.data.gallery_images as GalleryImage[] | null) ?? []);
  const assembly = $derived(s.data.assembly as { accession?: string; name?: string; url?: string; ftp?: string; refseq_category?: string } | null | undefined);
  const taxonomyRanks = [
    'superkingdom', 'kingdom', 'phylum', 'subphylum',
    'class', 'subclass', 'order', 'suborder',
    'family', 'subfamily', 'genus', 'species'
  ];

  const taxonomyRows = $derived(
    taxonomyRanks
      .filter((rank) => s.taxonomy[rank])
      .map((rank) => ({ rank, name: s.taxonomy[rank], current: rank === s.rank }))
  );

  let descendants = $state<Species[]>([]);
  const isSpeciesLike = $derived(
    ['species', 'subspecies', 'forma', 'varietas'].includes((s.rank ?? '').toLowerCase())
  );
  // Ranks that are "leaves" where we don't need a descendants gallery
  const leafRanks = new Set(['species', 'subspecies', 'forma', 'varietas']);
  const isLeaf = $derived(leafRanks.has((s.rank ?? '').toLowerCase()));

  $effect(() => {
    if (!isLeaf && s.ncbi_tax_id) {
      getDescendantsSample(s.ncbi_tax_id, 12).then((d) => (descendants = d));
    }
  });

  function slugify(name: string) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }
  const assemblyCount = $derived(s.data.assembly_count as number | null);
  const nucCount = $derived(s.data.nucleotide_record_count as number | null);
  const protCount = $derived(s.data.protein_record_count as number | null);
  const hasGenome = $derived(s.data.has_genome_assembly as boolean | null);
  const blurb = $derived(s.data.blurb as string | null);
  const wikiUrl = $derived(s.data.wikipedia_url as string | null);
  const useCustom = $derived(s.data.use_custom_description as boolean ?? false);
  const customDesc = $derived(s.data.custom_description as string | null);
  const customCover = $derived(s.data.custom_cover_image as string | null);
  const additionalImages = $derived((s.data.additional_images as { url: string; caption?: string }[] | null) ?? []);
  const customAssemblies = $derived(
    (s.data.custom_assemblies as { accession: string; label: string; url: string; description?: string }[] | null) ?? []
  );
  let showProjectPicker = $state(false);
  let userProjects = $state<Project[]>([]);
  let loadingProjects = $state(false);

  async function openPicker() {
    showProjectPicker = true;
    if (userProjects.length === 0) {
      loadingProjects = true;
      try {
        const all = await listProjects();
        userProjects = all.filter((c) => c.owner_id === data.user?.id);
      } catch {}
      loadingProjects = false;
    }
  }

  async function addToProject(slug: string) {
    try {
      await addSpeciesToProject(slug, { species_id: s.id });
      alert('Added');
      showProjectPicker = false;
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }
</script>

<div class="shell">
  <SiteHeader
    user={data.user}
    crumbs={[
      { label: 'species', href: '/species' },
      { label: s.scientific_name }
    ]}
  >
  
    {#snippet actions()}
        {#if isAdmin}
        <a href="/admin/species/{s.id}">
          <button>⚙ Edit in admin</button>
        </a>
      {/if}
      {#if data.user}
        <button onclick={openPicker}>Add to project</button>
      {/if}
    {/snippet}
  </SiteHeader>
  <main>
   {#if customCover || s.image?.url}
      <div class="hero">
        <img src={customCover || s.image?.url} alt={s.scientific_name} />
        {#if !customCover && s.image?.attribution}
          <div class="hero-caption mono dim">{s.image.attribution}</div>
        {/if}
      </div>
    {/if} 
    <TaxonomyBreadcrumb
      lineage={(s.data?.lineage as Array<{rank?: string | null; name: string; taxid?: number | null}>) ?? null}
      taxonomyMap={s.taxonomy}
      currentRank={s.rank}
      currentName={s.scientific_name}
    />
       <div class="title-block">
      {#if s.common_name}
        <h1 class="common">{s.common_name}</h1>
      {/if}
      <div class="sci">{s.scientific_name}</div>
      <div class="mono dim">
        tx{s.ncbi_tax_id} · {s.rank ?? 'no rank'}
      </div>
    </div>

    <div class="body">
      <div class="main-col">
        {#if useCustom && customDesc}
          <section>
            <div class="md-rendered">{@html renderMarkdown(customDesc)}</div>
          </section>
        {:else if blurb}
          <section>
            <p class="blurb">{blurb}</p>
            {#if wikiUrl}
              <a class="wiki-link mono" href={wikiUrl} target="_blank" rel="noreferrer">
                Read on Wikipedia →
              </a>
            {/if}
          </section>
        {:else}
          <p class="dim">No description available yet.</p>
        {/if}
        {#if !isLeaf && s.ncbi_tax_id}
          <section class="subtree-section">
            <div class="subtree-head">
              <h3 class="subtree-title mono">Subtree</h3>
              <div class="view-toggle">
                <button
                  class:active={viewMode === 'radial'}
                  onclick={() => (viewMode = 'radial')}
                >Packed</button>
                <button
                  class:active={viewMode === 'dendrogram'}
                  onclick={() => (viewMode = 'dendrogram')}
                >Dendrogram</button>
              </div>
            </div>
            {#if viewMode === 'radial'}
              <LeafletTree
                source={new SubtreeSource(s.ncbi_tax_id)}
                height={500}
                onSelect={(n) => n.taxid && goto(`/species/${n.taxid}/${slugify(n.name)}`)}
              />
            {:else}
              <div class="dim mono">Dendrogram view coming soon.</div>
            {/if}
          </section>
        {/if}
        {#if !isLeaf && descendants.length > 0}
          <section class="descendants">
            <h3 class="descendants-head">Representatives in database</h3>
            <div class="descendants-grid">
              {#each descendants as d}
                <a class="dec-card" href="/species/{d.ncbi_tax_id}/{slugify(d.scientific_name)}">
                  <div class="dec-thumb">
                    {#if d.image?.url}
                      <img src={d.image.url} alt="" loading="lazy" />
                    {/if}
                  </div>
                  <div class="dec-info">
                    <div class="dec-sci">{d.scientific_name}</div>
                    {#if d.common_name}
                      <div class="dec-common">{d.common_name}</div>
                    {/if}
                    <div class="mono dim">{d.rank}</div>
                  </div>
                </a>
              {/each}
            </div>
          </section>
        {/if}
        {#if galleryImages.length > 0}
          <section class="photo-gallery">
            <h3 class="gallery-head">Gallery</h3>
            <div class="photo-grid">
              {#each galleryImages as img}
                <figure class="photo">
                  {#if img.source_url}
                    <a href={img.source_url} target="_blank" rel="noreferrer">
                      <img src={img.url} alt={img.caption ?? ''} loading="lazy" />
                    </a>
                  {:else}
                    <img src={img.url} alt={img.caption ?? ''} loading="lazy" />
                  {/if}
                  <figcaption>
                    {#if img.caption}<span class="cap">{img.caption}</span>{/if}
                    {#if img.attribution}<span class="mono dim attrib">{img.attribution}</span>{/if}
                  </figcaption>
                </figure>
              {/each}
            </div>
          </section>
        {/if}
       
     </div>

      <aside class="side-col">
        {#if taxonomyRows.length > 0}
          <div class="meta-block">
            <h4>Taxonomy</h4>
            <div class="taxa">
              {#each taxonomyRows as row}
                <span class="rank">{row.rank}</span>
                <span class="name" class:current={row.current}>{row.name}</span>
              {/each}
            </div>
          </div>
        {/if}
        <div class="meta-block">
          <h4>NCBI coverage</h4>
          <dl class="facts">
            {#if nucCount !== null && nucCount !== undefined}
              <dt>nucleotide records</dt>
              <dd>{nucCount.toLocaleString()}</dd>
            {/if}
            {#if protCount !== null && protCount !== undefined}
              <dt>protein records</dt>
              <dd>{protCount.toLocaleString()}</dd>
            {/if}
            {#if isSpeciesLike && hasGenome !== null && hasGenome !== undefined}
              <dt>genome assembly</dt>
              <dd>{hasGenome ? 'yes' : 'no'}</dd>
            {/if}
            {#if !isSpeciesLike && assemblyCount !== null && assemblyCount !== undefined}
              <dt>assemblies (group)</dt>
              <dd>{assemblyCount.toLocaleString()}</dd>
            {/if}
          </dl>
        </div>
        {#if traits.length > 0}
          <div class="meta-block">
            <h4>Traits</h4>
            <dl class="facts">
              {#each traits as t}
                <dt>{t.label.replace(/_/g, ' ')}</dt>
                <dd>
                  {t.value}{#if t.unit} <span class="dim">{t.unit}</span>{/if}
                  {#if t.source === 'wikipedia_auto'}
                    <span class="trait-src mono dim" title="auto-extracted from Wikipedia">· wiki</span>
                  {/if}
                </dd>
              {/each}
            </dl>
          </div>
        {/if}

        {#if isSpeciesLike && assembly?.accession}
          <div class="meta-block">
            <h4>Genome assembly</h4>
            <dl class="facts">
              <dt>accession</dt>
              <dd class="mono">
                <a href={assembly.url} target="_blank" rel="noreferrer">{assembly.accession}</a>
              </dd>
              {#if assembly.name}
                <dt>name</dt>
                <dd>{assembly.name}</dd>
              {/if}
              {#if assembly.refseq_category}
                <dt>category</dt>
                <dd>{assembly.refseq_category}</dd>
              {/if}
              {#if assembly.ftp}
                <dt>FTP</dt>
                <dd class="mono">
                  <a href={assembly.ftp} target="_blank" rel="noreferrer">browse files →</a>
                </dd>
              {/if}
            </dl>
          </div>
        {/if}
        {#if isSpeciesLike && customAssemblies.length > 0}
          <div class="meta-block">
            <h4>Additional assemblies</h4>
            <ul class="assembly-list">
              {#each customAssemblies as a}
                <li>
                  {#if a.url}
                    <a href={a.url} target="_blank" rel="noreferrer" class="mono">{a.accession || 'link'}</a>
                  {:else}
                    <span class="mono">{a.accession}</span>
                  {/if}
                  {#if a.label}<div class="label-line">{a.label}</div>{/if}
                  {#if a.description}<div class="desc-line dim">{a.description}</div>{/if}
                </li>
              {/each}
            </ul>
          </div>
        {/if} 
      </aside>
    </div>
  </main>

  {#if showProjectPicker}
    <div class="picker-backdrop" onclick={() => (showProjectPicker = false)}>
      <div class="picker" onclick={(e) => e.stopPropagation()}>
        <h3>Add to project</h3>
        {#if loadingProjects}
          <p class="dim mono">loading…</p>
        {:else if userProjects.length === 0}
          <p class="dim">You have no projects. <a href="/projects/new">Create one</a>.</p>
        {:else}
          <div class="picker-list">
            {#each userProjects as c}
              <button class="picker-item" onclick={() => addToProject(c.slug)}>
                <div class="picker-name">{c.name}</div>
                <div class="mono dim">{c.visibility}</div>
              </button>
            {/each}
          </div>
        {/if}
        <button onclick={() => (showProjectPicker = false)}>Close</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .hero {
    aspect-ratio: 21 / 9;
    max-height: 360px;
    overflow: hidden;
    background: var(--bg-inset);
    position: relative;
    margin-bottom: 1.5rem;
  }
  .hero img { width: 100%; height: 100%; object-fit: cover; }
  .hero-caption {
    position: absolute; bottom: 0.5rem; right: 0.75rem;
    padding: 0.3rem 0.6rem;
    background: rgba(0,0,0,0.4);
    font-size: 0.68rem;
  }

  .title-block { margin-bottom: 2rem; }
  .title-block .common {
    font-size: clamp(1.8rem, 3vw, 2.5rem);
    margin-bottom: 0.3rem;
  }
  .title-block .sci {
    font-family: var(--font-display);
    font-style: italic;
    color: var(--accent);
    font-size: 1.15rem;
    margin-bottom: 0.25rem;
  }
  .mono { font-family: var(--font-mono); font-size: 0.75rem; }
  .dim { color: var(--fg-dim); }

  .body {
    display: grid;
    grid-template-columns: 1.6fr 1fr;
    gap: 2rem;
  }
  @media (max-width: 720px) {
    .body { grid-template-columns: 1fr; }
  }

  .blurb {
    font-size: 1.05rem;
    line-height: 1.7;
    color: var(--fg);
  }
  .wiki-link {
    display: inline-block;
    margin-top: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem;
    color: var(--accent);
    border-bottom: 1px solid var(--accent-dim);
    padding-bottom: 2px;
  }

  .meta-block + .meta-block { margin-top: 1.5rem; }
  .meta-block h4 {
    font-family: var(--font-mono);
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    margin-bottom: 0.75rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid var(--border);
  }

  .taxa {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.35rem 1rem;
    font-family: var(--font-mono);
    font-size: 0.8rem;
  }
  .taxa .rank { color: var(--fg-dim); text-transform: lowercase; }
  .taxa .name {
    color: var(--fg);
    font-style: italic;
    font-family: var(--font-display);
    font-size: 0.95rem;
  }
  .taxa .name.current { color: var(--accent); font-weight: 500; }

  .facts {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.4rem 1rem;
    font-family: var(--font-mono);
    font-size: 0.8rem;
    margin: 0;
  }
  .facts dt { color: var(--fg-dim); }
  .facts dd { margin: 0; color: var(--fg); }
.picker-backdrop {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.5);
    display: grid; place-items: center;
    z-index: 20;
    padding: 1rem;
  }
  .picker {
    max-width: 440px; width: 100%;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    padding: 1.5rem;
  }
  .picker h3 { margin-bottom: 1rem; }
  .picker-list { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1rem; }
  .picker-item {
    text-align: left;
    padding: 0.75rem 1rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    color: var(--fg);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .picker-item:hover { border-color: var(--accent); }
  .picker-name { font-family: var(--font-display); color: var(--fg); }

  .gallery {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 1rem;
    margin: 2rem 0;
  }
  figure { margin: 0; }
  figure img { width: 100%; height: auto; display: block; background: var(--bg-inset); }
  figcaption { margin-top: 0.4rem; font-size: 0.72rem; line-height: 1.5; }

  .assembly-list { list-style: none; padding: 0; margin: 0; font-size: 0.85rem; }
  .assembly-list li { margin-bottom: 0.4rem; display: flex; gap: 0.5rem; align-items: baseline; }

  .md-rendered :global(img) {
    max-width: 100%;
    height: auto;
    margin: 1em 0;
  }

  .descendants { margin: 3rem 0 1rem; }
  .descendants-head {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
    margin-bottom: 1.25rem;
  }
  .descendants-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.75rem;
  }
  .dec-card {
    display: block;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    color: var(--fg);
    transition: all 0.15s;
  }
  .dec-card:hover { border-color: var(--accent); transform: translateY(-2px); }
  .dec-thumb {
    aspect-ratio: 4 / 3;
    background: var(--bg-inset);
    overflow: hidden;
  }
  .dec-thumb img { width: 100%; height: 100%; object-fit: cover; }
  .dec-info { padding: 0.6rem 0.7rem; }
  .dec-sci {
    font-family: var(--font-display);
    font-style: italic;
    font-size: 0.82rem;
    line-height: 1.2;
  }
  .dec-common { color: var(--fg-muted); font-size: 0.72rem; margin-top: 0.2rem; }

.assembly-list { list-style: none; padding: 0; margin: 0; font-size: 0.85rem; }
  .assembly-list li {
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
  }
  .assembly-list li:last-child { border-bottom: none; }
  .label-line { font-family: var(--font-display); margin-top: 0.2rem; }
  .desc-line {
    font-size: 0.78rem;
    margin-top: 0.3rem;
    line-height: 1.5;
    color: var(--fg-dim);
  }

.photo-gallery { margin: 3rem 0 1rem; }
  .gallery-head {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
    margin-bottom: 1.25rem;
  }
  .photo-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 0.75rem;
  }
  .photo {
    margin: 0;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    overflow: hidden;
  }
  .photo img {
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: cover;
    display: block;
    transition: transform 0.3s ease;
  }
  .photo:hover img { transform: scale(1.02); }
  .photo figcaption {
    padding: 0.5rem 0.7rem;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .photo .cap {
    font-size: 0.85rem;
    color: var(--fg);
    font-style: italic;
  }
  .photo .attrib {
    font-size: 0.65rem;
    line-height: 1.4;
  }

.subtree-section { margin: 2.5rem 0; }
  .subtree-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
  }
  .subtree-title {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    margin: 0;
  }
  .view-toggle { display: flex; gap: 0.25rem; }
  .view-toggle button {
    padding: 0.3rem 0.7rem;
    font-size: 0.7rem;
    border: 1px solid var(--border-strong);
    background: transparent;
    color: var(--fg-muted);
  }
  .view-toggle button.active {
    background: var(--accent-dim);
    color: var(--accent);
    border-color: var(--accent);
  }

  .trait-src {
    font-size: 0.6rem;
    margin-left: 0.3rem;
  }
</style>
