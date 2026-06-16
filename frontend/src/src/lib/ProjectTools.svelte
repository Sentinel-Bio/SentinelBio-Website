<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    listToolRegistry, listToolRuns, createToolRun, createToolRunWithUpload,
    deleteToolRun, getProjectFileText,
    type ToolDef, type ToolRun, type Project,
  } from '$lib/api';
  import ToolParamsForm from '$lib/ToolParamsForm.svelte';
  import FastaMultiPicker, { type SelectedFile } from '$lib/FastaMultiPicker.svelte';
  import AlignmentViewer from '$lib/AlignmentViewer.svelte';
  import PhylogenyTree from '$lib/charts/PhylogenyTree.svelte';
  import PopStatsReport from '$lib/PopStatsReport.svelte';
  import FastqcReport from '$lib/FastqcReport.svelte';
  import BlastReport from '$lib/BlastReport.svelte';

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
  let viewerLoading = $state(false);

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
      if (!runs.some((r) => r.status === 'queued' || r.status === 'running')) return;
      try {
        runs = await listToolRuns(project.slug);
      } catch {}
    }, 2500);
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

  async function openViewer(run: ToolRun) {
    viewingRun = run;
    viewerAlignmentText = null;

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
  }

  function closeViewer() {
    viewingRun = null;
    viewerAlignmentText = null;
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
    <ul class="run-list">
      {#each runs as run (run.id)}
        <li class="run-card">
          <div class="run-head">
            <div class="run-info">
              <div class="run-name">
                <span class="tool-pill mono">{run.tool}</span>
                <span class="run-label">{run.label ?? run.tool}</span>
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
            <p class="error mono small">{run.error}</p>
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
        {:else if viewingRun.tool === 'iqtree' && viewingRun.result?.newick}
          <div class="tree-result">
            <PhylogenyTree newick={viewingRun.result.newick} height={550} />
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
        {:else if viewingRun.tool === 'annotate_gene' && viewingRun.result}
          <div class="trim-result">
            <h4>Gene annotated via Exonerate</h4>
            <p class="mono">
              <strong>{viewingRun.result.gene}</strong>
              on contig {viewingRun.result.contig}
              ({viewingRun.result.region_start}–{viewingRun.result.region_end},
              strand {viewingRun.result.strand})
            </p>
            <p class="mono">
              CDS: {viewingRun.result.cds_length} bp · Protein: {viewingRun.result.protein_length} aa
              · BLAST hits: {viewingRun.result.n_hsps}
              · Mean identity: {viewingRun.result.mean_blast_pident?.toFixed(1)}%
            </p>
            <p class="mono dim small">
              CDS, protein FASTA, and GFF3 saved to project files.
            </p>
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
  .run-label { font-family: var(--font-display); }
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
