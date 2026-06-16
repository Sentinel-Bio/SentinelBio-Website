<script lang="ts">
  interface Module {
    headers: string[];
    rows: string[][];
  }
    interface Props {
    module: Module;
    height?: number;
    yMax?: number;
  }
  let { module, height = 280, yMax = 42 }: Props = $props();

  // FastQC per-base quality columns:
  // #Base  Mean  Median  LowerQuartile  UpperQuartile  10thPercentile  90thPercentile
  interface Point {
    label: string;
    mean: number;
    median: number;
    q1: number;
    q3: number;
    p10: number;
    p90: number;
  }

  const points = $derived.by<Point[]>(() => {
    return module.rows.map((row) => ({
      label: row[0],
      mean: parseFloat(row[1]),
      median: parseFloat(row[2]),
      q1: parseFloat(row[3]),
      q3: parseFloat(row[4]),
      p10: parseFloat(row[5]),
      p90: parseFloat(row[6]),
    })).filter((p) => !isNaN(p.mean));
  });

  const padding = { left: 50, right: 20, top: 20, bottom: 40 };
  let containerEl: HTMLDivElement;
  let containerWidth = $state(800);

  $effect(() => {
    if (!containerEl) return;
    const ro = new ResizeObserver(() => {
      containerWidth = containerEl.clientWidth;
    });
    ro.observe(containerEl);
    containerWidth = containerEl.clientWidth;
    return () => ro.disconnect();
  });

  const innerWidth = $derived(Math.max(200, containerWidth - padding.left - padding.right));
  const innerHeight = height - padding.top - padding.bottom;

  // Y axis: 0 to 42 (Phred score range)
  const yScale = (q: number) => padding.top + innerHeight * (1 - q / yMax);
  const xScale = (i: number) => padding.left + (i / Math.max(1, points.length - 1)) * innerWidth;

  const yTicks = [0, 10, 20, 28, 35, 42];
  // Background bands: red (0-20), yellow (20-28), green (28-42)
  const bandRed = $derived({ y1: yScale(0), y2: yScale(20) });
  const bandYellow = $derived({ y1: yScale(20), y2: yScale(28) });
  const bandGreen = $derived({ y1: yScale(28), y2: yScale(42) });

  const boxWidth = $derived(Math.max(2, Math.min(20, innerWidth / Math.max(1, points.length) * 0.7)));

  // Decide which x-labels to show
  const labelStride = $derived(Math.ceil(points.length / 12));
</script>

<div bind:this={containerEl} class="chart-wrap" style:height="{height}px">
  {#if containerWidth > 0 && points.length > 0}
    <svg width={containerWidth} {height}>
      <!-- Quality bands -->
      <rect x={padding.left} y={bandRed.y2} width={innerWidth} height={bandRed.y1 - bandRed.y2} fill="#c93535" opacity="0.08" />
      <rect x={padding.left} y={bandYellow.y2} width={innerWidth} height={bandYellow.y1 - bandYellow.y2} fill="#e0b060" opacity="0.08" />
      <rect x={padding.left} y={bandGreen.y2} width={innerWidth} height={bandGreen.y1 - bandGreen.y2} fill="#5fa872" opacity="0.08" />

      <!-- Y axis -->
      {#each yTicks as t}
        <line x1={padding.left} x2={padding.left + innerWidth} y1={yScale(t)} y2={yScale(t)} class="grid" />
        <text x={padding.left - 6} y={yScale(t) + 4} text-anchor="end" class="tick">{t}</text>
      {/each}
      <text
        x={12}
        y={padding.top + innerHeight / 2}
        text-anchor="middle"
        class="axis-label"
        transform="rotate(-90, 12, {padding.top + innerHeight / 2})"
      >Quality (Phred)</text>

      <!-- X axis labels -->
      {#each points as p, i}
        {#if i % labelStride === 0}
          <text x={xScale(i)} y={padding.top + innerHeight + 16} text-anchor="middle" class="tick">{p.label}</text>
        {/if}
      {/each}
      <text
        x={padding.left + innerWidth / 2}
        y={height - 8}
        text-anchor="middle"
        class="axis-label"
      >Position in read (bp)</text>

      <!-- Box plots -->
      {#each points as p, i}
        <!-- Whiskers (10th-90th) -->
        <line x1={xScale(i)} x2={xScale(i)} y1={yScale(p.p10)} y2={yScale(p.p90)} class="whisker" />
        <line x1={xScale(i) - boxWidth/3} x2={xScale(i) + boxWidth/3} y1={yScale(p.p10)} y2={yScale(p.p10)} class="whisker" />
        <line x1={xScale(i) - boxWidth/3} x2={xScale(i) + boxWidth/3} y1={yScale(p.p90)} y2={yScale(p.p90)} class="whisker" />
        <!-- Box (Q1-Q3) -->
        <rect
          x={xScale(i) - boxWidth/2}
          y={yScale(p.q3)}
          width={boxWidth}
          height={yScale(p.q1) - yScale(p.q3)}
          class="box"
        />
        <!-- Median -->
        <line
          x1={xScale(i) - boxWidth/2}
          x2={xScale(i) + boxWidth/2}
          y1={yScale(p.median)}
          y2={yScale(p.median)}
          class="median"
        />
      {/each}

      <!-- Mean line -->
      <polyline
        points={points.map((p, i) => `${xScale(i)},${yScale(p.mean)}`).join(' ')}
        class="mean-line"
      />
    </svg>
  {/if}
</div>

<style>
  .chart-wrap {
    width: 100%;
    overflow: hidden;
  }
  svg { display: block; }
  .grid { stroke: var(--border); stroke-width: 1; }
  .tick {
    fill: var(--fg-dim);
    font-family: var(--font-mono);
    font-size: 10px;
  }
  .axis-label {
    fill: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .whisker { stroke: var(--fg-muted); stroke-width: 1; }
  .box { fill: var(--accent); fill-opacity: 0.4; stroke: var(--accent); stroke-width: 1; }
  .median { stroke: var(--accent); stroke-width: 2; }
  .mean-line {
    fill: none;
    stroke: var(--accent);
    stroke-width: 1.5;
    stroke-dasharray: 3 3;
    opacity: 0.6;
  }
</style>
