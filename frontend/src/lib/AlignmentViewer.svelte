<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  interface Props {
    /** Aligned FASTA text. */
    fasta: string;
    height?: number;
  }
  let { fasta, height = 480 }: Props = $props();

  interface AlignmentSeq {
    id: string;
    seq: string;
  }

  function parseAligned(text: string): AlignmentSeq[] {
    const out: AlignmentSeq[] = [];
    let cur: AlignmentSeq | null = null;
    for (const line of text.split('\n')) {
      if (line.startsWith('>')) {
        if (cur) out.push(cur);
        cur = { id: line.slice(1).trim().split(/\s+/)[0] || 'seq', seq: '' };
      } else if (cur) {
        cur.seq += line.replace(/\s+/g, '');
      }
    }
    if (cur) out.push(cur);
    return out;
  }

  const seqs = $derived(parseAligned(fasta));
  const alnLength = $derived(seqs.length > 0 ? seqs[0].seq.length : 0);

  // ClustalX-derived AA color groups, modulated by per-column conservation.
  // The classic ClustalX scheme is the de facto standard for biologists; it's
  // built on physicochemical similarity (the substance behind PAM/BLOSUM).
  const AA_GROUPS: Array<{ residues: string; color: string }> = [
    { residues: 'AILMFWV', color: '74, 130, 191' },   // hydrophobic — blue
    { residues: 'KR',      color: '184, 100, 100' },  // basic — red
    { residues: 'ED',      color: '184, 80, 220' },   // acidic — magenta
    { residues: 'NQST',    color: '95, 168, 114' },   // polar — green
    { residues: 'C',       color: '230, 100, 130' },  // cysteine — pink
    { residues: 'G',       color: '212, 144, 50' },   // glycine — orange
    { residues: 'P',       color: '212, 200, 50' },   // proline — yellow
    { residues: 'HY',      color: '74, 170, 178' },   // aromatic — teal
  ];

  function aaColor(ch: string): string | null {
    for (const g of AA_GROUPS) {
      if (g.residues.includes(ch)) return g.color;
    }
    return null;
  }

  function colorFor(ch: string, mode: 'dna' | 'aa', col: number): string {
    if (ch === '-' || ch === '.') return 'transparent';
    if (mode === 'dna') {
      const c = ch.toLowerCase();
      const alpha = 0.2 + (conservation[col] ?? 0) * 0.5;
      if (c === 'a') return `rgba(95, 184, 95, ${alpha})`;
      if (c === 't' || c === 'u') return `rgba(214, 84, 84, ${alpha})`;
      if (c === 'g') return `rgba(61, 126, 184, ${alpha})`;
      if (c === 'c') return `rgba(212, 144, 50, ${alpha})`;
      return 'rgba(136, 136, 136, 0.2)';
    }
    const c = ch.toUpperCase();
    const rgb = aaColor(c);
    if (!rgb) return 'rgba(136, 136, 136, 0.2)';
    // Conservation modulates alpha: highly conserved columns are more saturated.
    const alpha = 0.15 + (conservation[col] ?? 0) * 0.6;
    return `rgba(${rgb}, ${alpha})`;
  }

  // Detect DNA vs AA from the first non-gap residues we see.
  const mode = $derived.by<'dna' | 'aa'>(() => {
    if (seqs.length === 0) return 'dna';
    const sample = seqs[0].seq.toLowerCase().replace(/[-.]/g, '').slice(0, 200);
    const dnaChars = (sample.match(/[acgtun]/g) ?? []).length;
    return dnaChars / Math.max(1, sample.length) > 0.9 ? 'dna' : 'aa';
  });
  // Per-column Shannon entropy normalized to [0, 1] where 1 = fully conserved.
  // Entropy in bits; max for DNA is log2(4)=2, for AA is log2(20)≈4.32.
  const conservation = $derived.by<number[]>(() => {
    if (seqs.length === 0 || alnLength === 0) return [];
    const maxEntropy = mode === 'dna' ? Math.log2(4) : Math.log2(20);
    const out: number[] = [];
    for (let col = 0; col < alnLength; col++) {
      const counts: Record<string, number> = {};
      let nonGap = 0;
      for (const s of seqs) {
        const c = (s.seq[col] ?? '-').toUpperCase();
        if (c === '-' || c === '.') continue;
        counts[c] = (counts[c] ?? 0) + 1;
        nonGap++;
      }
      if (nonGap === 0) {
        out.push(0);
        continue;
      }
      let entropy = 0;
      for (const k in counts) {
        const p = counts[k] / nonGap;
        if (p > 0) entropy -= p * Math.log2(p);
      }
      // Normalize: 1.0 = fully conserved, 0 = max diversity
      const norm = 1 - Math.min(1, entropy / maxEntropy);
      out.push(norm);
    }
    return out;
  });
  // ─── Layout / virtualization ────────────────────────────────────
  const CHAR_W = 11;
  const ROW_H = 18;
  const LABEL_W = 140;
  const CONSERVATION_H = 28;
  const RULER_H = 18;

  let scrollEl: HTMLDivElement;
  let scrollLeft = $state(0);
  let scrollTop = $state(0);
  let viewportW = $state(800);
  let viewportH = $state(400);

  $effect(() => {
    if (!scrollEl) return;
    const ro = new ResizeObserver(() => {
      viewportW = scrollEl.clientWidth;
      viewportH = scrollEl.clientHeight;
    });
    ro.observe(scrollEl);
    viewportW = scrollEl.clientWidth;
    viewportH = scrollEl.clientHeight;
    return () => ro.disconnect();
  });

  function onScroll() {
    if (!scrollEl) return;
    scrollLeft = scrollEl.scrollLeft;
    scrollTop = scrollEl.scrollTop;
  }

  // Visible column / row range (with overscan)
  const colStart = $derived(Math.max(0, Math.floor(scrollLeft / CHAR_W) - 2));
  const colEnd = $derived(Math.min(alnLength, Math.ceil((scrollLeft + viewportW - LABEL_W) / CHAR_W) + 2));
  const rowStart = $derived(Math.max(0, Math.floor(scrollTop / ROW_H) - 2));
  const rowEnd = $derived(Math.min(seqs.length, Math.ceil((scrollTop + viewportH - CONSERVATION_H - RULER_H) / ROW_H) + 2));

  // Total scrollable size
  const totalW = $derived(LABEL_W + alnLength * CHAR_W);
  const totalH = $derived(RULER_H + CONSERVATION_H + seqs.length * ROW_H);

  // Ruler tick stride
  const tickStride = $derived.by(() => {
    if (alnLength <= 50) return 5;
    if (alnLength <= 500) return 25;
    if (alnLength <= 5000) return 100;
    return 500;
  });

  function downloadFasta() {
    const blob = new Blob([fasta], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'alignment.fasta';
    a.click();
    URL.revokeObjectURL(url);
  }
</script>

<div class="aln-viewer" style:height="{height}px">
  <div class="aln-toolbar">
    <span class="mono dim">{seqs.length} seqs · {alnLength} cols · {mode.toUpperCase()}</span>
    <button class="mini" onclick={downloadFasta}>Download alignment</button>
  </div>

  <div class="aln-scroll" bind:this={scrollEl} onscroll={onScroll}>
    <div class="aln-canvas" style:width="{totalW}px" style:height="{totalH}px">
      <!-- Ruler (sticky top) -->
      <div class="ruler" style:top="0" style:left="{LABEL_W}px" style:width="{alnLength * CHAR_W}px">
        {#each Array.from({ length: Math.floor(alnLength / tickStride) + 1 }) as _, i}
          {@const pos = i * tickStride}
          {#if pos < alnLength && pos >= colStart && pos <= colEnd}
            <div class="ruler-tick" style:left="{pos * CHAR_W}px">
              <span class="mono">{pos + 1}</span>
            </div>
          {/if}
        {/each}
      </div>

      <!-- Conservation track -->
      <div class="conservation" style:top="{RULER_H}px" style:left="{LABEL_W}px" style:width="{alnLength * CHAR_W}px">
        {#each conservation as c, i}
          {#if i >= colStart && i <= colEnd}
            <div
              class="cons-bar"
              style:left="{i * CHAR_W}px"
              style:height="{c * (CONSERVATION_H - 4)}px"
              style:opacity={0.3 + c * 0.7}
            ></div>
          {/if}
        {/each}
      </div>

      <!-- Sequence rows -->
      {#each seqs.slice(rowStart, rowEnd) as s, idx (rowStart + idx)}
        {@const rowIdx = rowStart + idx}
        <div class="row" style:top="{RULER_H + CONSERVATION_H + rowIdx * ROW_H}px">
          <div class="label mono" style:width="{LABEL_W}px" title={s.id}>{s.id}</div>
          <div class="seq-row" style:left="{LABEL_W}px" style:width="{alnLength * CHAR_W}px">
            {#each Array.from({ length: colEnd - colStart }) as _, j}
              {@const col = colStart + j}
              {@const ch = s.seq[col] ?? '-'}
              <span
                class="cell mono"
                style:left="{col * CHAR_W}px"
                style:width="{CHAR_W}px"
                style:background={colorFor(ch, mode, col)}
              >{ch === '-' ? '·' : ch.toUpperCase()}</span>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  </div>
</div>

<style>
  .aln-viewer {
    display: flex;
    flex-direction: column;
    background: var(--bg-base);
    border: 1px solid var(--border);
  }
  .aln-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.4rem 0.7rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .aln-scroll {
    flex: 1 1 auto;
    overflow: auto;
    min-height: 0;
    position: relative;
  }
  .aln-canvas {
    position: relative;
  }

  .ruler {
    position: absolute;
    height: 18px;
    border-bottom: 1px solid var(--border);
  }
  .ruler-tick {
    position: absolute;
    top: 0;
    height: 18px;
    border-left: 1px solid var(--border-strong);
    padding-left: 3px;
    color: var(--fg-dim);
    font-size: 10px;
    white-space: nowrap;
  }

  .conservation {
    position: absolute;
    height: 28px;
    background: var(--bg-inset);
    border-bottom: 1px solid var(--border);
  }
  .cons-bar {
    position: absolute;
    bottom: 2px;
    width: 11px;
    background: var(--accent);
  }

  .row {
    position: absolute;
    height: 18px;
    width: 100%;
  }
  .label {
    position: absolute;
    left: 0;
    height: 18px;
    line-height: 18px;
    font-size: 0.78rem;
    color: var(--fg-muted);
    background: var(--bg-base);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding-right: 8px;
    border-right: 1px solid var(--border);
    z-index: 1;
  }
  .seq-row {
    position: absolute;
    height: 18px;
  }
  .cell {
    position: absolute;
    height: 18px;
    line-height: 18px;
    text-align: center;
    font-size: 12px;
    color: var(--fg);
  }

  .mini {
    padding: 0.2rem 0.6rem;
    font-size: 0.7rem;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    font-family: var(--font-mono);
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
</style>
