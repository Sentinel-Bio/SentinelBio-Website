<script lang="ts">
  import { onMount } from 'svelte';
  import { listProjectFiles, type ToolDef, type ProjectFile } from '$lib/api';
  import ProjectFilePicker, { type FilePickerSelection } from '$lib/ProjectFilePicker.svelte';

  interface Props {
    params: ToolDef['params'];
    values: Record<string, any>;
    onchange: (values: Record<string, any>) => void;
    projectSlug?: string;
  }

  let { params, values, onchange, projectSlug }: Props = $props();

  let projectFiles = $state<ProjectFile[]>([]);
  let filesLoaded = $state(false);

  const needsFilePicker = $derived(
    params.some((p) =>
      p.type === 'project_file' || p.type === 'project_file_fasta'
    )
  );

  onMount(async () => {
    if (needsFilePicker && projectSlug) {
      try {
        projectFiles = await listProjectFiles(projectSlug);
      } catch (e) {
        console.warn('failed to load project files for param picker', e);
      }
      filesLoaded = true;
    }
  });

  function update(name: string, raw: any) {
    onchange({ ...values, [name]: raw });
  }

  function castInt(v: string): number | string {
    const n = parseInt(v, 10);
    return isNaN(n) ? v : n;
  }
  function castFloat(v: string): number | string {
    const n = parseFloat(v);
    return isNaN(n) ? v : n;
  }

  function filesForParam(type: string): ProjectFile[] {
    if (type === 'project_file_fasta') {
      return projectFiles.filter((f) => f.mime_hint === 'fasta');
    }
    return projectFiles;
  }

  // Multi-file picker: emit comma-separated IDs so backend tools that take
  // string CSV params (like mhcxgraph.structure_file_ids) work without change.
  function onMultiPick(name: string, sel: FilePickerSelection[]) {
    update(name, sel.map((s) => s.id).join(','));
  }
</script>

{#if params.length > 0}
  <div class="params-form">
    <h5 class="params-head mono">Parameters</h5>
    {#each params as p}
      <div class="param">
        <label>
          {p.label}
          {#if p.help}<span class="help dim" title={p.help}>?</span>{/if}
        </label>

        {#if p.type === 'int'}
          <input
            type="number"
            value={values[p.name] ?? p.default}
            min={p.min ?? undefined}
            max={p.max ?? undefined}
            step="1"
            oninput={(e) => update(p.name, castInt((e.target as HTMLInputElement).value))}
          />
        {:else if p.type === 'float'}
          <input
            type="number"
            value={values[p.name] ?? p.default}
            min={p.min ?? undefined}
            max={p.max ?? undefined}
            step="any"
            oninput={(e) => update(p.name, castFloat((e.target as HTMLInputElement).value))}
          />
        {:else if p.type === 'bool'}
          <input
            type="checkbox"
            checked={values[p.name] ?? p.default}
            onchange={(e) => update(p.name, (e.target as HTMLInputElement).checked)}
          />
        {:else if p.type === 'enum'}
          <select
            value={values[p.name] ?? p.default}
            onchange={(e) => update(p.name, (e.target as HTMLSelectElement).value)}
          >
            {#each (p.options ?? []) as opt}
              <option value={opt}>{opt}</option>
            {/each}
          </select>
        {:else if p.type === 'project_file' || p.type === 'project_file_fasta'}
          {#if !filesLoaded}
            <p class="hint dim mono">loading project files…</p>
          {:else if filesForParam(p.type).length === 0}
            <p class="hint dim mono">No matching files in this project.</p>
          {:else}
            <select
              value={values[p.name] ?? ''}
              onchange={(e) => update(p.name, (e.target as HTMLSelectElement).value)}
            >
              <option value="">— pick a file —</option>
              {#each filesForParam(p.type) as f}
                <option value={f.id}>
                  {f.name} ({(f.size / 1_000_000).toFixed(1)} MB)
                </option>
              {/each}
            </select>
          {/if}
        {:else if p.type === 'project_files_multi' && projectSlug}
          <ProjectFilePicker
            {projectSlug}
            kind={p.kind ?? undefined}
            category={p.category ?? undefined}
            minCount={p.min ?? 1}
            maxCount={p.max ?? 999}
            label={p.label}
            onchange={(sel) => onMultiPick(p.name, sel)}
          />
        {:else if p.type === 'text_long'}
          <textarea
            rows="5"
            value={values[p.name] ?? p.default ?? ''}
            placeholder={p.help}
            oninput={(e) => update(p.name, (e.target as HTMLTextAreaElement).value)}
          ></textarea>
        {:else}
          <input
            type="text"
            value={values[p.name] ?? p.default ?? ''}
            placeholder={p.help}
            oninput={(e) => update(p.name, (e.target as HTMLInputElement).value)}
          />
        {/if}

        {#if p.help}<p class="hint mono dim">{p.help}</p>{/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .params-form {
    margin-top: 0.5rem;
    padding: 0.7rem 0.9rem;
    background: var(--bg-base);
    border: 1px dashed var(--border);
  }
  .params-head {
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--fg-dim);
    margin: 0 0 0.6rem;
  }
  .param { margin-bottom: 0.7rem; }
  .param label {
    display: flex;
    gap: 0.4rem;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--fg-dim);
    margin-bottom: 0.25rem;
  }
  .help {
    cursor: help;
    border: 1px solid var(--border);
    width: 14px; height: 14px;
    text-align: center;
    line-height: 14px;
    font-size: 0.62rem;
  }
  .param input[type="text"], .param input[type="number"], .param select, .param textarea {
    width: 100%;
    padding: 0.4rem 0.55rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-body);
    font-size: 0.88rem;
  }
  .param textarea {
    font-family: var(--font-mono);
    font-size: 0.78rem;
    resize: vertical;
  }
  .hint {
    font-size: 0.66rem;
    margin: 0.2rem 0 0;
    line-height: 1.4;
  }
</style>
