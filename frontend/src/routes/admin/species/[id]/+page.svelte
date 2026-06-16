<script lang="ts">
  import { goto, invalidateAll } from '$app/navigation';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import MarkdownEditor from '$lib/MarkdownEditor.svelte';
  import {
    adminPatchSpecies, adminUploadImage, adminFetchGalleryCandidates,
    type GalleryCandidate
  } from '$lib/api';

  let { data } = $props();
  const s = $derived(data.species);

  // All editable fields. We bind local state, then flush to the backend on Save.
  let scientificName = $state(s.scientific_name);
  let commonName = $state(s.common_name ?? '');
  let rank = $state(s.rank ?? '');
  let needsReview = $state(s.needs_review);

  // data.* fields (nested in the JSONB bag)
  let wikiBlurb = $state((s.data.blurb as string | null) ?? '');
  let customDescription = $state((s.data.custom_description as string | null) ?? '');
  let useCustomDescription = $state((s.data.use_custom_description as boolean) ?? false);

  // Assembly: store the full object. Allow manual override via user-editable fields.
  const existingAssembly = (s.data.assembly as Record<string, string> | null) ?? null;
  let assemblyAccession = $state(existingAssembly?.accession ?? '');
  let assemblyName = $state(existingAssembly?.name ?? '');
  let assemblyUrl = $state(existingAssembly?.url ?? '');
  let assemblyFtp = $state(existingAssembly?.ftp ?? '');
  let assemblyCategory = $state(existingAssembly?.refseq_category ?? '');

  interface Trait {
    label: string;
    value: string;
    unit?: string | null;
    source?: string;
  }

  let traits = $state<Trait[]>(
    ((s.data.traits as Trait[] | null) ?? []).map((t) => ({ ...t }))
  );

  function addTrait() {
    traits = [...traits, { label: '', value: '', unit: '', source: 'manual' }];
  }
  function removeTrait(i: number) {
    traits = traits.filter((_, idx) => idx !== i);
  }

  // Custom assembly URLs (a list of user-added GCF/GCA accessions)
  interface CustomAssembly {
    accession: string;
    label: string;
    url: string;
    description: string;
  }

  let customAssemblies = $state<CustomAssembly[]>(
    ((s.data.custom_assemblies as CustomAssembly[] | null) ?? [])
  );
  let newAssembly = $state<CustomAssembly>({
    accession: '',
    label: '',
    url: '',
    description: ''
  });

  function addAssembly() {
    if (!newAssembly.accession.trim() && !newAssembly.url.trim()) return;
    const acc = newAssembly.accession.trim();
    customAssemblies = [
      ...customAssemblies,
      {
        accession: acc,
        label: newAssembly.label.trim() || acc,
        url: newAssembly.url.trim() ||
          (acc ? `https://www.ncbi.nlm.nih.gov/datasets/genome/${acc}/` : ''),
        description: newAssembly.description.trim()
      }
    ];
    newAssembly = { accession: '', label: '', url: '', description: '' };
  }

  function removeAssembly(i: number) {
    customAssemblies = customAssemblies.filter((_, idx) => idx !== i);
  }
  // Images
  const existingCover = (s.data.custom_cover_image as string | null) ?? null;
  let customCoverImage = $state(existingCover ?? '');
  let uploadingCover = $state(false);

  let additionalImages = $state(((s.data.additional_images as { url: string; caption?: string }[] | null) ?? []));
  let uploadingAdditional = $state(false);

  interface GalleryImage {
    url: string;
    caption?: string;
    attribution?: string;
    license?: string;
    source?: 'inaturalist' | 'wikimedia' | 'manual';
    source_url?: string;
  }

  let galleryImages = $state<GalleryImage[]>(
    ((s.data.gallery_images as GalleryImage[] | null) ?? [])
  );
  let uploadingGallery = $state(false);

  // Candidate picker from auto-fetch
  let candidates = $state<{ inaturalist: GalleryCandidate[]; wikimedia: GalleryCandidate[] } | null>(null);
  let fetchingCandidates = $state(false);
  let selectedCandidates = $state<Set<string>>(new Set());

  async function uploadGalleryImage(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    uploadingGallery = true;
    try {
      const { url } = await adminUploadImage(file);
      galleryImages = [...galleryImages, {
        url, caption: '', attribution: '', source: 'manual'
      }];
    } catch (err) {
      alert(err instanceof Error ? err.message : 'upload failed');
    }
    uploadingGallery = false;
    (e.target as HTMLInputElement).value = '';
  }

  function removeGalleryImage(i: number) {
    galleryImages = galleryImages.filter((_, idx) => idx !== i);
  }

  async function fetchCandidates() {
    fetchingCandidates = true;
    try {
      candidates = await adminFetchGalleryCandidates(s.id);
      selectedCandidates = new Set();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'fetch failed');
    }
    fetchingCandidates = false;
  }

  function toggleCandidate(url: string) {
    const next = new Set(selectedCandidates);
    if (next.has(url)) next.delete(url);
    else next.add(url);
    selectedCandidates = next;
  }

  function addSelectedCandidates() {
    if (!candidates) return;
    const all = [...candidates.inaturalist, ...candidates.wikimedia];
    const toAdd: GalleryImage[] = [];
    for (const c of all) {
      if (!selectedCandidates.has(c.url)) continue;
      // Skip if already in gallery.
      if (galleryImages.some((g) => g.url === c.url)) continue;
      toAdd.push({
        url: c.url,
        caption: '',
        attribution: c.attribution,
        license: c.license,
        source: c.source,
        source_url: c.source_url
      });
    }
    galleryImages = [...galleryImages, ...toAdd];
    candidates = null;
    selectedCandidates = new Set();
  }

  function updateCaption(i: number, value: string) {
    galleryImages = galleryImages.map((g, idx) =>
      idx === i ? { ...g, caption: value } : g
    );
  }

  // UI
  let saving = $state(false);
  let error = $state<string | null>(null);
  let showPreview = $state(true);

  async function uploadCover(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    uploadingCover = true;
    try {
      const { url } = await adminUploadImage(file);
      customCoverImage = url;
    } catch (err) {
      alert(err instanceof Error ? err.message : 'upload failed');
    }
    uploadingCover = false;
  }

  async function uploadAdditional(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    uploadingAdditional = true;
    try {
      const { url } = await adminUploadImage(file);
      additionalImages = [...additionalImages, { url, caption: '' }];
    } catch (err) {
      alert(err instanceof Error ? err.message : 'upload failed');
    }
    uploadingAdditional = false;
    (e.target as HTMLInputElement).value = '';
  }

  function removeAdditional(i: number) {
    additionalImages = additionalImages.filter((_, idx) => idx !== i);
  }
