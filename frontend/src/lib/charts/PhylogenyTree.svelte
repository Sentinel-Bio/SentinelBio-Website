<script lang="ts">
  /** Minimal Newick parser + multi-layout dendrogram renderer.
   *  Supports rectangular and circular layouts, ladderise, click-to-rotate
   *  children at a node, inline leaf-label editing (display-only), and a
   *  display panel with scale, label sizes, stroke width, cladogram mode
   *  and bootstrap threshold. */

  interface Props {
    newick: string;
    height?: number;
    runLabel?: string;
  }
  let { newick, height = 600, runLabel = 'phylogeny' }: Props = $props();

  type Layout = 'rectangular' | 'circular';
  type Ladderise = 'off' | 'ascending' | 'descending';

  // --- Display state -------------------------------------------------------
  let pubMode = $state(false);
  let showBootstrap = $state(true);
  let showBranchLengths = $state(false);
  let layoutMode = $state<Layout>('rectangular');
  let ladderiseMode = $state<Ladderise>('off');
  let showSettings = $state(false);

  // --- Sizing / typography -------------------------------------------------
  let treeScale = $state(1.0);
  let leafFontSize = $state(12);
  let bootstrapFontSize = $state(9);
  let branchLengthFontSize = $state(8);
  let strokeWidth = $state(1.5);
  let cladogramMode = $state(false);
  let bootstrapThreshold = $state(0);

  // --- Editing state -------------------------------------------------------
  let editingLeaf = $state<string | null>(null);
  let editValue = $state('');
  let labelOverrides = $state<Record<string, string>>({});
  let rotations = $state<Set<string>>(new Set());

  let svgEl = $state<SVGSVGElement | null>(null);

  interface TreeNode {
    name: string;
    branchLength: number;
    bootstrap: number | null;
    children: TreeNode[];
    x?: number;
    y?: number;
    depth?: number;
  }

  // --- Newick parsing ------------------------------------------------------
  function parseNewick(text: string): TreeNode {
    let pos = 0;
    const t = text.trim().replace(/;\s*$/, '');

    function parse(): TreeNode {
      const node: TreeNode = { name: '', branchLength: 0, bootstrap: null, children: [] };
      if (t[pos] === '(') {
        pos++;
        node.children.push(parse());
        while (t[pos] === ',') {
          pos++;
          node.children.push(parse());
        }
        if (t[pos] === ')') pos++;
        let label = '';
        while (pos < t.length && !':,()'.includes(t[pos])) label += t[pos++];
        const asNum = parseFloat(label);
        if (!isNaN(asNum)) node.bootstrap = asNum;
        else if (label) node.name = label;
      } else {
        let name = '';
        while (pos < t.length && !':,()'.includes(t[pos])) name += t[pos++];
        node.name = name;
      }
      if (t[pos] === ':') {
        pos++;
        let bl = '';
        while (pos < t.length && !',()'.includes(t[pos])) bl += t[pos++];
        node.branchLength = parseFloat(bl) || 0;
      }
      return node;
    }

    return parse();
  }

  // --- Tree manipulation ---------------------------------------------------
  function cloneTree(node: TreeNode): TreeNode {
    return {
      name: node.name,
      branchLength: node.branchLength,
      bootstrap: node.bootstrap,
      children: node.children.map(cloneTree),
    };
  }

  function collectLeaves(node: TreeNode, acc: string[]) {
    if (node.children.length === 0) acc.push(node.name);
    else for (const c of node.children) collectLeaves(c, acc);
  }

  function nodeFingerprint(node: TreeNode): string {
    const leaves: string[] = [];
    collectLeaves(node, leaves);
    return leaves.sort().join('|');
  }

  function ladderise(node: TreeNode, dir: 'ascending' | 'descending'): number {
    if (node.children.length === 0) return 1;
    const counts = node.children.map(c => ladderise(c, dir));
    const indexed = node.children.map((c, i) => ({ c, count: counts[i] }));
    indexed.sort((a, b) => dir === 'ascending' ? a.count - b.count : b.count - a.count);
    node.children = indexed.map(p => p.c);
    return counts.reduce((a, b) => a + b, 0);
  }

  function applyRotations(node: TreeNode, rots: Set<string>) {
    if (node.children.length > 1 && rots.has(nodeFingerprint(node))) {
      node.children.reverse();
    }
    for (const c of node.children) applyRotations(c, rots);
  }

  function maxDepth(node: TreeNode, d = 0): number {
    if (node.children.length === 0) return d;
    return Math.max(...node.children.map(c => maxDepth(c, d + 1)));
  }

  // --- Reactive tree -------------------------------------------------------
  const baseTree = $derived(parseNewick(newick));

  const root = $derived.by(() => {
    const clone = cloneTree(baseTree);
    if (ladderiseMode !== 'off') ladderise(clone, ladderiseMode);
    applyRotations(clone, rotations);
    return clone;
  });

  // --- Layout --------------------------------------------------------------
  function layoutRectangular(node: TreeNode, x: number, depth: number,
                              leafIdx: { i: number }, treeMaxDepth: number) {
    node.depth = depth;
    if (cladogramMode) {
      node.x = node.children.length === 0 ? treeMaxDepth : depth;
    } else {
      node.x = x + node.branchLength;
    }
    if (node.children.length === 0) {
      node.y = leafIdx.i++;
    } else {
      for (const c of node.children) {
        layoutRectangular(c, node.x, depth + 1, leafIdx, treeMaxDepth);
      }
      const ys = node.children.map(c => c.y!);
      node.y = (Math.min(...ys) + Math.max(...ys)) / 2;
    }
  }

  function layoutCircular(node: TreeNode, r: number, depth: number,
                          leafIdx: { i: number }, totalLeaves: number, treeMaxDepth: number) {
    if (cladogramMode) {
      node.x = node.children.length === 0 ? treeMaxDepth : depth;
    } else {
      node.x = r + node.branchLength;
    }
    if (node.children.length === 0) {
      const startAngle = -170;
      const endAngle = 170;
      const sweep = endAngle - startAngle;
      node.y = startAngle + ((leafIdx.i + 0.5) / Math.max(1, totalLeaves)) * sweep;
      leafIdx.i++;
    } else {
      for (const c of node.children) {
        layoutCircular(c, node.x, depth + 1, leafIdx, totalLeaves, treeMaxDepth);
      }
      const ys = node.children.map(c => c.y!);
      node.y = (Math.min(...ys) + Math.max(...ys)) / 2;
    }
  }

  function flatten(n: TreeNode, acc: TreeNode[] = []): TreeNode[] {
    acc.push(n);
    for (const c of n.children) flatten(c, acc);
    return acc;
  }

  function countLeaves(n: TreeNode): number {
    if (n.children.length === 0) return 1;
    return n.children.reduce((a, c) => a + countLeaves(c), 0);
  }

  // --- Container sizing ----------------------------------------------------
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

  const effectiveHeight = $derived(Math.max(120, height * treeScale));
  const rectPadding = { left: 20, right: 220, top: 20, bottom: 20 };
  const circularLabelPadding = 160;

  const layoutResult = $derived.by(() => {
    const r = root;
    const totalLeaves = countLeaves(r);
    const tmd = maxDepth(r);
    const idx = { i: 0 };
    if (layoutMode === 'rectangular') {
      layoutRectangular(r, 0, 0, idx, tmd);
    } else {
      layoutCircular(r, 0, 0, idx, totalLeaves, tmd);
    }
    return { all: flatten(r), numLeaves: idx.i };
  });

  const innerWidth = $derived(Math.max(200, containerWidth - rectPadding.left - rectPadding.right));
  const innerHeight = $derived(Math.max(100, effectiveHeight - rectPadding.top - rectPadding.bottom));
  const maxX = $derived(Math.max(...layoutResult.all.map(n => n.x ?? 0), 0.01));
  const rowH = $derived(layoutResult.numLeaves > 0 ? innerHeight / layoutResult.numLeaves : 20);

  const circSide = $derived(Math.min(containerWidth, effectiveHeight));
  const circR = $derived(Math.max(20, (circSide - circularLabelPadding) / 2));
  const cx = $derived(containerWidth / 2);
  const cy = $derived(effectiveHeight / 2);

  // --- Position helpers ----------------------------------------------------
  function rectPx(n: TreeNode): number {
    return rectPadding.left + ((n.x ?? 0) / maxX) * innerWidth;
  }
  function rectPy(n: TreeNode): number {
    return rectPadding.top + (n.y ?? 0) * rowH + rowH / 2;
  }
  function circToScreen(r: number, angleDeg: number): { x: number; y: number } {
    const a = angleDeg * Math.PI / 180;
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  }
  function circRadius(n: TreeNode): number {
    return ((n.x ?? 0) / maxX) * circR;
  }
  function circPos(n: TreeNode) {
    return circToScreen(circRadius(n), n.y ?? 0);
  }
  function arcPath(r: number, a1: number, a2: number): string {
    const p1 = circToScreen(r, a1);
    const p2 = circToScreen(r, a2);
    const largeArc = Math.abs(a2 - a1) > 180 ? 1 : 0;
    const sweep = a2 > a1 ? 1 : 0;
    return `M ${p1.x} ${p1.y} A ${r} ${r} 0 ${largeArc} ${sweep} ${p2.x} ${p2.y}`;
  }
  function leafLabelInfo(n: TreeNode) {
    const a = n.y ?? 0;
    const labelR = circRadius(n) + 6;
    const p = circToScreen(labelR, a);
    const normalised = ((a % 360) + 360) % 360;
    const flip = normalised > 90 && normalised < 270;
    return {
      x: p.x,
      y: p.y,
      transform: `rotate(${flip ? a + 180 : a} ${p.x} ${p.y})`,
      anchor: (flip ? 'end' : 'start') as 'start' | 'end',
    };
  }

  // --- Display labels ------------------------------------------------------
  function displayName(name: string): string {
    return labelOverrides[name] ?? name;
  }

  // --- Event handlers ------------------------------------------------------
  function startEditLeaf(name: string, evt?: Event) {
    evt?.stopPropagation();
    editingLeaf = name;
    editValue = displayName(name);
  }
  function commitEdit() {
    if (editingLeaf === null) return;
    const val = editValue.trim();
    const original = editingLeaf;
    if (val && val !== original) {
      labelOverrides = { ...labelOverrides, [original]: val };
    } else if (val === original) {
      const next = { ...labelOverrides };
      delete next[original];
      labelOverrides = next;
    }
    editingLeaf = null;
  }
  function cancelEdit() { editingLeaf = null; }
  function toggleRotation(node: TreeNode) {
    const fp = nodeFingerprint(node);
    const next = new Set(rotations);
    if (next.has(fp)) next.delete(fp);
    else next.add(fp);
    rotations = next;
  }
  function resetCustomisations() {
    labelOverrides = {};
    rotations = new Set();
    ladderiseMode = 'off';
  }
  function resetDisplaySettings() {
    treeScale = 1.0;
    leafFontSize = 12;
    bootstrapFontSize = 9;
    branchLengthFontSize = 8;
    strokeWidth = 1.5;
    cladogramMode = false;
    bootstrapThreshold = 0;
  }
  function focusOnMount(node: HTMLInputElement) {
    node.focus();
    node.select();
  }

  // --- Exports -------------------------------------------------------------
  function buildStandaloneSvg(): string {
    if (!svgEl) return '';
    if (editingLeaf) commitEdit();
    const clone = svgEl.cloneNode(true) as SVGSVGElement;

    const branchColor = pubMode ? '#000000' : '#444444';
    const leafColor = pubMode ? '#000000' : '#222222';
    const dotColor = pubMode ? '#000000' : '#b896e3';
    const bootColor = pubMode ? '#444444' : '#777777';
    const leafFontFamily = pubMode
      ? "'Times New Roman', 'Liberation Serif', Times, serif"
      : "ui-sans-serif, system-ui, sans-serif";

    const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    style.textContent = `
      .branch { stroke: ${branchColor}; stroke-width: ${strokeWidth}; fill: none; }
      .leaf-dot { fill: ${dotColor}; }
      .leaf-label {
        fill: ${leafColor};
        font-family: ${leafFontFamily};
        font-style: italic;
        font-size: ${leafFontSize}px;
      }
      .bootstrap {
        fill: ${bootColor};
        font-family: ui-monospace, 'Cascadia Mono', Menlo, Consolas, monospace;
        font-size: ${bootstrapFontSize}px;
      }
      .branch-length {
        fill: ${bootColor};
        font-family: ui-monospace, 'Cascadia Mono', Menlo, Consolas, monospace;
        font-size: ${branchLengthFontSize}px;
      }
      .internal-hit { fill: transparent; }
      svg { background: ${pubMode ? '#ffffff' : 'transparent'}; }
    `;
    clone.insertBefore(style, clone.firstChild);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
    clone.querySelectorAll('foreignObject').forEach(el => el.remove());
    clone.querySelectorAll('.internal-hit').forEach(el => el.remove());
    return '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' +
           new XMLSerializer().serializeToString(clone);
  }

  function downloadFile(content: string | Blob, filename: string, type: string) {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function exportSvg() {
    const svg = buildStandaloneSvg();
    if (!svg) return;
    downloadFile(svg, `${safeName(runLabel)}.svg`, 'image/svg+xml');
  }
  async function exportPng(dpi: number = 300) {
    if (!svgEl) return;
    const svgStr = buildStandaloneSvg();
    const scale = dpi / 96;
    const w = svgEl.clientWidth || parseFloat(svgEl.getAttribute('width') || '800');
    const h = parseFloat(svgEl.getAttribute('height') || String(effectiveHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(w * scale);
    canvas.height = Math.ceil(h * scale);
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const img = new Image();
    const svgBlob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(svgBlob);
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error('SVG-to-image conversion failed'));
      img.src = url;
    });
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    canvas.toBlob((blob) => {
      if (blob) downloadFile(blob, `${safeName(runLabel)}_${dpi}dpi.png`, 'image/png');
    }, 'image/png');
  }
  function exportNewick() {
    downloadFile(newick, `${safeName(runLabel)}.nwk`, 'text/plain');
  }
  function safeName(s: string): string {
    return s.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '') || 'phylogeny';
  }

  const overrideCount = $derived(Object.keys(labelOverrides).length);
  const rotationCount = $derived(rotations.size);
  const hasCustomisations = $derived(
    overrideCount > 0 || rotationCount > 0 || ladderiseMode !== 'off'
  );
