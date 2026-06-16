<script lang="ts">
  import { goto, invalidateAll } from '$app/navigation';
  import { page } from '$app/state';
  import SiteHeader from '$lib/SiteHeader.svelte';
  import ProjectOverview from '$lib/ProjectOverview.svelte';
  import ProjectTargets from '$lib/ProjectTargets.svelte';
  import ProjectWorkflow from '$lib/ProjectWorkflow.svelte';
  import ProjectNarrative from '$lib/ProjectNarrative.svelte';
  import ProjectFiles from '$lib/ProjectFiles.svelte';
  import ProjectNotesPlaceholder from '$lib/ProjectNotesPlaceholder.svelte';
  import ProjectTools from '$lib/ProjectTools.svelte';
  import ConservationPanel from '$lib/ConservationPanel.svelte';

  import {
    deleteProject,
    type Project
  } from '$lib/api';
  import { updateProject } from '$lib/api';

  let editingName = $state(false);
  let nameEditValue = $state('');

  function startNameEdit() {
    nameEditValue = project.name;
    editingName = true;
  }

  function cancelNameEdit() {
    editingName = false;
    nameEditValue = '';
  }

  async function saveName() {
    if (!editingName) return;
    const next = nameEditValue.trim();
    if (!next || next === project.name) {
      cancelNameEdit();
      return;
    }
    try {
      await updateProject(project.slug, { name: next });
      await invalidateAll();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'rename failed');
    }
    cancelNameEdit();
  }

  let { data } = $props();
  const project = $derived(data.project);
  const isOwner = $derived(project.owner_id === data.user?.id);

  // Tab state — derived from URL hash so refresh preserves the current tab.
  let activeTab = $state<'overview' | 'targets' | 'workflow' | 'files' | 'tools' | 'conservation' | 'narrative' | 'reports'>('overview');

  $effect(() => {
    const hash = page.url.hash.replace('#', '');
    if (['overview', 'targets', 'workflow', 'files', 'tools', 'narrative', 'reports'].includes(hash)) {
      activeTab = hash as typeof activeTab;
    }
  });

  function selectTab(tab: typeof activeTab) {
    activeTab = tab;
    history.replaceState(null, '', `#${tab}`);
  }

  async function destroy() {
    if (!confirm('Delete this project forever? Species and targets are removed from it but species stay in the global DB.')) return;
    try {
      await deleteProject(project.slug);
      goto('/projects');
    } catch (e) {
      alert(e instanceof Error ? e.message : 'failed');
    }
  }

  function copyShareLink() {
    const url = `${window.location.origin}/projects/${project.slug}`;
    navigator.clipboard.writeText(url);
    alert('Link copied');
  }

  const TABS: Array<{ id: typeof activeTab; label: string; description: string }> = [
    { id: 'overview', label: 'Overview', description: 'Research question, hypotheses, objectives, species with roles' },
    { id: 'targets', label: 'Targets', description: 'Genes, proteins, regions being studied' },
    { id: 'workflow', label: 'Workflow', description: 'Step-by-step analysis pipeline' },
    { id: 'tools', label: 'Tools', description: 'Set of tools to run anaylsis'},
    { id: 'files', label: 'Files', description: 'Local FASTA & attachments' },
    { id: 'conservation', label: 'Conservation', description: 'Map conservation scores onto 3D structures' },
    { id: 'narrative', label: 'Narrative', description: 'Long-form project write-up' },
    { id: 'reports', label: 'Reports', description: 'Generated summaries' }
  ];
</script>