async function save() {
    saving = true;
    error = null;
    try {
      const newData = {
        ...s.data,
        blurb: wikiBlurb,
        custom_description: customDescription,
        use_custom_description: useCustomDescription,
        assembly: assemblyAccession ? {
          accession: assemblyAccession,
          name: assemblyName,
          url: assemblyUrl,
          ftp: assemblyFtp,
          refseq_category: assemblyCategory
        } : null,
        custom_assemblies: customAssemblies,
        custom_cover_image: customCoverImage || null,
        gallery_images: galleryImages,
        traits: traits.filter((t) => t.label.trim() && t.value.trim())
      };
      // Clean up legacy field if it exists.
      delete (newData as Record<string, unknown>).additional_images;

      await adminPatchSpecies(s.id, {
        scientific_name: scientificName,
        common_name: commonName || null,
        rank: rank || null,
        needs_review: needsReview,
        data: newData
      });
      await invalidateAll();
      goto('/admin/species');
    } catch (e) {
      error = e instanceof Error ? e.message : 'save failed';
    }
    saving = false;
  }
</script>

<div class="shell">
  <SiteHeader
    user={data.user}
    crumbs={[
      { label: 'admin', href: '/admin' },
      { label: 'species', href: '/admin/species' },
      { label: s.scientific_name }
    ]}
  />

  <main>
    <h1>Edit species</h1>

    <section class="block">
      <h2 class="section-head">Identity</h2>
      <div class="field">
        <label>Scientific name</label>
        <input bind:value={scientificName} />
      </div>
      <div class="field">
        <label>Common name</label>
        <input bind:value={commonName} />
      </div>
      <div class="field inline">
        <label>Rank</label>
        <input bind:value={rank} style="max-width: 200px;" />
        <label class="check">
          <input type="checkbox" bind:checked={needsReview} />
          Needs review
        </label>
      </div>
    </section>

    <section class="block">
      <h2 class="section-head">Cover image</h2>
      <div class="cover-area">
        {#if customCoverImage}
          <img src={customCoverImage} alt="cover" class="cover-preview" />
        {:else if s.image?.url}
          <img src={s.image.url} alt="cover" class="cover-preview" />
          <p class="dim mono">Using Wikipedia default image. Upload below to override.</p>
        {:else}
          <p class="dim mono">No cover image.</p>
        {/if}
      </div>
      <div class="field inline">
        <label class="upload-btn">
          <input type="file" accept="image/*" onchange={uploadCover} style="display: none;" />
          <span>{uploadingCover ? 'uploading…' : customCoverImage ? 'Replace cover' : 'Upload cover'}</span>
        </label>
        {#if customCoverImage}
          <button onclick={() => (customCoverImage = '')}>Revert to Wikipedia default</button>
        {/if}
      </div>
    </section>
    <section class="block">
      <h2 class="section-head">Description</h2>
      <div class="field inline">
        <label class="check">
          <input type="radio" name="desc" checked={!useCustomDescription} onchange={() => (useCustomDescription = false)} />
          Use Wikipedia blurb
        </label>
        <label class="check">
          <input type="radio" name="desc" checked={useCustomDescription} onchange={() => (useCustomDescription = true)} />
          Use custom article
        </label>
      </div>

      <div class="field">
        <label>Wikipedia blurb (editable fallback)</label>
        <textarea bind:value={wikiBlurb} rows="4"></textarea>
      </div>

      <div class="field">
        <label>Custom article — markdown, LaTeX, inline images</label>
        <MarkdownEditor
          value={customDescription}
          onChange={(v) => (customDescription = v)}
          rows={20}
          placeholder={`## Introduction\n\nWrite your article here. Use markdown for formatting.\n\n![species in habitat](/path-to-uploaded-image.jpg)\n\nMath works: $E = mc^2$.\n\n$$\n\\int_0^1 f(x)\\,dx\n$$`}
        />
      </div>
    </section>
    <section class="block">
      <h2 class="section-head">Photo gallery</h2>
      <p class="dim mono">
        Standalone photo gallery, separate from images in the article body.
        Shown as a grid on the public page.
      </p>

      {#if galleryImages.length > 0}
        <div class="gallery-edit-grid">
          {#each galleryImages as img, i}
            <div class="gallery-edit-item">
              <img src={img.url} alt="" />
              <div class="gallery-fields">
                <input
                  type="text"
                  placeholder="Caption (optional)"
                  value={img.caption ?? ''}
                  oninput={(e) => updateCaption(i, (e.target as HTMLInputElement).value)}
                />
                {#if img.attribution}
                  <div class="mono dim attr">{img.attribution}</div>
                {/if}
                <button onclick={() => removeGalleryImage(i)}>Remove</button>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <p class="dim">No gallery images yet.</p>
      {/if}

      <div class="gallery-actions">
        <label class="upload-btn">
          <input type="file" accept="image/*" onchange={uploadGalleryImage} style="display: none;" />
          <span>{uploadingGallery ? 'uploading…' : '+ Upload'}</span>
        </label>
        <button onclick={fetchCandidates} disabled={fetchingCandidates}>
          {fetchingCandidates ? 'searching…' : 'Auto-fetch from iNaturalist + Wikimedia'}
        </button>
      </div>

      {#if candidates}
        <div class="candidate-picker">
          <div class="candidate-header">
            <span class="mono dim">
              {candidates.inaturalist.length + candidates.wikimedia.length} candidates · select to add
            </span>
            <button onclick={() => (candidates = null)}>Close</button>
            {#if selectedCandidates.size > 0}
              <button class="primary" onclick={addSelectedCandidates}>
                Add {selectedCandidates.size} selected
              </button>
            {/if}
          </div>

          {#if candidates.inaturalist.length > 0}
            <div class="source-group">
              <h5 class="source-head">iNaturalist</h5>
              <div class="candidate-grid">
                {#each candidates.inaturalist as c}
                  <button
                    type="button"
                    class="candidate"
                    class:selected={selectedCandidates.has(c.url)}
                    onclick={() => toggleCandidate(c.url)}
                  >
                    <img src={c.url} alt="" loading="lazy" />
                    <div class="candidate-attr mono dim">{c.attribution}</div>
                  </button>
                {/each}
              </div>
            </div>
          {/if}

          {#if candidates.wikimedia.length > 0}
            <div class="source-group">
              <h5 class="source-head">Wikimedia Commons</h5>
              <div class="candidate-grid">
                {#each candidates.wikimedia as c}
                  <button
                    type="button"
                    class="candidate"
                    class:selected={selectedCandidates.has(c.url)}
                    onclick={() => toggleCandidate(c.url)}
                  >
                    <img src={c.url} alt="" loading="lazy" />
                    <div class="candidate-attr mono dim">{c.attribution}</div>
                  </button>
                {/each}
              </div>
            </div>
          {/if}

          {#if candidates.inaturalist.length === 0 && candidates.wikimedia.length === 0}
            <p class="dim">No candidates found. Try uploading manually.</p>
          {/if}
        </div>
      {/if}
    </section>
    <section class="edit-section">
    <h3>Traits</h3>
    <p class="section-help dim">
      Free-form key/value entries for species traits (weight, lifespan, habitat, etc.).
      Auto-extracted Wikipedia traits are preserved during resync unless you override them.
    </p>

    {#each traits as _t, i}
      <div class="trait-row">
        <input
          bind:value={traits[i].label}
          placeholder="label (e.g. max_weight)"
          class="trait-label"
        />
        <input
          bind:value={traits[i].value}
          placeholder="value (e.g. 180)"
          class="trait-value"
        />
        <input
          bind:value={traits[i].unit}
          placeholder="unit (e.g. kg)"
          class="trait-unit"
        />
        <span class="trait-src mono dim">{traits[i].source ?? 'manual'}</span>
        <button class="mini danger" onclick={() => removeTrait(i)}>×</button>
      </div>
    {/each}
    <button class="add-row" onclick={addTrait}>+ Add trait</button>
  </section>
     <section class="block">
      <h2 class="section-head">Primary assembly</h2>
      <p class="dim mono">Auto-fetched from NCBI. Edit to override.</p>
      <div class="field"><label>Accession (GCF/GCA)</label><input bind:value={assemblyAccession} /></div>
      <div class="field"><label>Name</label><input bind:value={assemblyName} /></div>
      <div class="field"><label>NCBI URL</label><input bind:value={assemblyUrl} /></div>
      <div class="field"><label>FTP path</label><input bind:value={assemblyFtp} /></div>
      <div class="field"><label>RefSeq category</label><input bind:value={assemblyCategory} /></div>
    </section>
    <section class="block">
      <h2 class="section-head">Additional assemblies</h2>
      <p class="dim mono">Any number of related GCF/GCA accessions — your own lab uploads, alternative references, etc.</p>

      {#each customAssemblies as a, i}
        <div class="assembly-card">
          <div class="assembly-head">
            <span class="mono">{a.accession || '—'}</span>
            <span class="assembly-label">{a.label}</span>
            <button onclick={() => removeAssembly(i)}>×</button>
          </div>
          {#if a.url}
            <div class="mono dim"><a href={a.url} target="_blank" rel="noreferrer">{a.url}</a></div>
          {/if}
          {#if a.description}
            <p class="assembly-desc">{a.description}</p>
          {/if}
        </div>
      {/each}

      <div class="assembly-form">
        <div class="field">
          <label>Accession</label>
          <input bind:value={newAssembly.accession} placeholder="GCF_000001405.40" />
        </div>
        <div class="field">
          <label>Label</label>
          <input bind:value={newAssembly.label} placeholder="Reference human genome (GRCh38)" />
        </div>
        <div class="field">
          <label>URL <span class="dim">(auto-filled from accession if empty)</span></label>
          <input bind:value={newAssembly.url} placeholder="https://..." />
        </div>
        <div class="field">
          <label>Description</label>
          <textarea bind:value={newAssembly.description} rows="2" placeholder="What's special about this one?"></textarea>
        </div>
        <button onclick={addAssembly}>Add assembly</button>
      </div>
    </section>
    {#if error}
      <p class="error mono">{error}</p>
    {/if}

    <div class="save-row">
      <button onclick={() => goto('/admin/species')}>Cancel</button>
      <button class="primary" onclick={save} disabled={saving}>
        {saving ? 'saving…' : 'Save changes'}
      </button>
    </div>
  </main>
</div>

<style>
  .block { margin-bottom: 2.5rem; }
  .section-head {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
  }

  .field { display: flex; flex-direction: column; gap: 0.4rem; margin-bottom: 1rem; }
  .field.inline { flex-direction: row; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--fg-dim);
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .check { flex-direction: row; font-size: 0.8rem; text-transform: none; letter-spacing: normal; color: var(--fg); }
  .inline-toggle {
    padding: 0.15rem 0.5rem;
    font-size: 0.68rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
  }

  .field input, .field textarea {
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    padding: 0.6rem 0.8rem;
    font-family: var(--font-mono);
    font-size: 0.85rem;
  }
  .field textarea { font-family: var(--font-mono); line-height: 1.6; resize: vertical; }
  .field input:focus, .field textarea:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }

  .editor-pair { display: grid; grid-template-columns: 1fr; gap: 1rem; }
  .editor-pair.with-preview { grid-template-columns: 1fr 1fr; }
  .preview-box {
    background: var(--bg-inset);
    border: 1px dashed var(--border);
    padding: 1rem;
    font-size: 0.95rem;
    max-height: 600px;
    overflow-y: auto;
  }

  .cover-area { margin-bottom: 1rem; }
  .cover-preview {
    max-width: 100%;
    max-height: 300px;
    object-fit: contain;
    background: var(--bg-inset);
    display: block;
  }

  .upload-btn {
    display: inline-block;
    padding: 0.5rem 1rem;
    border: 1px dashed var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 0.8rem;
  }
  .upload-btn:hover { color: var(--accent); border-color: var(--accent); }

  .extra-img {
    display: flex;
    gap: 0.75rem;
    padding: 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    margin-bottom: 0.5rem;
    align-items: flex-start;
  }
  .extra-img img { width: 100px; height: 80px; object-fit: cover; }

  .assembly-row {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    margin-bottom: 0.4rem;
    align-items: center;
    font-size: 0.85rem;
  }

  .save-row {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 2rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--border);
  }

  .error { color: #c93535; font-size: 0.82rem; }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }

.assembly-card {
    padding: 0.75rem 1rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    margin-bottom: 0.5rem;
  }
  .assembly-head {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.4rem;
  }
  .assembly-head button {
    margin-left: auto;
    padding: 0.15rem 0.5rem;
    font-size: 0.9rem;
  }
  .assembly-label {
    font-family: var(--font-display);
    font-size: 0.95rem;
    flex: 1;
  }
  .assembly-desc {
    font-size: 0.85rem;
    color: var(--fg-muted);
    margin: 0;
    line-height: 1.5;
  }
  .assembly-form {
    padding: 1rem;
    background: var(--bg-inset);
    border: 1px dashed var(--border);
    margin-top: 1rem;
  }

  .gallery-edit-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 1rem;
    margin-bottom: 1rem;
  }
  .gallery-edit-item {
    display: flex;
    flex-direction: column;
    background: var(--bg-inset);
    border: 1px solid var(--border);
  }
  .gallery-edit-item img {
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: cover;
    display: block;
  }
  .gallery-fields {
    padding: 0.6rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .gallery-fields input {
    width: 100%;
    padding: 0.4rem 0.6rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.8rem;
  }
  .attr { font-size: 0.68rem; line-height: 1.4; }

  .gallery-actions { display: flex; gap: 0.75rem; flex-wrap: wrap; }

  .candidate-picker {
    margin-top: 1.5rem;
    padding: 1rem;
    background: var(--bg-inset);
    border: 1px dashed var(--border-strong);
  }
  .candidate-header {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .candidate-header .mono { flex: 1; }
  .source-group { margin-bottom: 1.5rem; }
  .source-head {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
    margin: 0 0 0.5rem;
  }
  .candidate-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.5rem;
  }
  .candidate {
    padding: 0;
    background: var(--bg-base);
    border: 2px solid var(--border);
    cursor: pointer;
    transition: all 0.15s;
    overflow: hidden;
    text-align: left;
  }
  .candidate:hover { border-color: var(--accent); }
  .candidate.selected {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }
  .candidate img {
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: cover;
    display: block;
  }
  .candidate-attr {
    padding: 0.4rem 0.5rem;
    font-size: 0.65rem;
    line-height: 1.4;
    max-height: 3em;
    overflow: hidden;
  }
.trait-row {
    display: grid;
    grid-template-columns: 1fr 1fr 0.7fr auto auto;
    gap: 0.4rem;
    align-items: center;
    margin-bottom: 0.4rem;
  }
  .trait-row input {
    padding: 0.4rem 0.55rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-body);
    font-size: 0.85rem;
  }
  .trait-src {
    font-size: 0.68rem;
    padding: 0 0.3rem;
  }
</style>