</script>

<div class="tree-wrap" class:pub={pubMode} bind:this={containerEl}
     style:height="{effectiveHeight + (showSettings ? 90 : 50)}px">
  <div class="tree-toolbar mono small">
    <select bind:value={layoutMode} class="mini-select" title="Layout">
      <option value="rectangular">▭ rectangular</option>
      <option value="circular">◯ circular</option>
    </select>
    <select bind:value={ladderiseMode} class="mini-select" title="Ladderise (arrange clades by size)">
      <option value="off">no ladderise</option>
      <option value="ascending">ladderise ↑</option>
      <option value="descending">ladderise ↓</option>
    </select>
    <label><input type="checkbox" bind:checked={pubMode} /> publication</label>
    <label><input type="checkbox" bind:checked={showBootstrap} /> bootstrap</label>
    <label><input type="checkbox" bind:checked={showBranchLengths} /> branch lengths</label>
    <button class="mini" class:active={showSettings}
            onclick={() => showSettings = !showSettings}>⚙ display</button>
    <span class="spacer"></span>
    {#if hasCustomisations}
      <button class="mini" onclick={resetCustomisations}
              title="Clear renames, rotations and ladderise">↻ tree</button>
    {/if}
    <button class="mini" onclick={exportSvg}>↓ SVG</button>
    <button class="mini" onclick={() => exportPng(300)}>↓ PNG · 300dpi</button>
    <button class="mini" onclick={() => exportPng(600)}>↓ PNG · 600dpi</button>
    <button class="mini" onclick={exportNewick}>↓ .nwk</button>
  </div>

  {#if showSettings}
    <div class="settings-panel mono small">
      <label class="setting">
        scale
        <input type="range" min="0.5" max="2.5" step="0.05" bind:value={treeScale} />
        <span class="val">{treeScale.toFixed(2)}×</span>
      </label>
      <label class="setting">
        leaf
        <input type="range" min="8" max="22" step="1" bind:value={leafFontSize} />
        <span class="val">{leafFontSize}px</span>
      </label>
      <label class="setting">
        bootstrap
        <input type="range" min="6" max="16" step="1" bind:value={bootstrapFontSize} />
        <span class="val">{bootstrapFontSize}px</span>
      </label>
      <label class="setting">
        branch&nbsp;label
        <input type="range" min="6" max="14" step="1" bind:value={branchLengthFontSize} />
        <span class="val">{branchLengthFontSize}px</span>
      </label>
      <label class="setting">
        stroke
        <input type="range" min="0.5" max="4" step="0.1" bind:value={strokeWidth} />
        <span class="val">{strokeWidth.toFixed(1)}</span>
      </label>
      <label class="setting">
        boot ≥
        <input type="range" min="0" max="100" step="1" bind:value={bootstrapThreshold} />
        <span class="val">{bootstrapThreshold}</span>
      </label>
      <label class="setting toggle">
        <input type="checkbox" bind:checked={cladogramMode} />
        cladogram
      </label>
      <button class="mini" onclick={resetDisplaySettings}>↻ display</button>
    </div>
  {/if}

  {#if containerWidth > 0}
    {#if layoutMode === 'rectangular'}
      <svg width={containerWidth} height={effectiveHeight} bind:this={svgEl}>
        {#each layoutResult.all as n}
          {#each n.children as c}
            <line x1={rectPx(n)} y1={rectPy(c)} x2={rectPx(c)} y2={rectPy(c)}
                  class="branch" stroke-width={strokeWidth} />
          {/each}
          {#if n.children.length > 1}
            {@const ys = n.children.map(c => rectPy(c))}
            <line x1={rectPx(n)} y1={Math.min(...ys)} x2={rectPx(n)} y2={Math.max(...ys)}
                  class="branch" stroke-width={strokeWidth} />
            <circle cx={rectPx(n)} cy={rectPy(n)} r="8" class="internal-hit"
                    onclick={() => toggleRotation(n)}>
              <title>Click to swap children at this node</title>
            </circle>
          {/if}
        {/each}

        {#if showBranchLengths && !cladogramMode}
          {#each layoutResult.all as n}
            {#each n.children as c}
              {#if c.branchLength > 0.001}
                <text x={(rectPx(n) + rectPx(c)) / 2} y={rectPy(c) - 2}
                      class="branch-length" text-anchor="middle"
                      style:font-size="{branchLengthFontSize}px">
                  {c.branchLength.toFixed(3)}
                </text>
              {/if}
            {/each}
          {/each}
        {/if}

        {#if showBootstrap}
          {#each layoutResult.all as n}
            {#if n.children.length > 0 && n.bootstrap !== null && n.bootstrap >= bootstrapThreshold}
              <text x={rectPx(n) + 3} y={rectPy(n) - 3}
                    class="bootstrap" style:font-size="{bootstrapFontSize}px">
                {Math.round(n.bootstrap)}
              </text>
            {/if}
          {/each}
        {/if}

        {#each layoutResult.all as n}
          {#if n.children.length === 0}
            <circle cx={rectPx(n)} cy={rectPy(n)} r="2.5" class="leaf-dot" />
            {#if editingLeaf === n.name}
              <foreignObject x={rectPx(n) + 6} y={rectPy(n) - 12} width="240" height="24">
                <input class="label-edit" bind:value={editValue}
                       onblur={commitEdit}
                       onkeydown={(e) => {
                         if (e.key === 'Enter') commitEdit();
                         else if (e.key === 'Escape') cancelEdit();
                       }}
                       use:focusOnMount />
              </foreignObject>
            {:else}
              <text x={rectPx(n) + 6} y={rectPy(n) + 3}
                    class="leaf-label editable"
                    class:overridden={labelOverrides[n.name] !== undefined}
                    style:font-size="{leafFontSize}px"
                    onclick={(e) => startEditLeaf(n.name, e)}>
                {displayName(n.name)}
                <title>Click to rename (display only; Newick unchanged)</title>
              </text>
            {/if}
          {/if}
        {/each}
      </svg>
    {:else}
      <svg width={containerWidth} height={effectiveHeight} bind:this={svgEl}>
        {#each layoutResult.all as n}
          {#if n.children.length > 1}
            {@const angles = n.children.map(c => c.y ?? 0)}
            {@const minA = Math.min(...angles)}
            {@const maxA = Math.max(...angles)}
            <path d={arcPath(circRadius(n), minA, maxA)} class="branch" stroke-width={strokeWidth} />
          {/if}
          {#each n.children as c}
            {@const p1 = circToScreen(circRadius(n), c.y ?? 0)}
            {@const p2 = circToScreen(circRadius(c), c.y ?? 0)}
            <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
                  class="branch" stroke-width={strokeWidth} />
          {/each}
          {#if n.children.length > 1}
            {@const pos = circToScreen(circRadius(n), n.y ?? 0)}
            <circle cx={pos.x} cy={pos.y} r="8" class="internal-hit"
                    onclick={() => toggleRotation(n)}>
              <title>Click to swap children at this node</title>
            </circle>
          {/if}
        {/each}

        {#if showBootstrap}
          {#each layoutResult.all as n}
            {#if n.children.length > 0 && n.bootstrap !== null && n.bootstrap >= bootstrapThreshold}
              {@const p = circToScreen(circRadius(n) + 4, n.y ?? 0)}
              <text x={p.x} y={p.y} class="bootstrap"
                    text-anchor="middle" dominant-baseline="middle"
                    style:font-size="{bootstrapFontSize}px">
                {Math.round(n.bootstrap)}
              </text>
            {/if}
          {/each}
        {/if}

        {#each layoutResult.all as n}
          {#if n.children.length === 0}
            {@const pos = circPos(n)}
            {@const info = leafLabelInfo(n)}
            <circle cx={pos.x} cy={pos.y} r="2.5" class="leaf-dot" />
            {#if editingLeaf === n.name}
              <foreignObject x={info.x - 120} y={info.y - 12} width="240" height="24">
                <input class="label-edit" bind:value={editValue}
                       onblur={commitEdit}
                       onkeydown={(e) => {
                         if (e.key === 'Enter') commitEdit();
                         else if (e.key === 'Escape') cancelEdit();
                       }}
                       use:focusOnMount />
              </foreignObject>
            {:else}
              <text x={info.x} y={info.y}
                    transform={info.transform}
                    text-anchor={info.anchor}
                    dominant-baseline="middle"
                    class="leaf-label editable"
                    class:overridden={labelOverrides[n.name] !== undefined}
                    style:font-size="{leafFontSize}px"
                    onclick={(e) => startEditLeaf(n.name, e)}>
                {displayName(n.name)}
                <title>Click to rename (display only; Newick unchanged)</title>
              </text>
            {/if}
          {/if}
        {/each}
      </svg>
    {/if}
  {/if}
</div>

<style>
  .tree-wrap { width: 100%; overflow: auto; }
  .tree-wrap.pub { background: #ffffff; }
  .tree-toolbar, .settings-panel {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.35rem 0.6rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    margin-bottom: 0.4rem;
    color: var(--fg-muted);
    flex-wrap: wrap;
  }
  .settings-panel { gap: 0.9rem; }
  .tree-toolbar label, .settings-panel label {
    display: flex; align-items: center; gap: 0.25rem; cursor: pointer;
  }
  .tree-toolbar .spacer { flex: 1; }
  .setting {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    cursor: default;
  }
  .setting input[type="range"] { width: 80px; }
  .setting .val {
    min-width: 2.5rem;
    text-align: right;
    color: var(--fg-dim);
    font-variant-numeric: tabular-nums;
  }
  .setting.toggle { cursor: pointer; }
  .mini, .mini-select {
    padding: 0.15rem 0.5rem;
    font-size: 0.66rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
    font-family: var(--font-mono);
    cursor: pointer;
  }
  .mini:hover, .mini-select:hover { color: var(--accent); border-color: var(--accent); }
  .mini.active { color: var(--accent); border-color: var(--accent); }
  svg { display: block; }
  .branch { stroke: var(--fg-muted); fill: none; }
  .leaf-dot { fill: var(--accent); }
  .leaf-label {
    fill: var(--fg);
    font-family: var(--font-display);
    font-style: italic;
  }
  .leaf-label.editable { cursor: text; }
  .leaf-label.editable:hover { fill: var(--accent); }
  .leaf-label.overridden { font-weight: 600; }
  .bootstrap {
    fill: var(--fg-dim);
    font-family: var(--font-mono);
  }
  .branch-length {
    fill: var(--fg-dim);
    font-family: var(--font-mono);
  }
  .internal-hit { fill: transparent; cursor: pointer; }
  .internal-hit:hover { fill: rgba(184, 150, 227, 0.15); }
  .label-edit {
    width: 100%;
    font-family: var(--font-display);
    font-style: italic;
    font-size: 12px;
    padding: 1px 4px;
    border: 1px solid var(--accent);
    background: var(--bg);
    color: var(--fg);
    box-sizing: border-box;
  }

  .tree-wrap.pub :global(svg) { background: #ffffff; }
  .tree-wrap.pub :global(.branch) { stroke: #000000; }
  .tree-wrap.pub :global(.leaf-dot) { fill: #000000; }
  .tree-wrap.pub :global(.leaf-label) {
    fill: #000000;
    font-family: 'Times New Roman', 'Liberation Serif', Times, serif;
  }
  .tree-wrap.pub :global(.bootstrap) { fill: #444444; }
  .tree-wrap.pub :global(.branch-length) { fill: #444444; }
  .tree-wrap.pub :global(.internal-hit) { display: none; }
  .small { font-size: 0.7rem; }
  .mono { font-family: var(--font-mono); }
</style>
