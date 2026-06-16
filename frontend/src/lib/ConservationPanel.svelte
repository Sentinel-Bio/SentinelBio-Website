<script lang="ts">
  /**
   * Panel for exploring a structure with per-residue conservation scores.
   *
   * Inputs (any subset can be omitted):
   *   - A PDB project file (the structure to display)
   *   - A HyPhy FEL result (per-codon ω + p-values)
   *   - An alignment FASTA (computes Shannon entropy per column on the fly)
   *   - An MHCXGraph summary JSON (qualitative frame membership)
   *
   * The user picks a coloring source via dropdown. Cancer hotspots can be
   * pasted as a comma-separated residue list (e.g. 700,666,625 for SF3B1).
   */
  import { onMount } from 'svelte';
  import {
    listProjectFiles, getProjectFileText,
    type Project, type ProjectFile,
  } from '$lib/api';
  import StructureViewerColored from '$lib/StructureViewerColored.svelte';

  interface Props {
    project: Project;
  }
  let { project }: Props = $props();

  let files = $state<ProjectFile[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // Selections
  let structureFileId = $state<string>('');
  let scoreSource = $state<'none' | 'plddt' | 'hyphy_fel' | 'entropy' | 'mhcxgraph'>('plddt');
  let hyphyFileId = $state<string>('');
  let alignmentFileId = $state<string>('');
  let mhcxgraphFileId = $state<string>('');

  let hotspotInput = $state<string>('');

  // Loaded artifacts
  let structureText = $state<string | null>(null);
  let structureFormat = $state<'pdb' | 'cif'>('pdb');
  let scores = $state<number[] | null>(null);
  let frameResidues = $state<number[] | null>(null);
  let highlightResidues = $derived(
    hotspotInput
      .split(/[,\s]+/)
      .map((s) => parseInt(s.trim()))
      .filter((n) => Number.isFinite(n) && n > 0)
  );

  onMount(async () => {
    try {
      files = await listProjectFiles(project.slug);
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
    loading = false;
  });

  const structureFiles = $derived(
    files.filter((f) => f.mime_hint === 'pdb' || f.mime_hint === 'cif')
  );
  const hyphyJsonFiles = $derived(
    files.filter((f) => f.name.endsWith('.fel.json'))
  );
  const fastaFiles = $derived(
    files.filter((f) => f.mime_hint === 'fasta')
  );
  const mhcxgraphSummaryFiles = $derived(
    files.filter((f) => f.name.endsWith('_summary.json'))
  );

  async function loadStructure() {
    if (!structureFileId) {
      structureText = null;
      return;
    }
    const file = files.find((f) => f.id === structureFileId);
    if (!file) return;
    try {
      const data = await getProjectFileText(project.slug, structureFileId);
      structureText = data.text;
      structureFormat = file.mime_hint === 'cif' ? 'cif' : 'pdb';
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
  }

  async function loadHyphyScores() {
    if (!hyphyFileId) {
      scores = null;
      return;
    }
    try {
      const data = await getProjectFileText(project.slug, hyphyFileId);
      const parsed = JSON.parse(data.text);
      const sites = parsed?.MLE?.content?.['0'] ?? [];
      if (!sites.length) {
        scores = null;
        return;
      }
      const headers: any[] = parsed.MLE?.headers ?? [];
      const colIndex = (name: string) => {
        for (let i = 0; i < headers.length; i++) {
          const label = Array.isArray(headers[i]) ? headers[i][0] : headers[i];
          if (label === name) return i;
        }
        return -1;
      };
      const iA = colIndex('alpha');
      const iB = colIndex('beta');
      const arr: number[] = [];
      for (const row of sites) {
        const alpha = parseFloat(row[iA] ?? 0);
        const beta = parseFloat(row[iB] ?? 0);
        // omega = beta/alpha, but clamp to a reasonable range for coloring
        const omega = alpha > 0 ? beta / alpha : (beta > 0 ? 5 : 0);
        arr.push(Math.min(omega, 5));  // cap at 5 for color scale
      }
      scores = arr;
    } catch (e) {
      error = e instanceof Error ? e.message : 'hyphy load failed';
      scores = null;
    }
  }

  async function loadEntropyScores() {
    if (!alignmentFileId) {
      scores = null;
      return;
    }
    try {
      const data = await getProjectFileText(project.slug, alignmentFileId);
      const seqs: string[] = [];
      let cur: string[] = [];
      for (const line of data.text.split('\n')) {
        if (line.startsWith('>')) {
          if (cur.length) seqs.push(cur.join(''));
          cur = [];
        } else {
          cur.push(line.trim());
        }
      }
      if (cur.length) seqs.push(cur.join(''));

      if (seqs.length < 2) {
        error = 'Need at least 2 sequences for entropy';
        return;
      }
      const L = seqs[0].length;
      if (!seqs.every((s) => s.length === L)) {
        error = 'Sequences have different lengths — not aligned?';
        return;
      }
      const arr: number[] = [];
      for (let i = 0; i < L; i++) {
        const counts = new Map<string, number>();
        let total = 0;
        for (const s of seqs) {
          const c = s[i];
          if (c === '-' || c === 'X') continue;
          counts.set(c, (counts.get(c) ?? 0) + 1);
          total++;
        }
        if (total === 0) {
          arr.push(0);
          continue;
        }
        let H = 0;
        for (const n of counts.values()) {
          const p = n / total;
          H -= p * Math.log2(p);
        }
        arr.push(H);
      }
      scores = arr;
    } catch (e) {
      error = e instanceof Error ? e.message : 'entropy compute failed';
    }
  }

  async function loadFrameResidues() {
    if (!mhcxgraphFileId) {
      frameResidues = null;
      return;
    }
    try {
      const data = await getProjectFileText(project.slug, mhcxgraphFileId);
      const parsed = JSON.parse(data.text);
      // Try to extract residue numbers from the summary. The exact path
      // depends on MHCXGraph version — we walk a few likely keys.
      const found = new Set<number>();
      const walk = (v: any) => {
        if (v == null) return;
        if (typeof v === 'number') return;
        if (typeof v === 'string') {
          // Strings like "A_42_GLU" — extract the middle number
          const m = v.match(/_(\d+)_/) ?? v.match(/^[A-Z]?(\d+)/);
          if (m) {
            const n = parseInt(m[1]);
            if (Number.isFinite(n)) found.add(n);
          }
          return;
        }
        if (Array.isArray(v)) {
          v.forEach(walk);
          return;
        }
        if (typeof v === 'object') {
          for (const k of Object.keys(v)) walk(v[k]);
        }
      };
      walk(parsed);
      frameResidues = Array.from(found).sort((a, b) => a - b);
    } catch (e) {
      error = e instanceof Error ? e.message : 'frame parse failed';
    }
  }

  // Reactive loaders
  $effect(() => {
    const _ = structureFileId;
    loadStructure();
  });
  $effect(() => {
    const _ = [scoreSource, hyphyFileId];
    if (scoreSource === 'hyphy_fel') loadHyphyScores();
  });
  $effect(() => {
    const _ = [scoreSource, alignmentFileId];
    if (scoreSource === 'entropy') loadEntropyScores();
  });
  $effect(() => {
    const _ = [scoreSource, mhcxgraphFileId];
    if (scoreSource === 'mhcxgraph') loadFrameResidues();
  });

  const coloringMode = $derived.by(() => {
    if (scoreSource === 'plddt') return 'plddt' as const;
    if (scoreSource === 'hyphy_fel') return 'omega' as const;
    if (scoreSource === 'entropy') return 'entropy' as const;
    if (scoreSource === 'mhcxgraph') return 'frame' as const;
    return 'none' as const;
  });
</script>

<div class="conservation-panel">
  <div class="section-head">
    <h3 class="section-title">Structural conservation viewer</h3>
  </div>

  <div class="controls">
    <div class="field">
      <label>Structure</label>
      <select bind:value={structureFileId}>
        <option value="">— pick a PDB/CIF —</option>
        {#each structureFiles as f}
          <option value={f.id}>{f.name}</option>
        {/each}
      </select>
    </div>

    <div class="field">
      <label>Color by</label>
      <select bind:value={scoreSource}>
        <option value="none">none (default)</option>
        <option value="plddt">pLDDT (AlphaFold confidence)</option>
        <option value="hyphy_fel">ω (dN/dS from HyPhy FEL)</option>
        <option value="entropy">Shannon entropy (alignment column variability)</option>
        <option value="mhcxgraph">MHCXGraph frame membership</option>
      </select>
    </div>

    {#if scoreSource === 'hyphy_fel'}
      <div class="field">
        <label>HyPhy FEL JSON</label>
        <select bind:value={hyphyFileId}>
          <option value="">— pick file —</option>
          {#each hyphyJsonFiles as f}
            <option value={f.id}>{f.name}</option>
          {/each}
        </select>
      </div>
    {/if}

    {#if scoreSource === 'entropy'}
      <div class="field">
        <label>Aligned FASTA</label>
        <select bind:value={alignmentFileId}>
          <option value="">— pick file —</option>
          {#each fastaFiles as f}
            <option value={f.id}>{f.name}</option>
          {/each}
        </select>
      </div>
    {/if}

    {#if scoreSource === 'mhcxgraph'}
      <div class="field">
        <label>MHCXGraph summary</label>
        <select bind:value={mhcxgraphFileId}>
          <option value="">— pick file —</option>
          {#each mhcxgraphSummaryFiles as f}
            <option value={f.id}>{f.name}</option>
          {/each}
        </select>
      </div>
    {/if}

    <div class="field">
      <label>Highlight residues (cancer hotspots)</label>
      <input
        type="text"
        bind:value={hotspotInput}
        placeholder="e.g. 700, 666, 625, 662 for SF3B1"
      />
    </div>
  </div>

  {#if error}<p class="error mono">{error}</p>{/if}

  {#if structureText}
    <StructureViewerColored
      structureData={structureText}
      format={structureFormat}
      coloringMode={coloringMode}
      {scores}
      {frameResidues}
      {highlightResidues}
      colorScale={scoreSource === 'plddt' ? 'plddt' : 'rd_bu'}
      height={620}
    />
  {:else if loading}
    <p class="dim">Loading project files…</p>
  {:else}
    <p class="dim">Pick a structure to begin.</p>
  {/if}
</div>

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
  .controls {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 0.6rem;
    padding: 0.7rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    margin-bottom: 0.7rem;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .field label {
    font-family: var(--font-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--fg-dim);
  }
  .field select, .field input {
    padding: 0.4rem 0.55rem;
    background: var(--bg-base);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.78rem;
  }
  .error { color: #c93535; font-size: 0.8rem; }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
</style>