<div class="shell">
  <SiteHeader
    user={data.user}
    crumbs={[
      { label: 'projects', href: '/projects' },
      { label: project.name }
    ]}
  />

  <main>
    <div class="project-header">
      <div class="title-line">
        {#if editingName}
          <input
            class="title-edit"
            bind:value={nameEditValue}
            onkeydown={(e) => e.key === 'Enter' ? saveName() : e.key === 'Escape' ? cancelNameEdit() : null}
            onblur={saveName}
            autofocus
          />
        {:else}
          <h1
            onclick={() => isOwner && startNameEdit()}
            class:editable={isOwner}
            title={isOwner ? 'Click to rename' : ''}
          >{project.name}</h1>
        {/if}
        <span class="vis-badge mono">{project.visibility}</span>
        <span class="status-badge status-{project.status} mono">{project.status}</span>
      </div>
      {#if project.description}
        <p class="project-desc">{project.description}</p>
      {/if}

      <div class="toolbar">
        {#if project.visibility !== 'private'}
          <button onclick={copyShareLink}>Copy link</button>
        {/if}
        {#if isOwner}
          <button class="danger" onclick={destroy}>Delete</button>
        {/if}
      </div>
    </div>

    <nav class="tabs">
      {#each TABS as tab}
        <button
          class="tab"
          class:active={activeTab === tab.id}
          onclick={() => selectTab(tab.id)}
          title={tab.description}
        >{tab.label}</button>
      {/each}
    </nav>
    <section class="tab-panel">
      {#if activeTab === 'overview'}
        <ProjectOverview {project} {isOwner} />
      {:else if activeTab === 'targets'}
        <ProjectTargets {project} {isOwner} />
      {:else if activeTab === 'workflow'}
        <ProjectWorkflow {project} {isOwner} />
      {:else if activeTab === 'files'}
        <ProjectFiles {project} {isOwner} />
      {:else if activeTab === 'tools'}
        <ProjectTools {project} {isOwner} />
      {:else if activeTab === 'conservation'}
        <ConservationPanel {project} />
      {:else if activeTab === 'narrative'}
        <ProjectNarrative {project} {isOwner} />
      {:else if activeTab === 'reports'}
        <ProjectNotesPlaceholder title="Reports" body="Generated summaries of project runs. Coming in a later phase." />
      {/if}
    </section>
  </main>
</div>

<style>
  .project-header { margin-bottom: 2rem; }
  .title-line { display: flex; gap: 1rem; align-items: baseline; margin-bottom: 0.5rem; flex-wrap: wrap; }
  h1 { font-size: clamp(1.6rem, 3vw, 2.3rem); }

  .vis-badge, .status-badge {
    padding: 0.2rem 0.55rem;
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    border: 1px solid var(--accent-dim);
    color: var(--accent);
    background: var(--accent-dim);
  }
  .status-badge {
    border-color: var(--border-strong);
    color: var(--fg-muted);
    background: var(--bg-inset);
  }
  .status-planning { color: var(--fg-muted); }
  .status-active { color: #7fd89c; border-color: #2a5c3a; background: rgba(42, 92, 58, 0.2); }
  .status-paused { color: #e0b060; border-color: #5c4c2a; background: rgba(92, 76, 42, 0.2); }
  .status-done { color: #7fa8d8; border-color: #2a4c5c; background: rgba(42, 76, 92, 0.2); }
  .status-archived { color: var(--fg-dim); }

  .project-desc { color: var(--fg-muted); margin: 0 0 1rem; }

  .toolbar { display: flex; gap: 0.5rem; }
  button.danger:hover { border-color: #c93535; color: #c93535; background: transparent; }

  .tabs {
    display: flex;
    gap: 0;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem;
    overflow-x: auto;
  }
  .tab {
    padding: 0.75rem 1.25rem;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.15s;
  }
  .tab:hover { color: var(--fg); }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .tab-panel {
    min-height: 300px;
  }

  h1.editable { cursor: text; }
  h1.editable:hover { color: var(--accent); }
  .title-edit {
    font-size: clamp(1.6rem, 3vw, 2.3rem);
    font-family: var(--font-display);
    padding: 0.2rem 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--accent);
    color: var(--fg);
    min-width: 20rem;
  }
</style>
