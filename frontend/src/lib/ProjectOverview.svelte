<script lang="ts">
  import { invalidateAll } from '$app/navigation';
  import {
    updateProject, updateSpeciesRole, removeSpeciesFromProject,
    addSpeciesToProject, listSpecies,
    type Project, type Species
  } from '$lib/api';
  import SpeciesFastaPanel from '$lib/SpeciesFastaPanel.svelte';
  import SpeciesStructurePanel from '$lib/SpeciesStructurePanel.svelte';
  import MembersPanel from '$lib/MembersPanel.svelte';
  import type { ProjectSpecies } from '$lib/api';
  import RoleSelector from '$lib/RoleSelector.svelte';

  interface Props {
    project: Project;
    isOwner: boolean;
    currentUserId?: string | null;
  }
  let { project, isOwner, currentUserId = null }: Props = $props();

  // Editable state snapshots.
  let editingScaffold = $state(false);
  let researchQuestion = $state(project.research_question ?? '');
  let hypotheses = $state<string[]>([...(project.hypotheses ?? [])]);
  let objectives = $state<string[]>([...(project.objectives ?? [])]);
  let status = $state(project.status);

  let saving = $state(false);
  let error = $state<string | null>(null);

  // Species add panel.
  let showAddPanel = $state(false);
  let searchQuery = $state('');
  let searchResults = $state<Species[]>([]);
  let searching = $state(false);

  // Common role defaults (also free text allowed).
  const ROLE_OPTIONS = ['primary', 'comparison', 'outgroup', 'reference', 'control', 'other'];

  function addHypothesis() { hypotheses = [...hypotheses, '']; }
  function removeHypothesis(i: number) { hypotheses = hypotheses.filter((_, idx) => idx !== i); }
  function addObjective() { objectives = [...objectives, '']; }
  function removeObjective(i: number) { objectives = objectives.filter((_, idx) => idx !== i); }

  async function saveScaffold() {
    saving = true;
    error = null;
    try {
      await updateProject(project.slug, {
        research_question: researchQuestion || undefined,
        hypotheses: hypotheses.filter((h) => h.trim()),
        objectives: objectives.filter((o) => o.trim()),
        status
      });
      editingScaffold = false;
      await invalidateAll();
    } catch (e) {
      error = e instanceof Error ? e.message : 'save failed';
    }
    saving = false;
  }

  async function searchSpecies() {
    searching = true;
    try {
      searchResults = await listSpecies({ q: searchQuery || undefined });
    } catch {
      // swallow
    }
    searching = false;
  }

  async function addSpecies(s: Species, role: string) {
    try {
      await addSpeciesToProject(project.slug, { species_id: s.id, role });
      await invalidateAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  async function changeRole(speciesId: string, newRole: string) {
    try {
      await updateSpeciesRole(project.slug, speciesId, { role: newRole });
      await invalidateAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  async function removeSp(s: Species) {
    if (!confirm(`Remove ${s.scientific_name} from this project?`)) return;
    try {
      await removeSpeciesFromProject(project.slug, s.id);
      await invalidateAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  function slugify(name: string) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }
</script>
<div class="overview-grid">
  <section class="scaffold-col">
    <div class="section-head">
      <h3 class="section-title">Research scaffold</h3>
      {#if isOwner && !editingScaffold}
        <button onclick={() => (editingScaffold = true)}>Edit</button>
      {/if}
    </div>

    {#if editingScaffold}
      <div class="edit-block">
        <div class="field">
          <label>Research question</label>
          <textarea bind:value={researchQuestion} rows="2" placeholder="What are you trying to answer?"></textarea>
        </div>

        <div class="field">
          <label>Hypotheses</label>
          {#each hypotheses as _h, i}
            <div class="list-row">
              <span class="mono dim">H{i + 1}</span>
              <input bind:value={hypotheses[i]} placeholder="e.g. There are polymorphisms in TP53..." />
              <button onclick={() => removeHypothesis(i)}>×</button>
            </div>
          {/each}
          <button class="add-row" onclick={addHypothesis}>+ Add hypothesis</button>
        </div>

        <div class="field">
          <label>Objectives</label>
          {#each objectives as _o, i}
            <div class="list-row">
              <span class="mono dim">{i + 1}</span>
              <input bind:value={objectives[i]} placeholder="e.g. Identify polymorphisms in A. australis..." />
              <button onclick={() => removeObjective(i)}>×</button>
            </div>
          {/each}
          <button class="add-row" onclick={addObjective}>+ Add objective</button>
        </div>

        <div class="field">
          <label>Status</label>
          <div class="status-opts">
            {#each ['planning', 'active', 'paused', 'done', 'archived'] as s}
              <button
                class="opt"
                class:active={status === s}
                onclick={() => (status = s as Project['status'])}
              >{s}</button>
            {/each}
          </div>
        </div>

        {#if error}<p class="error mono">{error}</p>{/if}

        <div class="actions">
          <button onclick={() => {
            editingScaffold = false;
            researchQuestion = project.research_question ?? '';
            hypotheses = [...(project.hypotheses ?? [])];
            objectives = [...(project.objectives ?? [])];
            status = project.status;
          }}>Cancel</button>
          <button class="primary" onclick={saveScaffold} disabled={saving}>
            {saving ? 'saving…' : 'Save'}
          </button>
        </div>
      </div>
    {:else}
      <div class="view-block">
        <div class="subsection">
          <span class="label mono">Research question</span>
          <p>{project.research_question || 'Not yet defined.'}</p>
        </div>

        <div class="subsection">
          <span class="label mono">Hypotheses</span>
          {#if project.hypotheses?.length}
            <ol class="hyp-list">
              {#each project.hypotheses as h, i}
                <li><span class="mono dim">H{i + 1}</span> {h}</li>
              {/each}
            </ol>
          {:else}
            <p class="dim">No hypotheses yet.</p>
          {/if}
        </div>

        <div class="subsection">
          <span class="label mono">Objectives</span>
          {#if project.objectives?.length}
            <ol class="obj-list">
              {#each project.objectives as o, i}
                <li><span class="mono dim">{i + 1}.</span> {o}</li>
              {/each}
            </ol>
          {:else}
            <p class="dim">No objectives yet.</p>
          {/if}
        </div>
      </div>
    {/if}
  </section>

  <section class="species-col">
    <div class="section-head">
      <h3 class="section-title">Species & roles ({project.species?.length ?? 0})</h3>
      {#if isOwner}
        <button onclick={() => {
          showAddPanel = !showAddPanel;
          if (showAddPanel && searchResults.length === 0) searchSpecies();
        }}>
          {showAddPanel ? 'Close' : 'Add'}
        </button>
      {/if}
    </div>

    {#if showAddPanel}
      <div class="add-panel">
        <div style="display: flex; gap: 0.5rem;">
          <input
            bind:value={searchQuery}
            placeholder="Search by scientific name"
            onkeydown={(e) => e.key === 'Enter' && searchSpecies()}
          />
          <button onclick={searchSpecies} disabled={searching}>Search</button>
        </div>
        <div class="results">
          {#each searchResults as s}
            <div class="result-row">
              <div>
                <div class="sci">{s.scientific_name}</div>
                <div class="mono dim">{s.common_name ?? ''} · tx{s.ncbi_tax_id}</div>
              </div>
              <div class="role-select">
                <select onchange={(e) => addSpecies(s, (e.target as HTMLSelectElement).value)}>
                  <option value="">add as…</option>
                  {#each ROLE_OPTIONS as r}
                    <option value={r}>{r}</option>
                  {/each}
                </select>
              </div>
            </div>
          {:else}
            {#if !searching}<p class="dim mono">No results. Fetch species from <a href="/species">the species page</a>.</p>{/if}
          {/each}
        </div>
      </div>
    {/if}

    <div class="species-list">
      {#each project.species ?? [] as s}
        <div class="species-card">
          <a class="species-link" href="/species/{s.ncbi_tax_id}/{slugify(s.scientific_name)}">
            <div class="thumb">
              {#if s.image?.url}<img src={s.image.url} alt="" loading="lazy" />{/if}
            </div>
            <div class="species-info">
              <div class="sci">{s.scientific_name}</div>
              {#if s.common_name}<div class="common">{s.common_name}</div>{/if}
              <div class="mono dim">{s.rank ?? ''}</div>
            </div>
          </a>
          <div class="role-line">
        <span class="role-label mono">role</span>
        {#if isOwner}
          <RoleSelector
            value={s._role ?? 'primary'}
            options={ROLE_OPTIONS}
            onchange={(v) => changeRole(s.id, v)}
          />
        {:else}
          <span class="role-static">{s._role ?? 'primary'}</span>
        {/if}
        {#if isOwner}
          <button class="mini" onclick={() => removeSp(s)} title="Remove">×</button>
        {/if}
      </div>
          <SpeciesFastaPanel
            projectSlug={project.slug}
            species={s as ProjectSpecies}
            {isOwner}
          />
          <SpeciesStructurePanel
            projectSlug={project.slug}
            species={s as ProjectSpecies}
            {isOwner}
          />
        </div>
      {:else}
        <p class="dim">No species yet. Add the organisms you're studying.</p>
      {/each}
    </div>
  </section>
</div>

<MembersPanel projectSlug={project.slug} {isOwner} {currentUserId} />

<style>
  .overview-grid {
    display: grid;
    grid-template-columns: 1.2fr 1fr;
    gap: 2rem;
  }
  @media (max-width: 900px) {
    .overview-grid { grid-template-columns: 1fr; }
  }

  .section-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 1rem;
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

  .subsection { margin-bottom: 1.5rem; }
  .label {
    display: block;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
    margin-bottom: 0.4rem;
  }
  .hyp-list, .obj-list { padding-left: 0; list-style: none; margin: 0; }
  .hyp-list li, .obj-list li {
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--border);
    display: flex;
    gap: 0.6rem;
    font-size: 0.92rem;
    line-height: 1.5;
  }
  .hyp-list li:last-child, .obj-list li:last-child { border-bottom: none; }

  .field { display: flex; flex-direction: column; gap: 0.4rem; margin-bottom: 1rem; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--fg-dim);
  }
  .field input, .field textarea {
    padding: 0.55rem 0.75rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-body);
    font-size: 0.92rem;
  }
  .field input:focus, .field textarea:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }
  .list-row {
    display: grid;
    grid-template-columns: 30px 1fr auto;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.4rem;
  }
  .add-row {
    padding: 0.35rem 0.8rem;
    background: transparent;
    border: 1px dashed var(--border-strong);
    color: var(--fg-muted);
    font-size: 0.8rem;
  }
  .add-row:hover { color: var(--accent); border-color: var(--accent); }

  .status-opts { display: flex; gap: 0.3rem; flex-wrap: wrap; }
  .opt {
    padding: 0.35rem 0.75rem;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.75rem;
    text-transform: lowercase;
  }
  .opt.active {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent);
  }

  .actions { display: flex; gap: 0.5rem; justify-content: flex-end; }
  .error { color: #c93535; font-size: 0.85rem; }

  .add-panel {
    background: var(--bg-inset);
    border: 1px solid var(--border);
    padding: 0.9rem;
    margin-bottom: 1rem;
  }
  .add-panel input {
    flex: 1;
    padding: 0.5rem 0.75rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.85rem;
  }
  .results { margin-top: 0.75rem; max-height: 280px; overflow-y: auto; }
  .result-row {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.5rem 0;
    align-items: center;
  }
  .result-row .sci { font-family: var(--font-display); font-style: italic; }
  .role-select select {
    padding: 0.35rem 0.6rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.78rem;
  }

  .species-list { display: flex; flex-direction: column; gap: 0.6rem; }
  .species-card {
    background: var(--bg-raised);
    border: 1px solid var(--border);
    padding: 0.5rem;
  }
  .species-link {
    display: grid;
    grid-template-columns: 50px 1fr;
    gap: 0.75rem;
    align-items: center;
    color: var(--fg);
    text-decoration: none;
  }
  .thumb {
    width: 50px; height: 50px;
    background: var(--bg-inset);
    overflow: hidden;
  }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .species-info { min-width: 0; }
  .sci { font-family: var(--font-display); font-style: italic; color: var(--fg); font-size: 0.95rem; }
  .common { font-size: 0.82rem; color: var(--fg-muted); }

  .role-line {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.5rem;
    padding-top: 0.5rem;
    border-top: 1px dashed var(--border);
  }
  .role-label {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--fg-dim);
  }
  .role-input {
    flex: 1;
    padding: 0.25rem 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 0.8rem;
  }
  .role-input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .role-static {
    flex: 1;
    font-family: var(--font-mono);
    font-size: 0.8rem;
    color: var(--accent);
  }
  .mini {
    padding: 0.15rem 0.5rem;
    font-size: 0.95rem;
    border: 1px solid var(--border);
  }

  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
  .dim { color: var(--fg-dim); }

  .folder-row {
    grid-column: 1 / -1;
    margin-bottom: 1rem;
  }
</style>
