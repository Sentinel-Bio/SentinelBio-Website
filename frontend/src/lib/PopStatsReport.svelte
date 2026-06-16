<script lang="ts">
  interface Props {
    result: {
      num_sequences?: number;
      alignment_length?: number;
      segregating_sites?: number | null;
      nucleotide_diversity_pi?: number | null;
      tajimas_d?: number | null;
      pairwise_distances?: Array<{
        a: string; b: string;
        p_distance: number;
        differences: number;
        comparable_sites: number;
      }>;
    };
  }
  let { result }: Props = $props();

  const pairs = $derived(result.pairwise_distances ?? []);

  function fmt(n: number | null | undefined, digits = 4): string {
    if (n === null || n === undefined || isNaN(n as number)) return '—';
    return (n as number).toFixed(digits);
  }
</script>

<div class="stats-report">
  <section class="stat-grid">
    <div class="stat-card">
      <div class="stat-label mono dim">N sequences</div>
      <div class="stat-value">{result.num_sequences ?? '—'}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label mono dim">Alignment length</div>
      <div class="stat-value">{result.alignment_length ?? '—'} bp</div>
    </div>
    <div class="stat-card">
      <div class="stat-label mono dim">Segregating sites (S)</div>
      <div class="stat-value">{result.segregating_sites ?? '—'}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label mono dim">Nucleotide diversity (π)</div>
      <div class="stat-value">{fmt(result.nucleotide_diversity_pi)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label mono dim">Tajima's D</div>
      <div class="stat-value">{fmt(result.tajimas_d, 3)}</div>
      <div class="stat-help dim mono">
        {#if result.tajimas_d == null}
          —
        {:else if Math.abs(result.tajimas_d) < 1}
          near neutral
        {:else if result.tajimas_d < -2}
          purifying selection / pop expansion
        {:else if result.tajimas_d > 2}
          balancing selection / structure
        {:else}
          mild deviation from neutrality
        {/if}
      </div>
    </div>
  </section>

  {#if pairs.length > 0}
    <section class="card">
      <h4 class="card-title">Pairwise p-distances</h4>
      <table class="pairs-table mono">
        <thead>
          <tr>
            <th>Sequence A</th>
            <th>Sequence B</th>
            <th>p-distance</th>
            <th>Differences</th>
            <th>Comparable sites</th>
          </tr>
        </thead>
        <tbody>
          {#each pairs as p}
            <tr>
              <td>{p.a}</td>
              <td>{p.b}</td>
              <td>{fmt(p.p_distance)}</td>
              <td>{p.differences}</td>
              <td>{p.comparable_sites}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </section>
  {/if}
</div>

<style>
  .stats-report { padding: 1rem; overflow-y: auto; }
  .stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.6rem;
    margin-bottom: 1rem;
  }
  .stat-card {
    padding: 0.75rem 0.9rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
  }
  .stat-label {
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    margin-bottom: 0.3rem;
  }
  .stat-value {
    font-family: var(--font-display);
    font-size: 1.4rem;
    color: var(--accent);
  }
  .stat-help { font-size: 0.7rem; margin-top: 0.3rem; }

  .card {
    background: var(--bg-raised);
    border: 1px solid var(--border);
    padding: 1rem 1.2rem;
  }
  .card-title {
    font-family: var(--font-display);
    font-size: 1rem;
    margin: 0 0 0.6rem;
  }
  .pairs-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
  }
  .pairs-table th, .pairs-table td {
    text-align: left;
    padding: 0.3rem 0.5rem;
    border-bottom: 1px solid var(--border);
  }
  .pairs-table th {
    color: var(--fg-dim);
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
</style>
