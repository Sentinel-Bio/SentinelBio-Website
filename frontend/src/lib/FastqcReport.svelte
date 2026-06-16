<script lang="ts">
  import PerBaseQualityChart from '$lib/charts/PerBaseQualityChart.svelte';
  import LineChart from '$lib/charts/LineChart.svelte';
  interface Module {
    status: string;
    headers: string[];
    rows: string[][];
  }

  interface Props {
    result: {
      summary?: Array<{ status: string; module: string, phred_max: number }>;
      stats?: Record<string, string>;
      modules?: Record<string, Module>;
      report_html?: string;
    };
  }

  let { result }: Props = $props();

  const summary = $derived(result.summary ?? []);
  const stats = $derived(result.stats ?? {});
  const modules = $derived(result.modules ?? {});

  const perBaseQuality = $derived(modules['Per base sequence quality']);
  const seqLengthDist = $derived(modules['Sequence Length Distribution']);
  const seqDuplication = $derived(modules['Sequence Duplication Levels']);
  const perSeqGc = $derived(modules['Per sequence GC content']);
  const perBaseN = $derived(modules['Per base N content']);
  const adapterContent = $derived(modules['Adapter Content']);
  const overrepresented = $derived(modules['Overrepresented sequences']);

  function statusColor(s: string): string {
    return s === 'PASS' ? '#5fa872' : s === 'WARN' ? '#e0b060' : s === 'FAIL' ? '#c93535' : 'var(--fg-dim)';
  }

  function statusBg(s: string): string {
    if (s === 'PASS') return 'rgba(95, 168, 114, 0.12)';
    if (s === 'WARN') return 'rgba(224, 176, 96, 0.12)';
    if (s === 'FAIL') return 'rgba(201, 53, 53, 0.12)';
    return 'transparent';
  }

  let showRaw = $state(false);
</script>

