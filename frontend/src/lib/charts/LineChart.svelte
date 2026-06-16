<script lang="ts">
  interface Module {
    headers: string[];
    rows: string[][];
  }
  interface Props {
    module: Module;
    xLabel: string;
    yLabel: string;
    colIdx: number; // which column holds the y value
    height?: number;
  }
  let { module, xLabel, yLabel, colIdx, height = 240 }: Props = $props();

  interface Point { x: number | string; xNumeric: number; y: number; }

  const points = $derived.by<Point[]>(() => {
    return module.rows.map((row, i) => {
      const xRaw = row[0];
      // X may be like "150" or "10-19" or "1". Pull the first number for plotting.
      const m = xRaw.match(/^(\d+(?:\.\d+)?)/);
      const xNum = m ? parseFloat(m[1]) : i;
      return {
        x: xRaw,
        xNumeric: xNum,
        y: parseFloat(row[colIdx]),
      };
    }).filter((p) => !isNaN(p.y));
  });

  const padding = { left: 60, right: 20, top: 20, bottom: 50 };
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

  const xMin = $derived(points.length ? Math.min(...points.map((p) => p.xNumeric)) : 0);
  const xMax = $derived(points.length ? Math.max(...points.map((p) => p.xNumeric)) : 1);
  const yMax = $derived(points.length ? Math.max(...points.map((p) => p.y), 0.01) : 1);

  function xScale(x: number): number {
    if (xMax === xMin) return padding.left + innerWidth / 2;
    return padding.left + ((x - xMin) / (xMax - xMin)) * innerWidth;
  }
  function yScale(y: number): number {
    return padding.top + innerHeight * (1 - y / yMax);
  }

  function niceTicks(min: number, max: number, count = 5): number[] {
    if (max <= min) return [min];
    const range = max - min;
    const step = Math.pow(10, Math.floor(Math.log10(range / count)));
    const niceStep = [1, 2, 5, 10].map((m) => m * step).find((s) => range / s <= count) ?? step * 10;
    const out: number[] = [];
    for (let v = Math.ceil(min / niceStep) * niceStep; v <= max; v += niceStep) out.push(v);
    return out;
  }

  const xTicks = $derived(niceTicks(xMin, xMax));
  const yTicks = $derived(niceTicks(0, yMax));

  const pathD = $derived(
    points.length === 0 ? '' :
    points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xScale(p.xNumeric)} ${yScale(p.y)}`).join(' ')
  );

  function fmtNum(n: number): string {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
    if (n >= 10) return n.toFixed(0);
    if (n >= 1) return n.toFixed(1);
    return n.toFixed(2);
  }
</script>

<div bind:this={containerEl} class="chart-wrap" style:height="{height}px">
  {#if containerWidth > 0 && points.length > 0}
    <svg width={containerWidth} {height}>
      <!-- Y grid + ticks -->
      {#each yTicks as t}
        <line x1={padding.left} x2={padding.left + innerWidth} y1={yScale(t)} y2={yScale(t)} class="grid" />
        <text x={padding.left - 6} y={yScale(t) + 4} text-anchor="end" class="tick">{fmtNum(t)}</text>
      {/each}

      <!-- X ticks -->
      {#each xTicks as t}
        <text x={xScale(t)} y={padding.top + innerHeight + 16} text-anchor="middle" class="tick">{fmtNum(t)}</text>
      {/each}

      <!-- Axis labels -->
      <text
        x={14}
        y={padding.top + innerHeight / 2}
        text-anchor="middle"
        class="axis-label"
        transform="rotate(-90, 14, {padding.top + innerHeight / 2})"
      >{yLabel}</text>
      <text
        x={padding.left + innerWidth / 2}
        y={height - 12}
        text-anchor="middle"
        class="axis-label"
      >{xLabel}</text>

      <!-- Line -->
      <path d={pathD} class="line" />

      <!-- Dots (only if not too many points) -->
      {#if points.length < 80}
        {#each points as p}
          <circle cx={xScale(p.xNumeric)} cy={yScale(p.y)} r="2" class="dot" />
        {/each}
      {/if}
    </svg>
  {/if}
</div>

<style>
  .chart-wrap { width: 100%; overflow: hidden; }
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
  .line {
    fill: none;
    stroke: var(--accent);
    stroke-width: 1.8;
  }
  .dot {
    fill: var(--accent);
  }
</style>
