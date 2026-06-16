<script lang="ts">
  interface Hit {
    query: string;
    subject: string;
    bit_score: number;
    e_value: number;
    identity: number;
    align_len: number;
    percent_identity: number;
    q_start: number;
    q_end: number;
    sub_start: number;
    sub_end: number;
    sub_strand: '+' | '-';
  }
  interface Props {
    result: {
      mode: 'local' | 'remote';
      program: string;
      hits: Hit[];
      n_hits: number;
      best_hit: Hit | null;
      cmd: string;
      output_file_ids?: string[];
      subject_label?: string;
      query_label?: string;
      database?: string;
    };
  }
  let { result }: Props = $props();

  function fmtE(e: number): string {
    if (e === 0) return '0';
    if (e < 1e-4 || e > 1e4) return e.toExponential(1);
    return e.toFixed(3);
  }

  // Sort: bit score desc.
  const sorted = $derived(result.hits.slice().sort((a, b) => b.bit_score - a.bit_score));
</script>

<div class="blast-report">
  <div class="head">
    <div>
      <span class="mono dim">{result.program}</span>
      ·
      {#if result.mode === 'local'}
        <span class="mono">local · {result.subject_label}</span>
      {:else}
        <span class="mono">NCBI · {result.database}</span>
      {/if}
    </div>
    <div class="mono dim small">{result.n_hits} hit{result.n_hits === 1 ? '' : 's'}</div>
  </div>

  {#if result.output_file_ids && result.output_file_ids.length > 0}
    <p class="extracted mono small">
      Best-hit region saved as a new project FASTA — open the Files tab to view or pipe to MAFFT.
    </p>
  {/if}

  {#if sorted.length === 0}
    <p class="dim mono">No hits found at this E-value cutoff. Try loosening it.</p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Subject</th>
            <th>% ID</th>
            <th>Align</th>
            <th>Bit</th>
            <th>E</th>
            <th>Q start</th>
            <th>Q end</th>
            <th>S start</th>
            <th>S end</th>
            <th>Str</th>
          </tr>
        </thead>
        <tbody>
          {#each sorted as h, i}
            <tr class:best={i === 0}>
              <td class="mono subject-cell" title={h.subject}>{h.subject.split(' ').slice(0, 3).join(' ')}</td>
              <td class="mono">{h.percent_identity.toFixed(1)}</td>
              <td class="mono">{h.align_len}</td>
              <td class="mono">{h.bit_score.toFixed(0)}</td>
              <td class="mono">{fmtE(h.e_value)}</td>
              <td class="mono">{h.q_start}</td>
              <td class="mono">{h.q_end}</td>
              <td class="mono">{h.sub_start}</td>
              <td class="mono">{h.sub_end}</td>
              <td class="mono">{h.sub_strand}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <details class="cmd-block">
    <summary class="mono dim small">command</summary>
    <pre class="mono">{result.cmd}</pre>
  </details>
</div>

<style>
  .blast-report { padding: 0.7rem; overflow: auto; height: 100%; }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.5rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid var(--border);
  }
  .extracted {
    background: var(--accent-dim);
    border-left: 2px solid var(--accent);
    padding: 0.4rem 0.6rem;
    margin: 0.5rem 0;
    font-size: 0.78rem;
  }
  .table-wrap { overflow-x: auto; max-height: 60vh; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.75rem;
  }
  th, td {
    padding: 0.3rem 0.5rem;
    text-align: right;
    border-bottom: 1px dashed var(--border);
  }
  th {
    text-align: left;
    font-family: var(--font-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--fg-dim);
    position: sticky;
    top: 0;
    background: var(--bg-raised);
  }
  th:first-child, td:first-child { text-align: left; }
  tr.best { background: var(--accent-dim); }
  .subject-cell {
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cmd-block { margin-top: 1rem; }
  .cmd-block pre {
    background: var(--bg-inset);
    padding: 0.5rem;
    font-size: 0.7rem;
    overflow: auto;
    word-break: break-all;
  }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
  .small { font-size: 0.78rem; }
</style>