<div class="report">
  <!-- Summary header -->
  <section class="summary-grid">
    {#each summary as s}
      <div class="summary-cell" style:background={statusBg(s.status)}>
        <div class="status-dot" style:background={statusColor(s.status)}></div>
        <div class="summary-label">{s.module}</div>
        <div class="summary-status mono" style:color={statusColor(s.status)}>{s.status}</div>
      </div>
    {/each}
  </section>

  <!-- Basic stats -->
  {#if Object.keys(stats).length > 0}
    <section class="card">
      <h4 class="card-title">Basic statistics</h4>
      <dl class="stats-grid">
        {#each Object.entries(stats) as [k, v]}
          <dt class="mono dim">{k}</dt>
          <dd>{v}</dd>
        {/each}
      </dl>
    </section>
  {/if}

  <!-- Per-base quality (the most important plot) -->
  {#if perBaseQuality}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Per-base sequence quality</h4>
        <span class="status-pill mono" style:color={statusColor(perBaseQuality.status)}>
          {perBaseQuality.status}
        </span>
      </div>
      <PerBaseQualityChart
          module={perBaseQuality}
          yMax={result.phred_max ?? 42}
        />
    </section>
  {/if}

  <!-- Per-sequence GC content -->
  {#if perSeqGc}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Per-sequence GC content</h4>
        <span class="status-pill mono" style:color={statusColor(perSeqGc.status)}>
          {perSeqGc.status}
        </span>
      </div>
      <LineChart module={perSeqGc} xLabel="Mean GC content (%)" yLabel="Read count" colIdx={1} />
    </section>
  {/if}

  <!-- Sequence length distribution -->
  {#if seqLengthDist}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Sequence length distribution</h4>
        <span class="status-pill mono" style:color={statusColor(seqLengthDist.status)}>
          {seqLengthDist.status}
        </span>
      </div>
      <LineChart module={seqLengthDist} xLabel="Length (bp)" yLabel="Read count" colIdx={1} />
    </section>
  {/if}

  <!-- Sequence duplication -->
  {#if seqDuplication}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Sequence duplication levels</h4>
        <span class="status-pill mono" style:color={statusColor(seqDuplication.status)}>
          {seqDuplication.status}
        </span>
      </div>
      <LineChart module={seqDuplication} xLabel="Duplication level" yLabel="% of reads" colIdx={1} />
    </section>
  {/if}

  <!-- Per-base N content -->
  {#if perBaseN}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Per-base N content</h4>
        <span class="status-pill mono" style:color={statusColor(perBaseN.status)}>
          {perBaseN.status}
        </span>
      </div>
      <LineChart module={perBaseN} xLabel="Position in read (bp)" yLabel="% N" colIdx={1} />
    </section>
  {/if}

  <!-- Adapter content -->
  {#if adapterContent && adapterContent.status !== 'PASS'}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Adapter content</h4>
        <span class="status-pill mono" style:color={statusColor(adapterContent.status)}>
          {adapterContent.status}
        </span>
      </div>
      <LineChart module={adapterContent} xLabel="Position in read (bp)" yLabel="% with adapter" colIdx={1} />
    </section>
  {/if}
  {#if overrepresented && overrepresented.rows.length > 0}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Overrepresented sequences</h4>
        <span class="status-pill mono" style:color={statusColor(overrepresented.status)}>
          {overrepresented.status}
        </span>
      </div>
      <table class="ovr-table mono">
        <thead>
          <tr>
            <th>Sequence</th>
            <th>Count</th>
            <th>%</th>
            <th>Possible source</th>
          </tr>
        </thead>
        <tbody>
          {#each overrepresented.rows.slice(0, 50) as row}
            <tr>
              <td class="seq-cell" title={row[0]}>{row[0]}</td>
              <td>{row[1]}</td>
              <td>{parseFloat(row[2]).toFixed(2)}%</td>
              <td>{row[3] ?? ''}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      {#if overrepresented.rows.length > 50}
        <p class="dim mono small">Showing 50 of {overrepresented.rows.length}. Open original report for full list.</p>
      {/if}
    </section>
  {/if}
  <!-- Fallback: official FastQC HTML (collapsed by default) -->
  {#if result.report_html}
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">Original FastQC report</h4>
        <button class="mini" onclick={() => (showRaw = !showRaw)}>
          {showRaw ? 'Hide' : 'Show'}
        </button>
      </div>
      {#if showRaw}
        <iframe srcdoc={result.report_html} title="FastQC report"></iframe>
      {/if}
    </section>
  {/if}
</div>
<style>
  .report {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    padding: 1rem;
    overflow-y: auto;
    max-height: 100%;
  }

  .summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 0.5rem;
  }
  .summary-cell {
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--border);
    display: grid;
    grid-template-columns: 8px 1fr auto;
    gap: 0.5rem;
    align-items: center;
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
  }
  .summary-label { font-size: 0.82rem; }
  .summary-status { font-size: 0.7rem; font-weight: 600; }

  .card {
    background: var(--bg-raised);
    border: 1px solid var(--border);
    padding: 1rem 1.2rem;
  }
  .card-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.6rem;
  }
  .card-title {
    font-family: var(--font-display);
    font-size: 1rem;
    margin: 0;
  }
  .status-pill {
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
  }

  .stats-grid {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.4rem 1rem;
    margin: 0;
    font-size: 0.85rem;
  }
  .stats-grid dt {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .stats-grid dd { margin: 0; font-family: var(--font-mono); }

  iframe {
    width: 100%;
    height: 600px;
    border: 1px solid var(--border);
    background: white;
  }

  .mini {
    padding: 0.18rem 0.55rem;
    font-size: 0.72rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
.ovr-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
  }
  .ovr-table th, .ovr-table td {
    text-align: left;
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid var(--border);
  }
  .ovr-table th {
    color: var(--fg-dim);
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .seq-cell {
    max-width: 360px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--accent);
  }
  .small { font-size: 0.72rem; }
</style>
