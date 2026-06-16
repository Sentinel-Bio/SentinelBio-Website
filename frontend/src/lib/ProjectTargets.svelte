<script lang="ts">
  import { invalidateAll } from '$app/navigation';
  import {
    createTarget, updateTarget, deleteTarget,
    type Project, type Target
  } from '$lib/api';

  interface Props {
    project: Project;
    isOwner: boolean;
  }
  let { project, isOwner }: Props = $props();

  const KINDS = [
    { value: 'gene', label: 'Gene' },
    { value: 'protein', label: 'Protein' },
    { value: 'exon', label: 'Exon' },
    { value: 'region', label: 'Region' },
    { value: 'primer', label: 'Primer' },
    { value: 'marker', label: 'Marker' },
    { value: 'transcript', label: 'Transcript' },
    { value: 'domain', label: 'Domain' },
    { value: 'other', label: 'Other' }
  ];

  let showNew = $state(false);
  let editing = $state<string | null>(null);

  interface DraftTarget {
    name: string;
    kind: Target['kind'];
    gene_symbol: string;
    accession: string;
    region: string;
    species_id: string;
    notes: string;
  }

  let draft = $state<DraftTarget>({
    name: '',
    kind: 'gene',
    gene_symbol: '',
    accession: '',
    region: '',
    species_id: '',
    notes: ''
  });

  function resetDraft() {
    draft = {
      name: '',
      kind: 'gene',
      gene_symbol: '',
      accession: '',
      region: '',
      species_id: '',
      notes: ''
    };
  }

  function startEdit(t: Target) {
    editing = t.id;
    draft = {
      name: t.name,
      kind: t.kind,
      gene_symbol: t.gene_symbol ?? '',
      accession: t.accession ?? '',
      region: t.region ?? '',
      species_id: t.species_id ?? '',
      notes: t.notes ?? ''
    };
  }

  async function saveNew() {
    if (!draft.name.trim()) return;
    try {
      await createTarget(project.slug, {
        name: draft.name,
        kind: draft.kind,
        gene_symbol: draft.gene_symbol || null,
        accession: draft.accession || null,
        region: draft.region || null,
        species_id: draft.species_id || null,
        notes: draft.notes || null,
        sort_order: (project.targets?.length ?? 0)
      });
      showNew = false;
      resetDraft();
      await invalidateAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  async function saveEdit(targetId: string) {
    try {
      await updateTarget(project.slug, targetId, {
        name: draft.name,
        kind: draft.kind,
        gene_symbol: draft.gene_symbol || null,
        accession: draft.accession || null,
        region: draft.region || null,
        species_id: draft.species_id || null,
        notes: draft.notes || null
      });
      editing = null;
      resetDraft();
      await invalidateAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  async function remove(t: Target) {
    if (!confirm(`Delete target "${t.name}"?`)) return;
    try {
      await deleteTarget(project.slug, t.id);
      await invalidateAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  function speciesOptions() {
    return project.species ?? [];
  }
</script>

<div class="targets-view">
  <div class="section-head">
    <h3 class="section-title">Biological targets ({project.targets?.length ?? 0})</h3>
    {#if isOwner && !showNew}
      <button onclick={() => { showNew = true; resetDraft(); }}>+ Add target</button>
    {/if}
  </div>

  {#if showNew}
    <div class="editor-box">
      <h4>New target</h4>
      <div class="form-grid">
        <div class="field">
          <label>Name</label>
          <input bind:value={draft.name} placeholder="e.g. TP53 exon 7" />
        </div>
        <div class="field">
          <label>Kind</label>
          <select bind:value={draft.kind}>
            {#each KINDS as k}<option value={k.value}>{k.label}</option>{/each}
          </select>
        </div>
        <div class="field">
          <label>Gene symbol</label>
          <input bind:value={draft.gene_symbol} placeholder="TP53" />
        </div>
        <div class="field">
          <label>Accession</label>
          <input bind:value={draft.accession} placeholder="NM_000546" />
        </div>
        <div class="field">
          <label>Region</label>
          <input bind:value={draft.region} placeholder="exon 7 or chr17:7676382-7675994" />
        </div>
        <div class="field">
          <label>Species (optional)</label>
          <select bind:value={draft.species_id}>
            <option value="">any / unspecified</option>
            {#each speciesOptions() as sp}
              <option value={sp.id}>{sp.scientific_name}</option>
            {/each}
          </select>
        </div>
        <div class="field wide">
          <label>Notes</label>
          <textarea bind:value={draft.notes} rows="3" placeholder="Context, why it matters, references..."></textarea>
        </div>
      </div>
      <div class="actions">
        <button onclick={() => { showNew = false; resetDraft(); }}>Cancel</button>
        <button class="primary" onclick={saveNew} disabled={!draft.name.trim()}>Save</button>
      </div>
    </div>
  {/if}

  <div class="target-grid">
    {#each project.targets ?? [] as t}
      {#if editing === t.id}
        <div class="editor-box inline">
          <div class="form-grid">
            <div class="field">
              <label>Name</label>
              <input bind:value={draft.name} />
            </div>
            <div class="field">
              <label>Kind</label>
              <select bind:value={draft.kind}>
                {#each KINDS as k}<option value={k.value}>{k.label}</option>{/each}
              </select>
            </div>
            <div class="field">
              <label>Gene symbol</label>
              <input bind:value={draft.gene_symbol} />
            </div>
            <div class="field">
              <label>Accession</label>
              <input bind:value={draft.accession} />
            </div>
            <div class="field">
              <label>Region</label>
              <input bind:value={draft.region} />
            </div>
            <div class="field">
              <label>Species</label>
              <select bind:value={draft.species_id}>
                <option value="">any / unspecified</option>
                {#each speciesOptions() as sp}
                  <option value={sp.id}>{sp.scientific_name}</option>
                {/each}
              </select>
            </div>
            <div class="field wide">
              <label>Notes</label>
              <textarea bind:value={draft.notes} rows="3"></textarea>
            </div>
          </div>
          <div class="actions">
            <button onclick={() => { editing = null; resetDraft(); }}>Cancel</button>
            <button class="primary" onclick={() => saveEdit(t.id)}>Save</button>
          </div>
        </div>
      {:else}
        <div class="target-card">
          <div class="target-head">
            <span class="kind-pill mono">{t.kind}</span>
            <h4>{t.name}</h4>
          </div>
          <dl class="target-fields">
            {#if t.gene_symbol}
              <dt>symbol</dt><dd class="mono">{t.gene_symbol}</dd>
            {/if}
            {#if t.accession}
              <dt>accession</dt>
              <dd class="mono">
                <a 
                  href="https://www.ncbi.nlm.nih.gov/nuccore/{t.accession}"
                  target="_blank"
                  rel="noreferrer"
                >{t.accession}</a>
              </dd>
            {/if}
            {#if t.region}
              <dt>region</dt><dd>{t.region}</dd>
            {/if}
            {#if t.species_id}
              {@const sp = speciesOptions().find((x) => x.id === t.species_id)}
              {#if sp}
                <dt>species</dt><dd class="sci">{sp.scientific_name}</dd>
              {/if}
            {/if}
          </dl>
          {#if t.notes}
            <p class="target-notes">{t.notes}</p>
          {/if}
          {#if isOwner}
            <div class="actions-inline">
              <button onclick={() => startEdit(t)}>Edit</button>
              <button class="danger" onclick={() => remove(t)}>Delete</button>
            </div>
          {/if}
        </div>
      {/if}
    {:else}
      <p class="dim empty">No targets yet. Add the genes, proteins, or regions you're studying.</p>
    {/each}
  </div>
</div>

<style>
  .section-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 1.25rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--border);
  }
  .section-title {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    margin: 0;
  }

  .editor-box {
    padding: 1.25rem;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    margin-bottom: 1rem;
  }
  .editor-box.inline { margin-bottom: 0; }
  .editor-box h4 {
    font-family: var(--font-display);
    margin: 0 0 1rem;
  }
  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.9rem;
    margin-bottom: 1rem;
  }
  .field { display: flex; flex-direction: column; gap: 0.35rem; }
  .field.wide { grid-column: 1 / -1; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--fg-dim);
  }
  .field input, .field select, .field textarea {
    padding: 0.5rem 0.7rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-body);
    font-size: 0.92rem;
  }
  .field input:focus, .field select:focus, .field textarea:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }

  .actions { display: flex; gap: 0.5rem; justify-content: flex-end; }

  .target-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1rem;
  }
  .target-card {
    padding: 1.1rem 1.25rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
  }
  .target-head {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
  }
  .kind-pill {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    padding: 0.2rem 0.55rem;
    background: var(--accent-dim);
    color: var(--accent);
    border: 1px solid var(--accent-dim);
  }
  .target-head h4 {
    margin: 0;
    font-family: var(--font-display);
    font-size: 1.1rem;
  }
  .target-fields {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.3rem 0.75rem;
    margin: 0 0 0.6rem;
    font-size: 0.85rem;
  }
  .target-fields dt {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--fg-dim);
  }
  .target-fields dd { margin: 0; }
  .target-fields .sci { font-family: var(--font-display); font-style: italic; }

  .target-notes {
    color: var(--fg-muted);
    font-size: 0.88rem;
    line-height: 1.5;
    margin: 0.5rem 0 0;
    padding-top: 0.6rem;
    border-top: 1px dashed var(--border);
  }

  .actions-inline {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.75rem;
    padding-top: 0.6rem;
    border-top: 1px dashed var(--border);
  }
  .actions-inline button { padding: 0.3rem 0.7rem; font-size: 0.72rem; }
  button.danger:hover { border-color: #c93535; color: #c93535; background: transparent; }

  .empty { grid-column: 1 / -1; padding: 2rem; text-align: center; }
  .mono { font-family: var(--font-mono); font-size: 0.75rem; }
  .dim { color: var(--fg-dim); }
</style>
