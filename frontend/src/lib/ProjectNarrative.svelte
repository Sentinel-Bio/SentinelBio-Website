<script lang="ts">
  import { onMount } from 'svelte';
  import { getNarrative, updateNarrative, listWorkflowSteps, type Project, type WorkflowStep } from '$lib/api';

  interface Props {
    project: Project;
    isOwner: boolean;
  }
  let { project, isOwner }: Props = $props();

  const narrativePlaceholder =
    'Write your project narrative here. Use markdown (#, ##, **bold**, *italic*, `code`). Embed a step result with {{step:full-id}}.';
  let narrative = $state('');
  let originalNarrative = $state('');
  let loading = $state(true);
  let saving = $state(false);
  let error = $state<string | null>(null);
  let steps = $state<WorkflowStep[]>([]);

  let showPreview = $state(true);
  let dirty = $derived(narrative !== originalNarrative);

  onMount(async () => {
  try {
    const narrativeRes = await getNarrative(project.slug);

    console.log('NARRATIVE RESPONSE:', narrativeRes);

    narrative = narrativeRes.narrative;
    originalNarrative = narrative;

  } catch (e) {
    console.error('NARRATIVE ERROR:', e);
    error = e instanceof Error ? e.message : 'failed to load';
  }

  loading = false;
});
  async function save() {
    saving = true;
    error = null;
    try {
      await updateNarrative(project.slug, narrative);
      originalNarrative = narrative;
    } catch (e) {
      error = e instanceof Error ? e.message : 'save failed';
    }
    saving = false;
  }

  function insertStepRef(stepId: string, stepName: string) {
    const ref = `{{step:${stepId}}}`;
    narrative = narrative + (narrative && !narrative.endsWith('\n') ? '\n\n' : '') + ref + '\n';
  }

  /** Render markdown-ish preview with step-embed resolution.
   *  Matches {{step:<uuid>}} and replaces with a card linking to the step.
   *  Everything else stays as-is; we're not a real markdown renderer (yet).
   */
  function renderedPreview(md: string): string {
    const stepMap = new Map(steps.map((s) => [s.id, s]));

    return md
      // Escape HTML first.
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      // Step embeds.
      .replace(/\{\{step:([a-f0-9-]+)\}\}/g, (_, id) => {
        const s = stepMap.get(id);
        if (!s) return `<div class="embed missing">unknown step: ${id}</div>`;
        return `<div class="embed">
          <div class="embed-label">STEP · ${s.kind.replace('_', ' ')}</div>
          <div class="embed-title">${s.name}</div>
          <div class="embed-status mono">status: ${s.status.replace('_', ' ')}</div>
        </div>`;
      })
      // Simple markdown: headings.
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      .replace(/^# (.+)$/gm, '<h1>$1</h1>')
      // Bold and italic.
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      // Code.
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // Paragraphs (double-newline separated blocks).
      .split(/\n\n+/)
      .map((block) => {
        if (block.match(/^<(h\d|div|ol|ul|pre)/)) return block;
        return `<p>${block.replace(/\n/g, '<br>')}</p>`;
      })
      .join('\n');
  }
</script>

<div class="narrative-view">
  <div class="section-head">
    <h3 class="section-title">Narrative · long-form writeup</h3>
    <div class="head-actions">
      <button
        class="toggle"
        class:active={showPreview}
        onclick={() => (showPreview = !showPreview)}
      >{showPreview ? 'Hide preview' : 'Show preview'}</button>
      {#if isOwner}
        <button
          class="primary"
          onclick={save}
          disabled={!dirty || saving}
        >{saving ? 'saving…' : dirty ? 'Save' : 'Saved'}</button>
      {/if}
    </div>
  </div>

  {#if loading}
    <p class="dim">Loading narrative…</p>
  {:else}
    {#if error}<p class="error mono">{error}</p>{/if}

    <div class="editor-row" class:split={showPreview}>
      <div class="editor-col">
        {#if isOwner}
          <textarea
            class="narrative-editor"
            bind:value={narrative}
            placeholder={narrativePlaceholder}
            spellcheck="true"
          ></textarea>

          {#if steps.length > 0}
            <div class="insert-helper">
              <span class="mono dim">Insert reference to a step:</span>
              <div class="step-refs">
                {#each steps as s}
                  <button
                    class="step-ref-btn mono"
                    onclick={() => insertStepRef(s.id, s.name)}
                    title={s.name}
                  >#{s.sort_order + 1} {s.name}</button>
                {/each}
              </div>
            </div>
          {/if}
        {:else}
          <pre class="narrative-readonly">{narrative || '(no narrative yet)'}</pre>
        {/if}
      </div>

      {#if showPreview}
        <div class="preview-col">
          {#if narrative.trim()}
            <div class="preview">
              {@html renderedPreview(narrative)}
            </div>
          {:else}
            <p class="dim preview-empty">Preview appears here as you write.</p>
          {/if}
        </div>
      {/if}
    </div>
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
  .head-actions { display: flex; gap: 0.5rem; }
  .toggle {
    padding: 0.35rem 0.85rem;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.72rem;
  }
  .toggle.active { color: var(--accent); border-color: var(--accent); }

  .editor-row {
    display: grid;
    grid-template-columns: 1fr;
    gap: 1.5rem;
  }
  .editor-row.split { grid-template-columns: 1fr 1fr; }
  @media (max-width: 900px) {
    .editor-row.split { grid-template-columns: 1fr; }
  }

  .editor-col { display: flex; flex-direction: column; gap: 0.75rem; }

  .narrative-editor {
    width: 100%;
    min-height: 500px;
    padding: 1rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.88rem;
    line-height: 1.6;
    resize: vertical;
  }
  .narrative-editor:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }

  .narrative-readonly {
    padding: 1rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    font-family: var(--font-mono);
    font-size: 0.88rem;
    line-height: 1.6;
    white-space: pre-wrap;
    min-height: 300px;
  }

  .insert-helper {
    padding: 0.75rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
  }
  .step-refs {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    margin-top: 0.5rem;
  }
  .step-ref-btn {
    padding: 0.3rem 0.7rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    font-size: 0.72rem;
    max-width: 24rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .step-ref-btn:hover { color: var(--accent); border-color: var(--accent); }

  .preview-col {
    padding: 1rem;
    background: var(--bg-base);
    border: 1px solid var(--border);
    max-height: 700px;
    overflow-y: auto;
  }
  .preview {
    font-size: 0.95rem;
    line-height: 1.7;
  }
  :global(.preview h1) { font-family: var(--font-display); font-size: 1.6rem; margin: 0 0 0.75rem; }
  :global(.preview h2) { font-family: var(--font-display); font-size: 1.3rem; margin: 1.2rem 0 0.5rem; }
  :global(.preview h3) { font-family: var(--font-display); font-size: 1.1rem; margin: 1rem 0 0.4rem; }
  :global(.preview p) { margin: 0.5rem 0 0.9rem; }
  :global(.preview code) {
    font-family: var(--font-mono);
    font-size: 0.85em;
    background: var(--bg-inset);
    padding: 0.1rem 0.35rem;
  }
  :global(.preview .embed) {
    display: block;
    margin: 1rem 0;
    padding: 0.9rem 1.1rem;
    background: var(--bg-raised);
    border: 1px solid var(--accent-dim);
    border-left: 3px solid var(--accent);
  }
  :global(.preview .embed-label) {
    font-family: var(--font-mono);
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--accent);
    margin-bottom: 0.3rem;
  }
  :global(.preview .embed-title) {
    font-family: var(--font-display);
    font-size: 1rem;
    margin-bottom: 0.2rem;
  }
  :global(.preview .embed-status) {
    font-size: 0.72rem;
    color: var(--fg-dim);
  }
  :global(.preview .embed.missing) {
    border-color: #c93535;
    color: #c93535;
    font-family: var(--font-mono);
    font-size: 0.82rem;
  }

  .preview-empty { padding: 2rem; text-align: center; }
  .error { color: #c93535; }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
</style>
