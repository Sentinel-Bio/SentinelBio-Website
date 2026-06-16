<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    listToolRegistry, listToolRuns, createToolRun, createToolRunWithUpload,
    deleteToolRun, renameRun, retryToolRun, getProjectFileText,
    type ToolDef, type ToolRun, type Project,
  } from '$lib/api';
  import ToolParamsForm from '$lib/ToolParamsForm.svelte';
  import FastaMultiPicker, { type SelectedFile } from '$lib/FastaMultiPicker.svelte';
  import AlignmentViewer from '$lib/AlignmentViewer.svelte';
  import SuperpositionViewer from '$lib/SuperpositionViewer.svelte';
  import PhylogenyTree from '$lib/charts/PhylogenyTree.svelte';
  import PopStatsReport from '$lib/PopStatsReport.svelte';
  import FastqcReport from '$lib/FastqcReport.svelte';
  import BlastReport from '$lib/BlastReport.svelte';
  import LogConsole from '$lib/LogConsole.svelte';

  interface Props {
    project: Project;
    isOwner: boolean;
  }
  let { project, isOwner }: Props = $props();

  let tools = $state<ToolDef[]>([]);
  let runs = $state<ToolRun[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // New-run state.
  let pickerOpen = $state(false);
  let selectedTool = $state<ToolDef | null>(null);
  let runLabel = $state('');
  let selectedSpeciesId = $state<string | null>(null);
  let selectedFile = $state<File | null>(null);
  let multiSelected = $state<SelectedFile[]>([]);
  let paramValues = $state<Record<string, any>>({});
  let submitting = $state(false);

  // Result viewer.
  let viewingRun = $state<ToolRun | null>(null);
  let viewerAlignmentText = $state<string | null>(null);
  // Structural superposition viewer payload (struct_align usalign mode).
  let viewerStructures = $state<Array<{ name: string; data: string; color: number }> | null>(null);
  const _STRUCT_COLORS = [0x4fa8e0, 0xe0a040, 0x7fd89c, 0xc97fd8, 0xe06868, 0x68c9c9, 0xb0b040, 0xd87fa8];
  let viewerLoading = $state(false);

  // Per-run expanded console (run.id -> open?). The console renders run.logs.
  let openLogs = $state<Set<string>>(new Set());
  function toggleLogs(runId: string) {
    const next = new Set(openLogs);
    if (next.has(runId)) next.delete(runId); else next.add(runId);
    openLogs = next;
  }

  let pollHandle: number | null = null;

  onMount(async () => {
    try {
      [tools, runs] = await Promise.all([listToolRegistry(), listToolRuns(project.slug)]);
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
    loading = false;
    startPolling();
  });

  onDestroy(() => {
    if (pollHandle !== null) clearInterval(pollHandle);
  });

  function startPolling() {
    pollHandle = window.setInterval(async () => {
      const hasActive = runs.some((r) => r.status === 'queued' || r.status === 'running');
      // Also refresh while any console is open, so logs of an active run keep
      // streaming; and keep refreshing briefly after completion so the final
      // log lines land. (Cheap: one request, single-user app.)
      const watchingLogs = openLogs.size > 0;
      if (!hasActive && !watchingLogs) return;
      try {
        runs = await listToolRuns(project.slug);
        // Keep the open viewer in sync with the freshest row.
        if (viewingRun) {
          const fresh = runs.find((r) => r.id === viewingRun!.id);
          if (fresh) viewingRun = fresh;
        }
      } catch {}
    }, 2000);
  }

  async function retry(run: ToolRun) {
    try {
      const newRun = await retryToolRun(project.slug, run.id);
      runs = [newRun, ...runs];
      // Open the new run's console so the user sees it start working.
      const next = new Set(openLogs);
      next.add(newRun.id);
      openLogs = next;
    } catch (e) {
      alert(e instanceof Error ? e.message : 'retry failed');
    }
  }

  function editAndRetry(run: ToolRun) {
    // Pre-fill the new-run form from this run's tool + params, then open the
    // picker so the user can tweak and submit. Inputs (file selections) can't
    // always be reconstructed, so we restore params and let the user re-pick
    // files; we surface a note in the form when that's the case.
    const tool = tools.find((t) => t.id === run.tool);
    if (!tool) {
      alert(`Tool "${run.tool}" is no longer in the registry.`);
      return;
    }
    selectedTool = tool;
    // Start from tool defaults, then overlay the previous run's params.
    const merged: Record<string, any> = {};
    for (const p of tool.params) merged[p.name] = p.default;
    Object.assign(merged, run.params ?? {});
    paramValues = merged;
    runLabel = (run.label ?? '').replace(/ \(retry\)$/, '') + ' (edited)';
    // File selections: restore from inputs.source_files when present.
    multiSelected = (run.inputs?.source_files as SelectedFile[] | undefined) ?? [];
    if (!multiSelected.length && run.inputs?.source_file) {
      multiSelected = [run.inputs.source_file as SelectedFile];
    }
    selectedFile = null;
    selectedSpeciesId = (run.inputs?.species_id as string | undefined) ?? null;
    error = null;
    pickerOpen = true;
  }

  function selectTool(t: ToolDef) {
    selectedTool = t;
    const defaults: Record<string, any> = {};
    for (const p of t.params) defaults[p.name] = p.default;
    paramValues = defaults;
    multiSelected = [];
    selectedFile = null;
    selectedSpeciesId = null;
    error = null;
  }

  async function submit() {
    if (!selectedTool) return;
    submitting = true;
    error = null;
    try {
      let newRun: ToolRun;

      switch (selectedTool.input_kind) {
        case 'fastq_upload':
        case 'fasta_upload': {
          if (!selectedFile) throw new Error('Select a file');
          newRun = await createToolRunWithUpload(project.slug, {
            tool: selectedTool.id,
            label: runLabel || selectedFile.name,
            file: selectedFile,
            species_id: selectedSpeciesId ?? undefined,
            params: paramValues,
          });
          break;
        }

        case 'multi_fasta': {
          if (multiSelected.length < 2) {
            throw new Error(`${selectedTool.label} needs at least 2 FASTA files`);
          }
          newRun = await createToolRun(project.slug, {
            tool: selectedTool.id,
            label: runLabel || `${selectedTool.id}: ${multiSelected.length} files`,
            inputs: {
              file_ids: multiSelected.map((f) => f.id),
              source_files: multiSelected,
            },
            params: paramValues,
          });
          break;
        }

        case 'aligned_fasta': {
          if (multiSelected.length !== 1) {
            throw new Error(`${selectedTool.label} needs exactly 1 aligned FASTA file`);
          }
          newRun = await createToolRun(project.slug, {
            tool: selectedTool.id,
            label: runLabel || `${selectedTool.id}: ${multiSelected[0].name}`,
            inputs: {
              file_ids: [multiSelected[0].id],
              source_file: multiSelected[0],
            },
            params: paramValues,
          });
          break;
        }

        case 'none':
        default: {
          newRun = await createToolRun(project.slug, {
            tool: selectedTool.id,
            label: runLabel || selectedTool.label,
            inputs: {},
            params: paramValues,
          });
        }
      }

      runs = [newRun, ...runs];
      pickerOpen = false;
      selectedTool = null;
      selectedFile = null;
      selectedSpeciesId = null;
      runLabel = '';
      paramValues = {};
      multiSelected = [];
    } catch (e) {
      error = e instanceof Error ? e.message : 'submit failed';
    }
    submitting = false;
  }

  async function remove(run: ToolRun) {
    if (!confirm(`Delete run "${run.label ?? run.tool}"?`)) return;
    try {
      await deleteToolRun(project.slug, run.id);
      runs = runs.filter((r) => r.id !== run.id);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'delete failed');
    }
  }

  // ── Multi-select for jobs ─────────────────────────────────────
  let selectedRunIds = $state<Set<string>>(new Set());
  let bulkDeleting = $state(false);
  let bulkProgress = $state({ done: 0, total: 0 });

  function toggleRunSelected(id: string) {
    const next = new Set(selectedRunIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedRunIds = next;
  }

  function selectAllVisibleRuns() {
    selectedRunIds = new Set(runs.map((r) => r.id));
  }

  function clearRunSelection() {
    selectedRunIds = new Set();
  }

  async function bulkDeleteRuns() {
    const ids = Array.from(selectedRunIds);
    if (ids.length === 0) return;
    if (!confirm(
      `Delete ${ids.length} run${ids.length === 1 ? '' : 's'}? This cannot be undone.`
    )) return;
    bulkDeleting = true;
    bulkProgress = { done: 0, total: ids.length };
    const CONCURRENCY = 6;
    let cursor = 0;
    const failures: string[] = [];
    async function worker() {
      while (cursor < ids.length) {
        const idx = cursor++;
        const id = ids[idx];
        try {
          await deleteToolRun(project.slug, id);
        } catch {
          failures.push(id);
        }
        bulkProgress = { done: bulkProgress.done + 1, total: ids.length };
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));
    if (failures.length > 0) {
      alert(`${failures.length} delete${failures.length === 1 ? '' : 's'} failed.`);
    }
    selectedRunIds = new Set();
    bulkDeleting = false;
    // Refresh runs list
    runs = runs.filter((r) => !ids.includes(r.id) || failures.includes(r.id));
  }

  // ── Inline rename for jobs ────────────────────────────────────
  let renamingRunId = $state<string | null>(null);
  let runRenameDraft = $state('');

  function startRunRename(run: ToolRun) {
    renamingRunId = run.id;
    runRenameDraft = run.label ?? run.tool;
  }

  function cancelRunRename() {
    renamingRunId = null;
    runRenameDraft = '';
  }

  async function commitRunRename(run: ToolRun) {
    const newLabel = runRenameDraft.trim();
    if (!newLabel || newLabel === (run.label ?? run.tool)) {
      cancelRunRename();
      return;
    }
    try {
      await renameRun(project.slug, run.id, newLabel);
      runs = runs.map((r) => (r.id === run.id ? { ...r, label: newLabel } : r));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'rename failed');
    }
    cancelRunRename();
  }

  // Svelte action: focus + select an input when it mounts.
  function focusOnMount(node: HTMLInputElement) {
    node.focus();
    node.select();
  }

  async function openViewer(run: ToolRun) {
    viewingRun = run;
    viewerAlignmentText = null;
    viewerStructures = null;

    // For MAFFT: fetch the aligned FASTA from the saved output file.
    if (run.tool === 'mafft' && run.result?.output_file_ids?.length) {
      viewerLoading = true;
      try {
        const data = await getProjectFileText(project.slug, run.result.output_file_ids[0]);
        viewerAlignmentText = data.text;
      } catch (e) {
        console.warn('alignment fetch failed', e);
      }
      viewerLoading = false;
    }

    // For struct_align (usalign): load all superposed PDBs into one Mol* scene,
    // plus the structure-derived alignment FASTA. Classify files by name suffix.
    if (run.tool === 'struct_align' && run.result?.output_file_ids?.length) {
      viewerLoading = true;
      try {
        const files = await Promise.all(
          run.result.output_file_ids.map((id: string) =>
            getProjectFileText(project.slug, id).catch(() => null)
          )
        );
        const structs: Array<{ name: string; data: string; color: number }> = [];
        let ci = 0;
        // Reference first (so it's color 0), then superposed mobiles.
        const ref = files.find((f) => f && /_reference\.pdb$/.test(f.name));
        if (ref) structs.push({ name: ref.name.replace(/_reference\.pdb$/, ''), data: ref.text, color: _STRUCT_COLORS[ci++] });
        for (const f of files) {
          if (!f) continue;
          if (/_superposed\.pdb$/.test(f.name)) {
            structs.push({ name: f.name.replace(/_superposed\.pdb$/, ''), data: f.text, color: _STRUCT_COLORS[ci++ % _STRUCT_COLORS.length] });
          }
        }
        if (structs.length) viewerStructures = structs;
        const aln = files.find((f) => f && /_structure_alignment\.fasta$/.test(f.name));
        if (aln) viewerAlignmentText = aln.text;
      } catch (e) {
        console.warn('struct_align viewer fetch failed', e);
      }
      viewerLoading = false;
    }
  }

  function closeViewer() {
    viewingRun = null;
    viewerAlignmentText = null;
    viewerStructures = null;
  }

  function statusColor(s: string): string {
    return s === 'done' ? '#5fa872'
      : s === 'failed' ? '#c93535'
      : s === 'running' ? '#e0b060'
      : 'var(--fg-dim)';
  }

  function relTime(iso: string): string {
    const d = new Date(iso).getTime();
    const diff = (Date.now() - d) / 1000;
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return new Date(iso).toLocaleDateString();
  }

  // Failed runs store the full traceback in `error`; show only the first line
  // inline (the actual message). The full traceback is in the Log console.
  function firstLine(s: string): string {
    const line = (s || '').split('\n').find((l) => l.trim()) ?? '';
    return line.length > 240 ? line.slice(0, 240) + '…' : line;
  }
</script>

<div class="tools-view">
  <div class="section-head">
    <h3 class="section-title">Tool runs · {runs.length}</h3>
    {#if isOwner}
      <button onclick={() => (pickerOpen = !pickerOpen)}>
        {pickerOpen ? 'Cancel' : '+ New run'}
      </button>
    {/if}
  </div>

  {#if pickerOpen && isOwner}
    <div class="picker">
      {#if !selectedTool}
        <p class="dim mono small">Pick a tool:</p>
        <div class="tool-grid">
          {#each tools as t}
            <button class="tool-card" onclick={() => selectTool(t)}>
              <div class="tool-name mono">{t.label}</div>
              <div class="tool-desc dim">{t.description}</div>
            </button>
          {/each}
        </div>
      {:else}
        <div class="picker-form">
          <div class="picker-head">
            <div>
              <span class="mono dim">tool:</span>
              <span class="accent">{selectedTool.label}</span>
            </div>
            <button class="mini" onclick={() => (selectedTool = null)}>Change</button>
          </div>

          <div class="field">
            <label>Label (optional)</label>
            <input type="text" bind:value={runLabel} placeholder="e.g. SF3B1 PCR check" />
          </div>

          {#if selectedTool.input_kind === 'fastq_upload' || selectedTool.input_kind === 'fasta_upload'}
            <div class="field">
              <label>{selectedTool.input_kind === 'fastq_upload' ? 'FASTQ file' : 'FASTA file'}</label>
              <input
                type="file"
                accept={selectedTool.input_kind === 'fastq_upload'
                  ? '.fastq,.fq,.fastq.gz,.fq.gz,.gz'
                  : '.fasta,.fa,.fna,.faa,.txt'}
                onchange={(e) => {
                  selectedFile = (e.target as HTMLInputElement).files?.[0] ?? null;
                }}
              />
              {#if selectedFile}
                <p class="mono dim small">
                  {selectedFile.name} · {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                </p>
              {/if}
            </div>

            {#if (project.species?.length ?? 0) > 0}
              <div class="field">
                <label>Link to species (optional)</label>
                <select bind:value={selectedSpeciesId}>
                  <option value={null}>— none —</option>
                  {#each (project.species ?? []) as s}
                    <option value={s.id}>{s.scientific_name}</option>
                  {/each}
                </select>
              </div>
            {/if}

          {:else if selectedTool.input_kind === 'multi_fasta'}
            <FastaMultiPicker
              {project}
              onchange={(s) => (multiSelected = s)}
            />
            {#if multiSelected.length > 0 && multiSelected.length < 2}
              <p class="error mono small">{selectedTool.label} needs at least 2 FASTA files.</p>
            {/if}

          {:else if selectedTool.input_kind === 'aligned_fasta'}
            <FastaMultiPicker
              {project}
              onchange={(s) => (multiSelected = s)}
            />
            {#if multiSelected.length === 0}
              <p class="dim mono small">
                Select 1 <strong>aligned</strong> FASTA. Run MAFFT first if you only have
                single-sequence files.
              </p>
            {:else if multiSelected.length > 1}
              <p class="error mono small">Select exactly 1 file.</p>
            {/if}

          {:else if selectedTool.input_kind === 'none'}
            <p class="dim mono small">No input file needed.</p>
          {/if}

          <ToolParamsForm
            params={selectedTool.params}
            values={paramValues}
            onchange={(v) => (paramValues = v)}
            projectSlug={project.slug}
          />

          {#if error}<p class="error mono">{error}</p>{/if}

          <div class="actions">
            <button onclick={() => (selectedTool = null)}>Back</button>
            <button class="primary" onclick={submit} disabled={submitting}>
              {submitting ? 'submitting…' : 'Run'}
            </button>
          </div>
        </div>
      {/if}
    </div>
  {/if}

  {#if loading}
    <p class="dim">Loading runs…</p>
  {:else if runs.length === 0}
    <p class="dim">No runs yet. Click "+ New run" to start.</p>
  {:else}
    {#if isOwner}
      <div class="bulk-bar">
        <label class="bulk-check mono small">
          <input
            type="checkbox"
            checked={selectedRunIds.size > 0 && selectedRunIds.size === runs.length}
            indeterminate={selectedRunIds.size > 0 && selectedRunIds.size < runs.length}
            onchange={(e) => {
              if ((e.target as HTMLInputElement).checked) selectAllVisibleRuns();
              else clearRunSelection();
            }}
          />
          {#if selectedRunIds.size === 0}
            select runs for bulk actions
          {:else}
            {selectedRunIds.size} selected
          {/if}
        </label>
        {#if selectedRunIds.size > 0}
          <div class="bulk-actions">
            {#if bulkDeleting}
              <span class="mono small dim">deleting {bulkProgress.done}/{bulkProgress.total}…</span>
            {:else}
              <button class="mini danger" onclick={bulkDeleteRuns}>Delete {selectedRunIds.size}</button>
              <button class="mini" onclick={clearRunSelection}>Clear</button>
            {/if}
          </div>
        {/if}
      </div>
    {/if}
    <ul class="run-list">
      {#each runs as run (run.id)}
        <li class="run-card" class:selected={selectedRunIds.has(run.id)}>
          <div class="run-head">
            {#if isOwner}
              <input
                type="checkbox"
                class="row-check"
                checked={selectedRunIds.has(run.id)}
                onchange={() => toggleRunSelected(run.id)}
                aria-label="select run"
              />
            {/if}
            <div class="run-info">
              <div class="run-name">
                <span class="tool-pill mono">{run.tool}</span>
                {#if renamingRunId === run.id}
                  <input
                    type="text"
                    class="rename-input mono"
                    bind:value={runRenameDraft}
                    onkeydown={(e) => {
                      if (e.key === 'Enter') commitRunRename(run);
                      else if (e.key === 'Escape') cancelRunRename();
                    }}
                    onblur={() => commitRunRename(run)}
                    use:focusOnMount
                  />
                {:else}
                  <span
                    class="run-label"
                    ondblclick={() => isOwner && startRunRename(run)}
                    title={isOwner ? 'Double-click to rename' : ''}
                  >{run.label ?? run.tool}</span>
                {/if}
              </div>
              <div class="run-meta mono dim">
                {relTime(run.created_at)}
                · <span style:color={statusColor(run.status)}>{run.status}</span>
                {#if run.status === 'running'}· {run.progress}%{/if}
                {#if run.result?.output_file_ids?.length}
                  · saved {run.result.output_file_ids.length} file{run.result.output_file_ids.length > 1 ? 's' : ''}
                {/if}
              </div>
            </div>
            <div class="run-actions">
              {#if run.status === 'done'}
                <button class="mini" onclick={() => openViewer(run)}>View</button>
              {/if}
              {#if (run.logs && run.logs.length) || run.status === 'running' || run.status === 'queued'}
                <button
                  class="mini"
                  class:active={openLogs.has(run.id)}
                  onclick={() => toggleLogs(run.id)}
                  title="Show the live run log"
                >{openLogs.has(run.id) ? 'Hide log' : 'Log'}</button>
              {/if}
              {#if isOwner && run.status === 'failed'}
                <button class="mini" onclick={() => retry(run)} title="Re-run identically">Retry</button>
                <button class="mini" onclick={() => editAndRetry(run)} title="Edit params and re-run">Edit…</button>
              {/if}
              {#if isOwner && (run.status === 'done' || run.status === 'failed')}
                <button class="mini danger" onclick={() => remove(run)}>×</button>
              {/if}
            </div>
          </div>

          {#if run.status === 'running' || run.status === 'queued'}
            <div class="progress-bar">
              <div class="progress-fill" style:width="{run.progress}%"></div>
            </div>
          {/if}

          {#if run.status === 'failed' && run.error}
            <p class="error mono small">{firstLine(run.error)}</p>
          {/if}

          {#if openLogs.has(run.id)}
            <LogConsole logs={run.logs ?? ''} status={run.status} />
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

{#if viewingRun}
  <div class="viewer-backdrop" onclick={closeViewer}>
    <div class="viewer-modal" onclick={(e) => e.stopPropagation()}>
      <div class="viewer-head">
        <div class="mono">{viewingRun.tool} · {viewingRun.label ?? ''}</div>
        <button onclick={closeViewer}>Close</button>
      </div>
      <div class="viewer-body">
        {#if viewingRun.tool === 'fastqc' && viewingRun.result}
          <FastqcReport result={viewingRun.result} />
        {:else if viewingRun.tool === 'mafft'}
          {#if viewerLoading}
            <p class="dim mono" style="padding: 2rem;">Loading alignment…</p>
          {:else if viewerAlignmentText}
            <AlignmentViewer fasta={viewerAlignmentText} height={650} />
          {:else}
            <p class="dim mono" style="padding: 2rem;">No alignment file found. Check the Files tab.</p>
          {/if}
        {:else if viewingRun.tool === 'struct_align'}
          {#if viewerLoading}
            <p class="dim mono" style="padding: 2rem;">Loading superposition…</p>
          {:else if viewerStructures && viewerStructures.length}
            <div class="struct-result">
              <SuperpositionViewer structures={viewerStructures} height={520} />
              {#if viewingRun.result?.pairwise?.length}
                <div class="pair-metrics mono">
                  <div class="pair-head dim">
                    pair · TM-score · RMSD (Å) · aligned · structβ-ID · engine
                  </div>
                  {#each viewingRun.result.pairwise as p}
                    <div class="pair-row">
                      {p.mobile} → {p.reference}
                      · {p.tm_score_ref ?? '—'}
                      · {p.rmsd?.toFixed?.(2) ?? p.rmsd}
                      · {p.aligned_length}
                      · {(p.structure_seq_identity * 100)?.toFixed?.(1) ?? '—'}%
                      · {p.engine}
                    </div>
                  {/each}
                </div>
              {/if}
              {#if viewerAlignmentText}
                <details open>
                  <summary class="mono dim">Structure-derived alignment</summary>
                  <AlignmentViewer fasta={viewerAlignmentText} height={400} />
                </details>
              {/if}
            </div>
          {:else}
            <p class="dim mono" style="padding: 2rem;">
              No superposed structures found. If this run used seq_guided mode or
              US-align isn't installed, check the run log and the Files tab.
            </p>
          {/if}
        {:else if viewingRun.tool === 'iqtree' && viewingRun.result?.newick}
          <div class="tree-result">
            <PhylogenyTree
              newick={viewingRun.result.newick}
              height={550}
              runLabel={viewingRun.label ?? viewingRun.tool}
            />
            <details>
              <summary class="mono dim">IQ-TREE report</summary>
              <pre class="report-text">{viewingRun.result.report}</pre>
            </details>
          </div>
        {:else if viewingRun.tool === 'popstats' && viewingRun.result}
          <PopStatsReport result={viewingRun.result} />
        {:else if viewingRun.tool === 'blast' && viewingRun.result}
          <BlastReport result={viewingRun.result} />
        {:else if viewingRun.tool === 'ncbi_fetch_assembly' && viewingRun.result}
          <div class="trim-result">
            <h4>Assembly downloaded</h4>
            <p class="mono">
              <strong>{viewingRun.result.accession}</strong>
              · {(viewingRun.result.size_bytes / 1e9).toFixed(2)} GB
            </p>
            <p class="mono dim small">
              Saved as project file — find it in the Files tab.
            </p>
            <pre class="stats-block">{JSON.stringify(viewingRun.result, null, 2)}</pre>
          </div>
        {:else if viewingRun.tool === 'region_extract' && viewingRun.result}
          <div class="trim-result">
            <h4>Region extracted</h4>
            <p class="mono">
              {viewingRun.result.seq_id}:{viewingRun.result.start}-{viewingRun.result.end}
              ({viewingRun.result.length} bp)
              {#if viewingRun.result.gene}· gene: <strong>{viewingRun.result.gene}</strong>{/if}
            </p>
            <p class="mono dim small">
              Extracted via {viewingRun.result.extractor}.
              Output FASTA saved to the Files tab.
            </p>
          </div>
        {:else if viewingRun.tool === 'alphafold_fetch' && viewingRun.result}
          <div class="trim-result">
            <h4>AlphaFold DB structure fetched</h4>
            <p class="mono">
              UniProt <strong>{viewingRun.result.uniprot_accession}</strong>
              {#if viewingRun.result.gene_symbol}· {viewingRun.result.gene_symbol}{/if}
            </p>
            {#if viewingRun.result.alphafold_meta}
              <p class="mono dim small">
                {viewingRun.result.alphafold_meta.organismScientificName ?? ''}
                · {viewingRun.result.alphafold_meta.uniprotDescription ?? ''}
                · model v{viewingRun.result.alphafold_meta.latestVersion ?? '?'}
              </p>
            {/if}
            <p class="mono dim small">
              PDB saved to project files — open it from the Files tab to view in 3D.
            </p>
          </div>
        {:else if (viewingRun.tool === 'annotate_gene' || viewingRun.tool === 'annotate_gene_miniprot') && viewingRun.result}
          <div class="trim-result">
            <h4>
              Gene annotation ·
              {viewingRun.result.tool_used ?? viewingRun.tool}
              {#if viewingRun.result.gene}· <strong>{viewingRun.result.gene}</strong>{/if}
            </h4>
            {#if viewingRun.result.per_genome_results}
              <p class="mono small">
                <strong>{viewingRun.result.n_successful}</strong> of
                {viewingRun.result.n_genomes_processed} genome{viewingRun.result.n_genomes_processed === 1 ? '' : 's'}
                annotated successfully
                {#if viewingRun.result.n_failed > 0}
                  · <span class="error-text">{viewingRun.result.n_failed} failed</span>
                {/if}
              </p>
              <table class="annot-table mono small">
                <thead>
                  <tr>
                    <th>Genome</th>
                    <th>Species</th>
                    <th>Status</th>
                    <th>CDS bp</th>
                    <th>Protein aa</th>
                    <th>Contig</th>
                    <th>N-term (first 30 aa)</th>
                  </tr>
                </thead>
                <tbody>
                  {#each viewingRun.result.per_genome_results as g}
                    <tr class:row-failed={g.status === 'failed'}>
                      <td>{g.genome ?? g.genome_file_id?.slice(0, 8) ?? '—'}</td>
                      <td>{g.species_code ?? '—'}</td>
                      <td>{g.status}</td>
                      <td>{g.cds_length ?? '—'}</td>
                      <td>{g.protein_length ?? '—'}</td>
                      <td>{g.contig ?? '—'}</td>
                      <td><code class="nterm">{g.protein_first_30aa ?? g.error ?? '—'}</code></td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {:else}
              <p class="mono dim small">No per-genome results in this run.</p>
            {/if}
            <p class="mono dim small">
              Files saved with prefix
              <code>{viewingRun.result.tool_used ?? 'annotate'}.&lt;SpCode&gt;_{viewingRun.result.gene ?? 'gene'}_&lt;kind&gt;</code>
              and linked to the corresponding species.
            </p>
          </div>
        {:else if viewingRun.tool === 'translate_dna' && viewingRun.result}
          <div class="trim-result">
            <h4>DNA → protein translation</h4>
            <p class="mono">
              Input: <strong>{viewingRun.result.input}</strong>
              ({viewingRun.result.input_mime})
            </p>
            <p class="mono">
              Strategy: <strong>{viewingRun.result.strategy_used}</strong>
              · {viewingRun.result.n_records} record(s)
              · {viewingRun.result.total_aa} aa total
            </p>
            {#if viewingRun.result.warnings && viewingRun.result.warnings.length > 0}
              <div class="mono small" style:color="#e0b060">
                {#each viewingRun.result.warnings as w}
                  <p>⚠ {w}</p>
                {/each}
              </div>
            {/if}
            <p class="mono dim small">Protein FASTA saved to project files.</p>
            {#if viewingRun.result.per_record}
              <details>
                <summary class="mono dim small">per-record breakdown</summary>
                <ul class="mono small" style:padding-left="1rem">
                  {#each viewingRun.result.per_record as r}
                    <li>{r.header.split(' ')[0]} — {r.length_aa} aa</li>
                  {/each}
                </ul>
              </details>
            {/if}
          </div>
        {:else if viewingRun.tool === 'colabfold' && viewingRun.result}
          <div class="trim-result">
            <h4>Structure predicted (ColabFold)</h4>
            <p class="mono">
              <strong>{viewingRun.result.name}</strong>
              · {viewingRun.result.n_residues} residues
            </p>
            {#if viewingRun.result.scores?.plddt_mean !== undefined}
              <p class="mono">
                Mean pLDDT: <strong>{viewingRun.result.scores.plddt_mean.toFixed(1)}</strong>
                (min {viewingRun.result.scores.plddt_min?.toFixed(0)},
                max {viewingRun.result.scores.plddt_max?.toFixed(0)})
                {#if viewingRun.result.scores.ptm !== undefined}
                  · pTM: {viewingRun.result.scores.ptm.toFixed(3)}
                {/if}
              </p>
              <p class="mono dim small">
                pLDDT &gt; 90 = very confident · 70-90 = confident · 50-70 = low ·
                &lt; 50 = very low (likely disordered)
              </p>
            {/if}
            <p class="mono dim small">
              PDB saved to project files — open it to view in 3D.
            </p>
            <details>
              <summary class="mono dim small">ColabFold log tail</summary>
              <pre class="stats-block">{viewingRun.result.stdout_tail}</pre>
            </details>
          </div>
        {:else if viewingRun.tool === 'hyphy_selection' && viewingRun.result}
          <div class="trim-result">
            <h4>HyPhy {viewingRun.result.method?.toUpperCase()} results</h4>
            <p class="mono">
              {viewingRun.result.n_sequences} sequences ·
              {viewingRun.result.n_codons} codons
            </p>
            {#if viewingRun.result.summary}
              <p class="mono">
                Under purifying selection: <strong>{viewingRun.result.summary.n_negative_selection}</strong> sites ·
                Under positive selection: <strong>{viewingRun.result.summary.n_positive_selection}</strong> sites ·
                Neutral: {viewingRun.result.summary.n_neutral}
                (p &lt; {viewingRun.result.summary.p_threshold})
              </p>
            {/if}
            <p class="mono dim small">
              Saved as .fel.json + .fel.tsv. To project ω onto a structure,
              go to the Conservation tab and select this run's JSON.
            </p>
          </div>
        {:else if viewingRun.tool === 'mhcxgraph' && viewingRun.result}
          <div class="trim-result">
            <h4>MHCXGraph structural conservation</h4>
            <p class="mono">
              Mode: <strong>{viewingRun.result.run_mode}</strong> ·
              {viewingRun.result.n_structures} structures
            </p>
            <p class="mono dim small">
              {(viewingRun.result.structures ?? []).map((s: any) => s.name).join(' · ')}
            </p>
            {#if viewingRun.result.summary?.frame_files}
              <p class="mono small">
                {viewingRun.result.summary.frame_files.length} frame file(s)
                · {viewingRun.result.summary.total_json_files} total JSON
              </p>
            {/if}
            <p class="mono dim small">
              tar.gz + summary saved. Use Conservation tab to highlight
              frame residues on a structure.
            </p>
            <details>
              <summary class="mono dim small">stdout tail</summary>
              <pre class="stats-block">{viewingRun.result.stdout_tail}</pre>
            </details>
          </div>
        {:else if viewingRun.tool === 'cutadapt' && viewingRun.result}
          <div class="trim-result">
            <h4>Cutadapt summary</h4>
            <p class="mono dim">{viewingRun.result.cmd}</p>
            <pre class="stats-block">{JSON.stringify(viewingRun.result.stats, null, 2)}</pre>
            {#if viewingRun.result.output_file_ids?.length}
              <p class="mono dim small">
                Trimmed FASTQ saved as project file — find it in the Files tab.
              </p>
            {/if}
          </div>
        {:else}
          <pre class="raw-result">{JSON.stringify(viewingRun.result, null, 2)}</pre>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
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

  .picker {
    padding: 1rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    margin-bottom: 1rem;
  }
  .tool-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
  .tool-card {
    text-align: left;
    padding: 0.7rem 0.8rem;
    background: var(--bg-base);
    border: 1px solid var(--border);
    color: var(--fg);
    cursor: pointer;
  }
  .tool-card:hover { border-color: var(--accent); }
  .tool-name { font-size: 0.95rem; color: var(--accent); margin-bottom: 0.3rem; }
  .tool-desc { font-size: 0.78rem; line-height: 1.4; }

  .picker-form { display: flex; flex-direction: column; gap: 0.7rem; }
  .picker-head {
    display: flex; justify-content: space-between; align-items: center;
    padding-bottom: 0.5rem; border-bottom: 1px solid var(--border);
  }
  .accent { color: var(--accent); }

  .field { display: flex; flex-direction: column; gap: 0.3rem; }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--fg-dim);
  }
  .field input, .field select {
    padding: 0.45rem 0.6rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-body);
    font-size: 0.9rem;
  }
  .field input:focus, .field select:focus {
    outline: none;
    border-color: var(--accent);
  }

  .actions { display: flex; gap: 0.5rem; justify-content: flex-end; }

  .run-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  .run-card {
    padding: 0.7rem 0.9rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
  }
  .run-card.selected {
    border-color: var(--accent);
    background: var(--accent-dim);
  }
  .row-check {
    flex-shrink: 0;
    margin: 0.3rem 0.3rem 0 0;
    cursor: pointer;
  }
  .bulk-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0.7rem;
    margin-bottom: 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
  }
  .bulk-check {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--fg-muted);
    cursor: pointer;
  }
  .bulk-actions { display: flex; gap: 0.3rem; align-items: center; }

  .run-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.5rem; }
  .run-info { min-width: 0; flex: 1; }
  .run-name {
    display: flex; align-items: center; gap: 0.5rem;
    font-size: 0.95rem;
  }
  .tool-pill {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    padding: 0.15rem 0.5rem;
    background: var(--accent-dim);
    color: var(--accent);
    border: 1px solid var(--accent-dim);
  }
  .run-label {
    font-family: var(--font-display);
    cursor: text;
    user-select: text;
  }
  .run-label:hover { color: var(--accent); }
  .rename-input {
    padding: 0.18rem 0.4rem;
    background: var(--bg-base);
    border: 1px solid var(--accent);
    color: var(--fg);
    font-size: 0.9rem;
    font-family: var(--font-display);
    min-width: 12rem;
  }
  .rename-input:focus { outline: none; }
  .run-meta { font-size: 0.7rem; margin-top: 0.2rem; }

  .run-actions { display: flex; gap: 0.3rem; }
  .mini {
    padding: 0.18rem 0.55rem;
    font-size: 0.72rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .struct-result { display: flex; flex-direction: column; gap: 0.8rem; padding: 0.5rem; }
  .pair-metrics { font-size: 0.72rem; border: 1px solid var(--border); border-radius: 3px; overflow: hidden; }
  .pair-head { padding: 0.35rem 0.6rem; background: var(--bg-inset); border-bottom: 1px solid var(--border); }
  .pair-row { padding: 0.3rem 0.6rem; border-bottom: 1px solid var(--border); }
  .pair-row:last-child { border-bottom: none; }
  .mini.active { color: var(--accent); border-color: var(--accent); background: var(--accent-dim); }
  .mini.danger:hover { color: #c93535; border-color: #c93535; }

  .progress-bar {
    margin-top: 0.5rem;
    height: 3px;
    background: var(--border);
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s;
  }

  .error { color: #c93535; margin: 0.5rem 0 0; }
  .small { font-size: 0.78rem; }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }

  .viewer-backdrop {
    position: fixed; inset: 0;
    background: rgba(0, 0, 0, 0.7);
    z-index: 50;
    display: grid; place-items: center;
    padding: 1rem;
  }
  .viewer-modal {
    width: 90vw; max-width: 1400px;
    height: 85vh;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    display: flex; flex-direction: column;
  }
  .viewer-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
  }
  .viewer-body { flex: 1 1 auto; min-height: 0; overflow: auto; }
  iframe { width: 100%; height: 100%; border: 0; background: white; }
  .raw-result {
    height: 100%; overflow: auto;
    padding: 1rem;
    font-family: var(--font-mono);
    font-size: 0.78rem;
  }
  .trim-result { padding: 1rem; overflow-y: auto; }
  .trim-result h4 { margin: 0 0 0.5rem; }
  .annot-table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.5rem 0;
    font-size: 0.72rem;
  }
  .annot-table th, .annot-table td {
    padding: 0.3rem 0.4rem;
    border-bottom: 1px solid var(--border);
    text-align: left;
    vertical-align: top;
  }
  .annot-table th {
    color: var(--fg-muted);
    font-weight: normal;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.6rem;
  }
  .annot-table tbody tr.row-failed { color: var(--fg-dim); }
  .annot-table tbody tr.row-failed td { color: #c93535; }
  .annot-table .nterm {
    font-size: 0.65rem;
    background: var(--bg-inset);
    padding: 0.1rem 0.3rem;
    word-break: break-all;
  }
  .error-text { color: #c93535; }
  .stats-block {
    background: var(--bg-inset);
    padding: 0.75rem;
    font-size: 0.78rem;
    font-family: var(--font-mono);
    overflow: auto;
    max-height: 400px;
  }
  .tree-result { padding: 0.5rem; overflow-y: auto; height: 100%; }
  .tree-result details {
    margin-top: 1rem;
    padding: 0 0.5rem;
  }
  .report-text {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    background: var(--bg-inset);
    padding: 0.75rem;
    overflow: auto;
    max-height: 300px;
  }
</style>
