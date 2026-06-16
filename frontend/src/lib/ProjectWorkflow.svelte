<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listWorkflowSteps, createWorkflowStep, updateWorkflowStep,
    deleteWorkflowStep, reorderWorkflowSteps,
    type Project, type WorkflowStep
  } from '$lib/api';
  import WorkflowStepEditor from '$lib/WorkflowStepEditor.svelte';

  interface Props {
    project: Project;
    isOwner: boolean;
  }
  let { project, isOwner }: Props = $props();

  let steps = $state<WorkflowStep[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  let editing = $state<string | null>(null); // step id being edited, or 'new'
  let newDraftVisible = $state(false);

  onMount(load);

  async function load() {
    loading = true;
    error = null;
    try {
      steps = await listWorkflowSteps(project.slug);
    } catch (e) {
      error = e instanceof Error ? e.message : 'failed to load';
    }
    loading = false;
  }

  async function saveNew(draft: any) {
    await createWorkflowStep(project.slug, {
      ...draft,
      sort_order: steps.length
    });
    newDraftVisible = false;
    await load();
  }

  async function saveEdit(stepId: string, draft: any) {
    await updateWorkflowStep(project.slug, stepId, draft);
    editing = null;
    await load();
  }

  async function remove(step: WorkflowStep) {
    if (!confirm(`Delete step "${step.name}"?`)) return;
    try {
      await deleteWorkflowStep(project.slug, step.id);
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'delete failed');
    }
  }

  async function moveUp(i: number) {
    if (i === 0) return;
    const newOrder = [...steps];
    [newOrder[i - 1], newOrder[i]] = [newOrder[i], newOrder[i - 1]];
    steps = newOrder;
    try {
      await reorderWorkflowSteps(project.slug, newOrder.map((s) => s.id));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'reorder failed');
      await load();
    }
  }

  async function moveDown(i: number) {
    if (i >= steps.length - 1) return;
    const newOrder = [...steps];
    [newOrder[i], newOrder[i + 1]] = [newOrder[i + 1], newOrder[i]];
    steps = newOrder;
    try {
      await reorderWorkflowSteps(project.slug, newOrder.map((s) => s.id));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'reorder failed');
      await load();
    }
  }

  async function cycleStatus(step: WorkflowStep) {
    const order: WorkflowStep['status'][] = ['pending', 'in_progress', 'blocked', 'done', 'skipped'];
    const curIdx = order.indexOf(step.status);
    const next = order[(curIdx + 1) % order.length];
    try {
      await updateWorkflowStep(project.slug, step.id, { status: next });
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'update failed');
    }
  }

  function statusColor(status: WorkflowStep['status']): string {
    switch (status) {
      case 'pending': return 'var(--fg-dim)';
      case 'in_progress': return '#e0b060';
      case 'blocked': return '#c93535';
      case 'done': return '#7fd89c';
      case 'skipped': return 'var(--fg-muted)';
    }
  }

  function kindLabel(kind: WorkflowStep['kind']): string {
    return kind.replace('_', ' ');
  }
</script>

