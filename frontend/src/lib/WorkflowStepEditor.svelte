<script lang="ts">
  import type { WorkflowStep } from '$lib/api';

  interface Draft {
    name: string;
    description: string;
    kind: WorkflowStep['kind'];
    status: WorkflowStep['status'];
    external_url: string;
    external_tool_name: string;
    backend_job_type: string;
    backend_job_params: string; // JSON as string for editing
    output_data: string;        // JSON as string for editing
    notes: string;
  }

  interface Props {
    initial: Partial<WorkflowStep>;
    onsave: (draft: Omit<WorkflowStep, 'id' | 'project_id' | 'sort_order' | 'input_refs' | 'backend_run_id' | 'created_at' | 'updated_at'> & {
      backend_job_params: Record<string, unknown> | null;
      output_data: Record<string, unknown>;
    }) => Promise<void>;
    oncancel: () => void;
  }

  let { initial, onsave, oncancel }: Props = $props();

  let draft = $state<Draft>({
    name: initial.name ?? '',
    description: initial.description ?? '',
    kind: initial.kind ?? 'manual',
    status: initial.status ?? 'pending',
    external_url: initial.external_url ?? '',
    external_tool_name: initial.external_tool_name ?? '',
    backend_job_type: initial.backend_job_type ?? '',
    backend_job_params: initial.backend_job_params
      ? JSON.stringify(initial.backend_job_params, null, 2)
      : '{}',
    output_data: initial.output_data
      ? JSON.stringify(initial.output_data, null, 2)
      : '{}',
    notes: initial.notes ?? ''
  });

  let saving = $state(false);
  let error = $state<string | null>(null);

  const KINDS: Array<{ value: WorkflowStep['kind']; label: string; help: string }> = [
    { value: 'manual', label: 'Manual', help: 'Describe work done outside the platform (notes + paste-in results).' },
    { value: 'external_tool', label: 'External tool', help: 'Point at a web tool (PROVEAN, DnaSP, BLAST). Record inputs and paste outputs.' },
    { value: 'backend_job', label: 'Backend job', help: 'Run a computational step on our server. Wiring up in Phase 2.' },
    { value: 'file_upload', label: 'File upload', help: 'Attach FASTA/CSV/etc. Tied to the Files tab (localStorage).' },
    { value: 'review', label: 'Review', help: 'Checkpoint to review output from prior steps before continuing.' }
  ];

  const STATUSES: WorkflowStep['status'][] = ['pending', 'in_progress', 'blocked', 'done', 'skipped'];

  async function save() {
    saving = true;
    error = null;
    try {
      // Parse JSON fields. Accept empty strings as empty objects.
      let parsedParams: Record<string, unknown> | null = null;
      let parsedOutput: Record<string, unknown> = {};
      try {
        parsedParams = draft.backend_job_params.trim()
          ? JSON.parse(draft.backend_job_params)
          : null;
      } catch {
        throw new Error('backend_job_params must be valid JSON');
      }
      try {
        parsedOutput = draft.output_data.trim()
          ? JSON.parse(draft.output_data)
          : {};
      } catch {
        throw new Error('output_data must be valid JSON');
      }

      await onsave({
        name: draft.name,
        description: draft.description || null,
        kind: draft.kind,
        status: draft.status,
        external_url: draft.kind === 'external_tool' ? (draft.external_url || null) : null,
        external_tool_name: draft.kind === 'external_tool' ? (draft.external_tool_name || null) : null,
        backend_job_type: draft.kind === 'backend_job' ? (draft.backend_job_type || null) : null,
        backend_job_params: draft.kind === 'backend_job' ? parsedParams : null,
        output_data: parsedOutput,
        notes: draft.notes || null,
        completed_at: null // server computes this on status flip — we leave null
      } as any);
    } catch (e) {
      error = e instanceof Error ? e.message : 'save failed';
    }
    saving = false;
  }

  const outputPlaceholder =
    '{"key": "value"} or free-form paste — wrapped into {"text": ...} if not JSON';
</script>

