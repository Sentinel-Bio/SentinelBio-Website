<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import { parseFasta, parseGenBank } from '$lib/genome/parsers';
  import type { Feature, Sequence } from '$lib/genome/types';

  interface Props {
    fasta?: string | null;
    genbank?: string | null;
    name?: string;
    height?: number;
  }

  let { fasta = null, genbank = null, name = 'sequence', height = 500 }: Props = $props();

  let containerEl: HTMLDivElement | undefined;
  let canvasWrapEl: HTMLDivElement | undefined;
  let canvasEl: HTMLCanvasElement | undefined;

  let sequences = $state<Sequence[]>([]);
  let features = $state<Feature[]>([]);
  let activeSeqIndex = $state(0);
  let parseError = $state<string | null>(null);

  let viewStart = $state(1);
  let viewEnd = $state(1);

  let canvasWidth = $state(0);
  let canvasHeight = $state(0);

  let searchInput = $state('');
  let posInput = $state('');

  let hoveredFeature = $state<{ feature: Feature; x: number; y: number } | null>(null);

  const RULER_H = 26;
  const FEATURE_TRACK_H = 16;
  const TRACK_GAP = 4;
  const SEQUENCE_H = 36;

  const MIN_VISIBLE_BP = 5;
  const BASE_LETTER_MIN_PX = 7;
  const ZOOM_SLIDER_MAX = 1000;

  const activeSeq = $derived(sequences[activeSeqIndex] ?? null);
  const visibleSpan = $derived(activeSeq ? Math.max(1, viewEnd - viewStart + 1) : 0);
  const pxPerBp = $derived(visibleSpan > 0 && canvasWidth > 0 ? canvasWidth / visibleSpan : 0);
  const baseLettersReadable = $derived(pxPerBp >= BASE_LETTER_MIN_PX);

  const zoomSliderValue = $derived(spanToZoomSliderValue(visibleSpan));

  const visibleSequence = $derived.by(() => {
    if (!activeSeq) return '';
    if (visibleSpan > 500) return '';
    return activeSeq.seq.slice(viewStart - 1, viewEnd).toUpperCase();
  });

  interface ThemeColors {
    bg: string;
    bgRaised: string;
    bgInset: string;
    fg: string;
    fgMuted: string;
    fgDim: string;
    accent: string;
    border: string;
    nucA: string;
    nucT: string;
    nucG: string;
    nucC: string;
    nucN: string;
    gene: string;
    cds: string;
    exon: string;
    mrna: string;
    rrna: string;
    trna: string;
    regulatory: string;
    misc: string;
  }

  let theme = $state<ThemeColors | null>(null);

  let themeObserver: MutationObserver | null = null;
  let resizeObs: ResizeObserver | null = null;
  let rafId: number | null = null;

  let featureBoxes: { feature: Feature; x: number; y: number; w: number; h: number }[] = [];
  let panOrigin: { x: number; start: number; end: number } | null = null;

  function readTheme(): ThemeColors {
    const cs = getComputedStyle(containerEl ?? document.body);
    const v = (key: string, fb: string) => cs.getPropertyValue(key).trim() || fb;

    return {
      bg: v('--bg-base', '#15121c'),
      bgRaised: v('--bg-raised', '#1e1a2a'),
      bgInset: v('--bg-inset', '#0f0d15'),
      fg: v('--fg', '#e8e3d3'),
      fgMuted: v('--fg-muted', '#a89f8c'),
      fgDim: v('--fg-dim', '#6b6479'),
      accent: v('--accent', '#b896e3'),
      border: v('--border', 'rgba(232, 227, 211, 0.12)'),

      nucA: '#5fb85f',
      nucT: '#d65454',
      nucG: '#3d7eb8',
      nucC: '#d49032',
      nucN: '#888888',

      gene: v('--accent', '#b896e3'),
      cds: '#5fa872',
      exon: '#4a82bf',
      mrna: '#c89348',
      rrna: '#a85842',
      trna: '#4faab2',
      regulatory: '#c66e9b',
      misc: '#8a7caf',
    };
  }

  function parseInputs(nextFasta: string | null, nextGenbank: string | null) {
    parseError = null;
    activeSeqIndex = 0;

    try {
      let nextSequences: Sequence[] = [];
      let nextFeatures: Feature[] = [];

      if (nextGenbank) {
        const parsed = parseGenBank(nextGenbank);
        nextSequences = [parsed.sequence];
        nextFeatures = parsed.features.sort((a, b) => a.start - b.start);
      } else if (nextFasta) {
        nextSequences = parseFasta(nextFasta);
      } else {
        parseError = 'No sequence data provided';
      }

      sequences = nextSequences;
      features = nextFeatures;

      if (nextSequences.length > 0) {
        viewStart = 1;
        viewEnd = nextSequences[0].length;
      } else {
        viewStart = 1;
        viewEnd = 1;
      }

      searchInput = '';
      posInput = '';
      requestRender();
    } catch (e) {
      parseError = e instanceof Error ? e.message : 'parse failed';
      sequences = [];
      features = [];
      viewStart = 1;
      viewEnd = 1;
      requestRender();
    }
  }

  $effect(() => {
    const nextFasta = fasta;
    const nextGenbank = genbank;

    untrack(() => {
      parseInputs(nextFasta, nextGenbank);
    });
  });

  $effect(() => {
    const _viewStart = viewStart;
    const _viewEnd = viewEnd;
    const _theme = theme;
    const _activeSeq = activeSeq;
    const _canvasWidth = canvasWidth;
    const _canvasHeight = canvasHeight;

    requestRender();
  });

  onMount(() => {
    theme = readTheme();

    themeObserver = new MutationObserver(() => {
      theme = readTheme();
      requestRender();
    });

    themeObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ['data-theme', 'class'],
    });

    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class'],
    });

    if (canvasWrapEl) {
      resizeObs = new ResizeObserver(() => sizeCanvas());
      resizeObs.observe(canvasWrapEl);
    }

    sizeCanvas();

    requestAnimationFrame(() => {
      sizeCanvas();
      requestAnimationFrame(sizeCanvas);
    });
  });

  onDestroy(() => {
    themeObserver?.disconnect();
    resizeObs?.disconnect();

    if (rafId !== null) {
      cancelAnimationFrame(rafId);
    }
  });

  function sizeCanvas() {
    if (!canvasEl || !canvasWrapEl) return;

    const w = Math.floor(canvasWrapEl.clientWidth);
    const h = Math.floor(canvasWrapEl.clientHeight);

    if (w <= 0 || h <= 0) return;

    const dpr = window.devicePixelRatio || 1;

    canvasEl.width = Math.floor(w * dpr);
    canvasEl.height = Math.floor(h * dpr);
    canvasEl.style.width = `${w}px`;
    canvasEl.style.height = `${h}px`;

    const ctx = canvasEl.getContext('2d');
    if (ctx) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    canvasWidth = w;
    canvasHeight = h;

    requestRender();
  }

  function requestRender() {
    if (rafId !== null) return;

    rafId = requestAnimationFrame(() => {
      rafId = null;
      render();
    });
  }

  function clampRange(start: number, end: number) {
    const len = activeSeq?.length ?? 1;

    let s = Math.floor(start);
    let e = Math.floor(end);

    if (!Number.isFinite(s)) s = 1;
    if (!Number.isFinite(e)) e = len;

    if (s > e) {
      [s, e] = [e, s];
    }

    s = Math.max(1, Math.min(len, s));
    e = Math.max(1, Math.min(len, e));

    if (e - s + 1 < MIN_VISIBLE_BP) {
      const center = Math.round((s + e) / 2);
      s = center - Math.floor(MIN_VISIBLE_BP / 2);
      e = s + MIN_VISIBLE_BP - 1;
    }

    if (s < 1) {
      e += 1 - s;
      s = 1;
    }

    if (e > len) {
      s -= e - len;
      e = len;
    }

    s = Math.max(1, s);
    e = Math.max(s, e);

    return {
      start: s,
      end: e,
      span: e - s + 1,
    };
  }

  function setViewRange(start: number, end: number) {
    if (!activeSeq) return;

    const next = clampRange(start, end);

    viewStart = next.start;
    viewEnd = next.end;

    requestRender();
  }

  function setViewAround(center: number, span: number) {
    const safeSpan = Math.max(MIN_VISIBLE_BP, Math.round(span));
    const start = Math.round(center - safeSpan / 2);

    setViewRange(start, start + safeSpan - 1);
  }

  function spanToZoomSliderValue(span: number): number {
    if (!activeSeq) return 0;

    const maxSpan = Math.max(MIN_VISIBLE_BP, activeSeq.length);
    const safeSpan = Math.max(MIN_VISIBLE_BP, Math.min(maxSpan, span || maxSpan));

    const denom = Math.log(maxSpan / MIN_VISIBLE_BP);
    if (!Number.isFinite(denom) || denom <= 0) return 0;

    const fraction = Math.log(maxSpan / safeSpan) / denom;
    return Math.round(Math.max(0, Math.min(1, fraction)) * ZOOM_SLIDER_MAX);
  }

  function zoomSliderValueToSpan(value: number): number {
    if (!activeSeq) return MIN_VISIBLE_BP;

    const maxSpan = Math.max(MIN_VISIBLE_BP, activeSeq.length);
    const fraction = Math.max(0, Math.min(1, value / ZOOM_SLIDER_MAX));

    const span = maxSpan / Math.pow(maxSpan / MIN_VISIBLE_BP, fraction);

    return Math.max(MIN_VISIBLE_BP, Math.min(maxSpan, Math.round(span)));
  }

  function zoomFromSlider(event: Event) {
    if (!activeSeq) return;

    const value = Number((event.currentTarget as HTMLInputElement).value);
    const nextSpan = zoomSliderValueToSpan(Number.isFinite(value) ? value : 0);

    setViewAround((viewStart + viewEnd) / 2, nextSpan);
  }

  function zoomIn() {
    if (!activeSeq) return;

    const nextSpan = Math.max(MIN_VISIBLE_BP, Math.floor(visibleSpan / 2));
    setViewAround((viewStart + viewEnd) / 2, nextSpan);
  }

  function zoomOut() {
    if (!activeSeq) return;

    const nextSpan = Math.min(activeSeq.length, Math.ceil(visibleSpan * 2));
    setViewAround((viewStart + viewEnd) / 2, nextSpan);
  }

  function zoomToBases() {
    if (!activeSeq) return;

    const desiredSpan = Math.max(MIN_VISIBLE_BP, Math.floor(Math.max(1, canvasWidth) / 16));
    setViewAround((viewStart + viewEnd) / 2, desiredSpan);
  }

  function zoomFull() {
    if (!activeSeq) return;

    setViewRange(1, activeSeq.length);
  }

  function parseCoordinateText(rawValue: string) {
    const raw = rawValue.trim();
    const normalized = raw.replace(/[,_\s]/g, '');

    const rangeMatch = normalized.match(/^(\d+)(?:\.\.|-|:)(\d+)$/);
    if (rangeMatch) {
      return {
        type: 'range' as const,
        start: Number(rangeMatch[1]),
        end: Number(rangeMatch[2]),
      };
    }

    const singleMatch = normalized.match(/^\d+$/);
    if (singleMatch) {
      return {
        type: 'single' as const,
        position: Number(normalized),
      };
    }

    return {
      type: 'invalid' as const,
    };
  }

  function gotoPosition() {
    if (!activeSeq) return;

    const parsed = parseCoordinateText(posInput);

    if (parsed.type === 'range') {
      setViewRange(parsed.start, parsed.end);
      return;
    }

    if (parsed.type === 'single') {
      const span = Math.min(activeSeq.length, Math.max(80, visibleSpan || 80));
      setViewAround(parsed.position, span);
    }
  }

  function searchFeature() {
    const value = searchInput.trim().toLowerCase();

    if (!value || !activeSeq) return;

    const match = features.find(
      (feature) =>
        feature.name.toLowerCase().includes(value) ||
        Object.values(feature.qualifiers).some((qualifier) =>
          String(qualifier).toLowerCase().includes(value),
        ),
    );

    if (!match) return;

    const featureSpan = Math.max(40, match.end - match.start + 1);
    setViewAround((match.start + match.end) / 2, Math.min(activeSeq.length, featureSpan * 3));
  }

  function onWheel(event: WheelEvent) {
    event.preventDefault();

    if (!activeSeq || !canvasEl) return;

    const rect = canvasEl.getBoundingClientRect();
    const mouseX = event.clientX - rect.left;

    const anchorBp = xToBp(mouseX);
    const factor = event.deltaY > 0 ? 1.35 : 0.65;
    const nextSpan = Math.max(
      MIN_VISIBLE_BP,
      Math.min(activeSeq.length, Math.round(visibleSpan * factor)),
    );

    const leftFraction = visibleSpan > 0 ? (anchorBp - viewStart) / visibleSpan : 0.5;
    const nextStart = Math.round(anchorBp - leftFraction * nextSpan);

    setViewRange(nextStart, nextStart + nextSpan - 1);
  }

  function onMouseDown(event: MouseEvent) {
    if (event.button !== 0) return;

    event.preventDefault();

    panOrigin = {
      x: event.clientX,
      start: viewStart,
      end: viewEnd,
    };

    const onMove = (moveEvent: MouseEvent) => {
      if (!panOrigin || !activeSeq || canvasWidth <= 0) return;

      const dx = moveEvent.clientX - panOrigin.x;
      const bpShift = -Math.round((dx / canvasWidth) * (panOrigin.end - panOrigin.start + 1));

      setViewRange(panOrigin.start + bpShift, panOrigin.end + bpShift);
    };

    const onUp = () => {
      panOrigin = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  function onMouseMove(event: MouseEvent) {
    if (!canvasEl || panOrigin) return;

    const rect = canvasEl.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;

    const found = featureBoxes.find(
      (box) => mx >= box.x && mx <= box.x + box.w && my >= box.y && my <= box.y + box.h,
    );

    hoveredFeature = found ? { feature: found.feature, x: mx, y: my } : null;
  }

  function onMouseLeave() {
    hoveredFeature = null;
  }

  function bpToX(bp: number): number {
    if (visibleSpan <= 0 || canvasWidth <= 0) return 0;

    return ((bp - viewStart) / visibleSpan) * canvasWidth;
  }

  function xToBp(x: number): number {
    if (visibleSpan <= 0 || canvasWidth <= 0) return viewStart;

    return Math.floor(viewStart + (x / canvasWidth) * visibleSpan);
  }

  function colorForBase(base: string): string {
    if (!theme) return '#888888';

    switch (base.toLowerCase()) {
      case 'a':
        return theme.nucA;
      case 't':
        return theme.nucT;
      case 'g':
        return theme.nucG;
      case 'c':
        return theme.nucC;
      default:
        return theme.nucN;
    }
  }

  function colorForType(type: string): string {
    if (!theme) return '#888888';

    switch (type.toLowerCase()) {
      case 'gene':
        return theme.gene;
      case 'cds':
        return theme.cds;
      case 'exon':
        return theme.exon;
      case 'mrna':
        return theme.mrna;
      case 'rrna':
        return theme.rrna;
      case 'trna':
        return theme.trna;
      case 'regulatory':
        return theme.regulatory;
      default:
        return theme.misc;
    }
  }

  function formatBp(n: number): string {
    if (n >= 1_000_000) {
      return `${(n / 1_000_000).toFixed(2).replace(/\.?0+$/, '')} Mb`;
    }

    if (n >= 1_000) {
      return `${(n / 1_000).toFixed(1).replace(/\.?0+$/, '')} kb`;
    }

    return n.toLocaleString();
  }

  const featureTracks = $derived.by(() => {
    const preferred = ['gene', 'mRNA', 'CDS', 'exon', 'rRNA', 'tRNA', 'regulatory'];
    const present = Array.from(new Set(features.map((feature) => feature.type)));
    const ordered = preferred.filter((type) => present.includes(type));

    return [...ordered, ...present.filter((type) => !ordered.includes(type))];
  });

  function featureTrackHeight(): number {
    return featureTracks.length * (FEATURE_TRACK_H + 2);
  }

  function render() {
    if (!canvasEl || !theme || !activeSeq || canvasWidth <= 0 || canvasHeight <= 0) return;

    const ctx = canvasEl.getContext('2d');
    if (!ctx) return;

    ctx.fillStyle = theme.bg;
    ctx.fillRect(0, 0, canvasWidth, canvasHeight);

    let y = 0;

    drawRuler(ctx, y);
    y += RULER_H + TRACK_GAP;

    if (features.length > 0) {
      drawFeatures(ctx, y);
      y += featureTrackHeight() + TRACK_GAP;
    } else {
      featureBoxes = [];
    }

    drawSequence(ctx, y);
  }

  function drawRuler(ctx: CanvasRenderingContext2D, y: number) {
    if (!theme) return;

    ctx.fillStyle = theme.bgInset;
    ctx.fillRect(0, y, canvasWidth, RULER_H);

    const targetTicks = Math.max(4, Math.floor(canvasWidth / 110));
    const rough = Math.max(1, visibleSpan / targetTicks);
    const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
    const candidates = [1, 2, 5, 10].map((m) => m * magnitude);
    const step = candidates.find((candidate) => candidate >= rough) ?? magnitude * 10;
    const firstTick = Math.ceil(viewStart / step) * step;

    ctx.strokeStyle = theme.fgDim;
    ctx.fillStyle = theme.fgMuted;
    ctx.font = '11px var(--font-mono, monospace)';
    ctx.textBaseline = 'middle';

    for (let bp = firstTick; bp <= viewEnd; bp += step) {
      const x = bpToX(bp);

      ctx.beginPath();
      ctx.moveTo(x, y + RULER_H - 7);
      ctx.lineTo(x, y + RULER_H);
      ctx.stroke();

      ctx.fillText(formatBp(bp), x + 4, y + 9);
    }

    ctx.strokeStyle = theme.border;
    ctx.beginPath();
    ctx.moveTo(0, y + RULER_H);
    ctx.lineTo(canvasWidth, y + RULER_H);
    ctx.stroke();
  }

  function drawFeatures(ctx: CanvasRenderingContext2D, baseY: number) {
    if (!theme) return;

    featureBoxes = [];

    const visibleFeatures = features.filter(
      (feature) => feature.end >= viewStart && feature.start <= viewEnd,
    );

    for (let trackIdx = 0; trackIdx < featureTracks.length; trackIdx++) {
      const trackType = featureTracks[trackIdx];
      const y = baseY + trackIdx * (FEATURE_TRACK_H + 2);

      ctx.fillStyle = trackIdx % 2 === 0 ? theme.bgInset : theme.bgRaised;
      ctx.fillRect(0, y, canvasWidth, FEATURE_TRACK_H);

      ctx.fillStyle = theme.fgDim;
      ctx.font = '9px var(--font-mono, monospace)';
      ctx.textBaseline = 'middle';
      ctx.fillText(trackType, 4, y + FEATURE_TRACK_H / 2);

      for (const feature of visibleFeatures) {
        if (feature.type !== trackType) continue;

        const color = colorForType(feature.type);

        for (const segment of feature.segments) {
          const x1 = bpToX(segment.start);
          const x2 = bpToX(segment.end + 1);
          const drawX = Math.max(0, x1);
          const drawW = Math.min(
            canvasWidth - drawX,
            Math.max(1, x2 - x1 - Math.max(0, drawX - x1)),
          );

          if (drawW <= 0) continue;

          ctx.fillStyle = color;
          ctx.fillRect(drawX, y + 2, drawW, FEATURE_TRACK_H - 4);

          featureBoxes.push({
            feature,
            x: drawX,
            y: y + 2,
            w: drawW,
            h: FEATURE_TRACK_H - 4,
          });

          if (drawW > 35) {
            ctx.save();
            ctx.beginPath();
            ctx.rect(drawX, y, drawW, FEATURE_TRACK_H);
            ctx.clip();

            ctx.fillStyle = theme.fg;
            ctx.font = '10px var(--font-mono, monospace)';
            ctx.textBaseline = 'middle';
            ctx.fillText(feature.name, drawX + 5, y + FEATURE_TRACK_H / 2);

            ctx.restore();
          }
        }
      }
    }
  }

  function drawSequence(ctx: CanvasRenderingContext2D, y: number) {
    if (!theme || !activeSeq) return;

    const seq = activeSeq.seq;
    const rowH = SEQUENCE_H;

    ctx.fillStyle = theme.bgInset;
    ctx.fillRect(0, y, canvasWidth, rowH);

    if (visibleSpan > 20000) {
      ctx.fillStyle = theme.fgDim;
      ctx.font = '11px var(--font-mono, monospace)';
      ctx.textBaseline = 'middle';
      ctx.fillText('sequence hidden at this zoom level', 8, y + rowH / 2);
      return;
    }

    const startIdx = Math.max(0, viewStart - 1);
    const endIdx = Math.min(seq.length, viewEnd);

    if (!baseLettersReadable) {
      for (let idx = startIdx; idx < endIdx; idx++) {
        const x1 = bpToX(idx + 1);
        const x2 = bpToX(idx + 2);

        ctx.fillStyle = colorForBase(seq[idx] ?? 'n');
        ctx.fillRect(x1, y + 4, Math.max(1, x2 - x1 + 1), rowH - 8);
      }

      return;
    }

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = `${Math.min(22, Math.max(11, Math.floor(pxPerBp * 0.82)))}px var(--font-mono, monospace)`;

    for (let idx = startIdx; idx < endIdx; idx++) {
      const base = seq[idx] ?? 'n';
      const centerX = bpToX(idx + 1.5);

      ctx.fillStyle = colorForBase(base);
      ctx.fillText(base.toUpperCase(), centerX, y + rowH / 2);
    }

    ctx.textAlign = 'start';
  }
</script>

<div class="genome-viewer" bind:this={containerEl} style={`height: ${height}px;`}>
  <div class="toolbar">
    <div class="toolbar-left">
      <span class="seq-name mono" title={activeSeq?.description ?? name}>
        {activeSeq?.id ?? name}
      </span>

      <span class="seq-len mono dim">
        {activeSeq ? `${activeSeq.length.toLocaleString()} bp` : ''}
      </span>
    </div>

    <div class="toolbar-right">
      <input
        bind:value={searchInput}
        type="text"
        class="search-input mono"
        placeholder="search feature"
        onkeydown={(event) => {
          if (event.key === 'Enter') searchFeature();
        }}
      />

      <button type="button" class="zoom-btn small" onclick={searchFeature}>
        go
      </button>

      <input
        bind:value={posInput}
        type="text"
        class="pos-input mono"
        placeholder="2430..2520"
        onkeydown={(event) => {
          if (event.key === 'Enter') gotoPosition();
        }}
      />

      <button type="button" class="zoom-btn small" onclick={gotoPosition}>
        go
      </button>

      <label class="zoom-slider mono" title="Zoom">
        <span>zoom</span>

        <input
          type="range"
          min="0"
          max={ZOOM_SLIDER_MAX}
          step="1"
          value={zoomSliderValue}
          oninput={zoomFromSlider}
        />
      </label>

      <button type="button" class="zoom-btn" title="Zoom out" onclick={zoomOut}>
        −
      </button>

      <button type="button" class="zoom-btn" title="Zoom in" onclick={zoomIn}>
        +
      </button>

      <button type="button" class="zoom-btn bases" title="Zoom until bases are readable" onclick={zoomToBases}>
        bases
      </button>

      <button type="button" class="zoom-btn" title="Fit all" onclick={zoomFull}>
        ⤢
      </button>
    </div>
  </div>

  <div class="state-row mono">
    <span>view {viewStart.toLocaleString()}..{viewEnd.toLocaleString()}</span>
    <span>span {visibleSpan.toLocaleString()} bp</span>
    <span>px per bp {pxPerBp.toFixed(3)}</span>
    <span>letters {baseLettersReadable ? 'YES' : 'NO'}</span>
    <span>zoom {zoomSliderValue}</span>
  </div>

  <div class="canvas-wrap" bind:this={canvasWrapEl}>
    <canvas
      bind:this={canvasEl}
      onwheel={onWheel}
      onmousedown={onMouseDown}
      onmousemove={onMouseMove}
      onmouseleave={onMouseLeave}
    ></canvas>

    {#if parseError}
      <div class="overlay msg error mono">{parseError}</div>
    {:else if !activeSeq}
      <div class="overlay msg dim mono">Loading sequence</div>
    {/if}

    {#if hoveredFeature}
      <div
        class="tooltip"
        style:left={`${Math.min(hoveredFeature.x + 12, Math.max(0, canvasWidth - 260))}px`}
        style:top={`${hoveredFeature.y + 12}px`}
      >
        <div class="tip-name">{hoveredFeature.feature.name}</div>

        <div class="tip-meta mono dim">
          {hoveredFeature.feature.type} · {hoveredFeature.feature.start.toLocaleString()}..{hoveredFeature.feature.end.toLocaleString()}
          {hoveredFeature.feature.complement ? ' · complement' : ''}
        </div>

        {#if hoveredFeature.feature.qualifiers.product}
          <div class="tip-product">{hoveredFeature.feature.qualifiers.product}</div>
        {/if}
      </div>
    {/if}
  </div>

  {#if visibleSequence}
    <div class="base-strip mono" aria-label="visible DNA sequence">
      <span class="base-pos dim">{viewStart.toLocaleString()}</span>

      <div class="base-letters">
        {#each visibleSequence.split('') as base, i}
          <span class={`base base-${base.toLowerCase()}`} title={`${viewStart + i}: ${base}`}>
            {base}
          </span>
        {/each}
      </div>

      <span class="base-pos dim">{viewEnd.toLocaleString()}</span>
    </div>
  {/if}
</div>

<style>
  .genome-viewer {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-height: 320px;
    background: var(--bg-base);
    border: 1px solid var(--border);
    overflow: hidden;
    user-select: none;
  }

  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.55rem 0.75rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
    gap: 0.6rem;
    flex-wrap: wrap;
    flex-shrink: 0;
  }

  .toolbar-left,
  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    flex-wrap: wrap;
  }

  .toolbar-right {
    justify-content: flex-end;
  }

  .seq-name {
    color: var(--accent);
    font-size: 0.86rem;
    font-weight: 500;
  }

  .seq-len {
    font-size: 0.75rem;
  }

  .search-input,
  .pos-input {
    padding: 0.34rem 0.55rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-size: 0.78rem;
    font-family: var(--font-mono);
  }

  .search-input {
    width: 8rem;
  }

  .pos-input {
    width: 8rem;
  }

  .search-input:focus,
  .pos-input:focus {
    outline: none;
    border-color: var(--accent);
  }

  .zoom-slider {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    color: var(--fg-dim);
    font-size: 0.64rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }

  .zoom-slider input {
    width: 12rem;
    accent-color: var(--accent);
  }

  .zoom-btn {
    min-width: 30px;
    height: 30px;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
    display: grid;
    place-items: center;
    font-size: 0.9rem;
    padding: 0 0.4rem;
  }

  .zoom-btn:hover,
  .zoom-btn:focus {
    color: var(--accent);
    border-color: var(--accent);
    outline: none;
  }

  .zoom-btn.small {
    min-width: 34px;
    font-size: 0.62rem;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .zoom-btn.bases {
    width: auto;
    font-size: 0.62rem;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .state-row {
    display: flex;
    gap: 0.75rem;
    flex-wrap: wrap;
    padding: 0.35rem 0.75rem;
    background: var(--bg-inset);
    border-bottom: 1px solid var(--border);
    color: var(--fg-muted);
    font-size: 0.68rem;
    flex-shrink: 0;
  }

  .canvas-wrap {
    position: relative;
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
    cursor: grab;
    touch-action: none;
  }

  canvas:active {
    cursor: grabbing;
  }

  .overlay {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    pointer-events: none;
  }

  .tooltip {
    position: absolute;
    z-index: 10;
    min-width: 180px;
    max-width: 260px;
    padding: 0.5rem 0.7rem;
    background: var(--bg-raised);
    border: 1px solid var(--accent);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    pointer-events: none;
  }

  .tip-name {
    font-family: var(--font-display);
    color: var(--fg);
    font-size: 0.95rem;
    margin-bottom: 0.2rem;
  }

  .tip-meta {
    font-size: 0.7rem;
  }

  .tip-product {
    margin-top: 0.3rem;
    font-size: 0.78rem;
    line-height: 1.4;
    color: var(--fg-muted);
  }

  .base-strip {
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 0.75rem;
    align-items: start;
    padding: 0.45rem 0.75rem;
    background: var(--bg-raised);
    border-top: 1px solid var(--border);
    flex-shrink: 0;
    user-select: text;
  }

  .base-pos {
    font-size: 0.65rem;
    padding-top: 0.1rem;
  }

  .base-letters {
    display: flex;
    flex-wrap: wrap;
    gap: 0.06rem;
    line-height: 1.25;
  }

  .base {
    min-width: 0.85em;
    text-align: center;
    font-size: 0.78rem;
    font-weight: 700;
  }

  .base-a {
    color: #5fb85f;
  }

  .base-t {
    color: #d65454;
  }

  .base-g {
    color: #3d7eb8;
  }

  .base-c {
    color: #d49032;
  }

  .msg {
    padding: 2rem;
    text-align: center;
  }

  .error {
    color: #c93535;
  }

  .dim {
    color: var(--fg-dim);
  }

  .mono {
    font-family: var(--font-mono);
  }
</style>