<div class="workflow-view">
  <div class="section-head">
    <h3 class="section-title">Workflow steps ({steps.length})</h3>
    {#if isOwner && !newDraftVisible}
      <button onclick={() => { newDraftVisible = true; editing = null; }}>+ Add step</button>
    {/if}
  </div>

  {#if newDraftVisible}
    <WorkflowStepEditor
      initial={{}}
      onsave={saveNew}
      oncancel={() => (newDraftVisible = false)}
    />
  {/if}

  {#if loading}
    <p class="dim">Loading steps…</p>
  {:else if error}
    <p class="error mono">{error}</p>
  {:else if steps.length === 0 && !newDraftVisible}
    <div class="empty">
      <p>No workflow steps yet.</p>
      <p class="dim">
        Break down your research into named steps. Each step can be manual (just notes),
        an external tool (PROVEAN, DnaSP), a review checkpoint, or a backend job (coming in Phase 2).
      </p>
    </div>
  {:else}
    <ol class="step-list">
      {#each steps as step, i (step.id)}
        {#if editing === step.id}
          <li>
            <WorkflowStepEditor
              initial={step}
              onsave={(d) => saveEdit(step.id, d)}
              oncancel={() => (editing = null)}
            />
          </li>
        {:else}
          <li class="step-card">
            <div class="step-head">
              <div class="step-num mono">{i + 1}</div>
              <div class="step-title-line">
                <h4>{step.name}</h4>
                <span class="kind-pill mono">{kindLabel(step.kind)}</span>
                <button
                  class="status-chip mono"
                  style:color={statusColor(step.status)}
                  style:border-color={statusColor(step.status)}
                  onclick={() => isOwner && cycleStatus(step)}
                  disabled={!isOwner}
                  title={isOwner ? 'Click to advance status' : ''}
                >{step.status.replace('_', ' ')}</button>
              </div>
              {#if isOwner}
                <div class="step-actions">
                  <button class="mini" onclick={() => moveUp(i)} disabled={i === 0} title="Move up">↑</button>
                  <button class="mini" onclick={() => moveDown(i)} disabled={i === steps.length - 1} title="Move down">↓</button>
                  <button class="mini" onclick={() => (editing = step.id)}>Edit</button>
                  <button class="mini danger" onclick={() => remove(step)}>×</button>
                </div>
              {/if}
            </div>

            {#if step.description}
              <p class="step-desc">{step.description}</p>
            {/if}

            {#if step.kind === 'external_tool' && (step.external_tool_name || step.external_url)}
              <div class="step-detail">
                <span class="mono dim">tool</span>
                <span>{step.external_tool_name ?? 'external'}</span>
                {#if step.external_url}
                  <a href={step.external_url} target="_blank" rel="noreferrer">↗ open</a>
                {/if}
              </div>
            {/if}

            {#if step.kind === 'backend_job' && step.backend_job_type}
              <div class="step-detail">
                <span class="mono dim">job</span>
                <span class="mono">{step.backend_job_type}</span>
                <span class="dim mono">(not yet runnable)</span>
              </div>
            {/if}

            {#if step.notes}
              <div class="step-notes">{step.notes}</div>
            {/if}

            {#if step.output_data && Object.keys(step.output_data).length > 0}
              <details class="step-output">
                <summary class="mono dim">Output / results</summary>
                <pre>{JSON.stringify(step.output_data, null, 2)}</pre>
              </details>
            {/if}

            <div class="step-ref mono dim">
              id: <code>{step.id.slice(0, 8)}</code> · embed in narrative with <code>{'{{step:' + step.id.slice(0, 8) + '...}}'}</code>
            </div>
          </li>
        {/if}
      {/each}
    </ol>
  {/if}
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

  .empty { padding: 2rem 1rem; text-align: center; color: var(--fg-muted); }
  .empty p { margin: 0.3rem 0; }

  .step-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.75rem; }

  .step-card {
    padding: 1.1rem 1.25rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent-dim);
  }

  .step-head {
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 0.75rem;
    align-items: start;
  }
  .step-num {
    font-size: 1.3rem;
    color: var(--fg-dim);
    line-height: 1;
    padding-top: 0.25rem;
  }
  .step-title-line {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    flex-wrap: wrap;
    min-width: 0;
  }
  .step-title-line h4 {
    margin: 0;
    font-family: var(--font-display);
    font-size: 1.05rem;
  }
  .kind-pill {
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    padding: 0.18rem 0.55rem;
    background: var(--accent-dim);
    color: var(--accent);
    border: 1px solid var(--accent-dim);
  }
  .status-chip {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    padding: 0.18rem 0.55rem;
    background: transparent;
    border: 1px solid;
    cursor: pointer;
  }
  .status-chip:disabled { cursor: default; }

  .step-actions { display: flex; gap: 0.3rem; }
  .mini {
    padding: 0.2rem 0.5rem;
    font-size: 0.75rem;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--fg-muted);
  }
  .mini:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .mini:disabled { opacity: 0.3; cursor: not-allowed; }
  .mini.danger:hover { color: #c93535; border-color: #c93535; }

  .step-desc {
    margin: 0.6rem 0 0;
    color: var(--fg-muted);
    font-size: 0.9rem;
    padding-left: 2.25rem;
  }
  .step-detail {
    margin: 0.6rem 0 0;
    padding-left: 2.25rem;
    font-size: 0.82rem;
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
  }
  .step-notes {
    margin: 0.75rem 0 0;
    padding: 0.75rem 0.9rem 0.75rem 2.25rem;
    background: var(--bg-inset);
    font-size: 0.88rem;
    line-height: 1.5;
    white-space: pre-wrap;
  }
  .step-output {
    margin: 0.6rem 0 0;
    padding-left: 2.25rem;
  }
  .step-output summary {
    cursor: pointer;
    padding: 0.3rem 0;
  }
  .step-output pre {
    background: var(--bg-inset);
    padding: 0.75rem;
    font-size: 0.78rem;
    overflow-x: auto;
    margin: 0.4rem 0 0;
  }
  .step-ref {
    margin-top: 0.75rem;
    padding-left: 2.25rem;
    font-size: 0.7rem;
  }
  .step-ref code {
    font-family: var(--font-mono);
    background: var(--bg-inset);
    padding: 0.1rem 0.4rem;
  }

  .error { color: #c93535; }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); font-size: 0.72rem; }
</style>