<div class="step-editor">
  <div class="field">
    <label>Name</label>
    <input bind:value={draft.name} placeholder="e.g. Align TP53 sequences with MAFFT" />
  </div>

  <div class="field">
    <label>Description</label>
    <textarea bind:value={draft.description} rows="2" placeholder="What does this step do?"></textarea>
  </div>

  <div class="field">
    <label>Kind</label>
    <div class="kind-opts">
      {#each KINDS as k}
        <button
          class="kind-opt"
          class:active={draft.kind === k.value}
          onclick={() => (draft.kind = k.value)}
          title={k.help}
        >{k.label}</button>
      {/each}
    </div>
    <p class="kind-help mono dim">{KINDS.find((k) => k.value === draft.kind)?.help}</p>
  </div>

  <div class="field">
    <label>Status</label>
    <div class="status-opts">
      {#each STATUSES as s}
        <button
          class="status-opt"
          class:active={draft.status === s}
          onclick={() => (draft.status = s)}
        >{s.replace('_', ' ')}</button>
      {/each}
    </div>
  </div>

  {#if draft.kind === 'external_tool'}
    <div class="kind-fields">
      <div class="field">
        <label>Tool name</label>
        <input bind:value={draft.external_tool_name} placeholder="PROVEAN, DnaSP, BLAST…" />
      </div>
      <div class="field">
        <label>Tool URL</label>
        <input bind:value={draft.external_url} placeholder="https://…" />
      </div>
    </div>
  {/if}

  {#if draft.kind === 'backend_job'}
    <div class="kind-fields">
      <div class="field">
        <label>Job type</label>
        <input bind:value={draft.backend_job_type} placeholder="mafft_align, translate, iqtree_phylo…" disabled />
      </div>
      <div class="field">
        <label>Parameters (JSON)</label>
        <textarea bind:value={draft.backend_job_params} rows="4" class="mono" disabled></textarea>
      </div>
      <p class="dim mono">Backend jobs become runnable in Phase 2.</p>
    </div>
  {/if}

  {#if draft.kind === 'file_upload'}
    <p class="dim mono">File attachments live on the Files tab (localStorage per species).</p>
  {/if}

  <div class="field">
    <label>Output / results (JSON or free text)</label>
    <textarea
      bind:value={draft.output_data}
      rows="4"
      class="mono"
      placeholder={outputPlaceholder}
    ></textarea>
  </div>

  <div class="field">
    <label>Notes (markdown)</label>
    <textarea
      bind:value={draft.notes}
      rows="6"
      placeholder="Method details, parameters, what you observed, how you interpret the results…"
    ></textarea>
  </div>

  {#if error}<p class="error mono">{error}</p>{/if}

  <div class="actions">
    <button onclick={oncancel}>Cancel</button>
    <button class="primary" onclick={save} disabled={saving || !draft.name.trim()}>
      {saving ? 'saving…' : 'Save step'}
    </button>
  </div>
</div>

<style>
  .step-editor {
    padding: 1.25rem;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    margin-bottom: 1rem;
  }

  .field { display: flex; flex-direction: column; gap: 0.4rem; margin-bottom: 1rem; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--fg-dim);
  }
  .field input, .field textarea, .field select {
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
  .field textarea.mono { font-family: var(--font-mono); font-size: 0.8rem; }

  .kind-opts, .status-opts {
    display: flex;
    gap: 0.3rem;
    flex-wrap: wrap;
  }
  .kind-opt, .status-opt {
    padding: 0.4rem 0.85rem;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.72rem;
    text-transform: lowercase;
    cursor: pointer;
  }
  .kind-opt.active, .status-opt.active {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent);
  }
  .kind-help {
    margin-top: 0.4rem;
    font-size: 0.72rem;
    line-height: 1.4;
  }

  .kind-fields {
    padding: 0.9rem 1rem;
    background: var(--bg-inset);
    border: 1px dashed var(--border);
    margin-bottom: 1rem;
  }

  .actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
    margin-top: 1.25rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
  }

  .error { color: #c93535; font-size: 0.85rem; margin: 0.5rem 0 0; }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
</style>
