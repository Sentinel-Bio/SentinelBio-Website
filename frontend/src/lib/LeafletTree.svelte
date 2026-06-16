<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { AllNodesSource, type PositionedNode, type TreeDataSource } from '$lib/treeSource';

  interface Props {
    source?: TreeDataSource;
    selectedTaxid?: number | null;
    onSelect?: (node: PositionedNode) => void;
    onBackgroundClick?: () => void;
    height?: number;
  }

  let {
    source = new AllNodesSource(),
    selectedTaxid = null,
    onSelect,
    onBackgroundClick,
    height = 720,
  }: Props = $props();

  let container: HTMLDivElement;
  let map: any = null;
  let L: any = null;

  // Layer groups.
  let hullLayer: any = null;
  let linkLayer: any = null;
  let nodeLayer: any = null;
  let labelLayer: any = null;

  // Data.
  let allNodes: PositionedNode[] = [];
  let byTaxid: Map<number, PositionedNode> = new Map();
  let childrenMap: Map<number, PositionedNode[]> = new Map();

  // Precomputed hulls, one per internal node with ≥3 descendants.
  // Each hull carries its polygon points, parent's depth, and rank.
  interface PrecomputedHull {
    taxid: number;
    name: string;
    rank: string | null;
    depth: number;
    polygon: [number, number][]; // closed polygon in world coords [y, x]
    family: string;               // top-level domain for color
    bounds: { minX: number; maxX: number; minY: number; maxY: number };
  }
  let precomputedHulls: PrecomputedHull[] = [];
  // Per-hull rendered layers so we can tweak opacity without rebuilding.
  let hullRenderedMap: Map<number, { polygon: any; label: any }> = new Map();

  // ─── Color palette ─────────────────────────────────────────────────

  const FAMILY_COLORS: Record<string, { h: number; s: number }> = {
    Eukaryota: { h: 272, s: 45 },
    Bacteria: { h: 38, s: 70 },
    Archaea: { h: 186, s: 50 },
    'cellular organisms': { h: 0, s: 0 },
  };

  const HULL_DEPTH_BUDGET = 6; // render at most this many depth levels of hulls

  // ─── Geometry ──────────────────────────────────────────────────────

  function chaikinSmooth(points: [number, number][], iterations = 3): [number, number][] {
    let pts = points;
    for (let i = 0; i < iterations; i++) {
      const next: [number, number][] = [];
      const n = pts.length;
      for (let j = 0; j < n; j++) {
        const [ax, ay] = pts[j];
        const [bx, by] = pts[(j + 1) % n];
        next.push([0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by]);
        next.push([0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by]);
      }
      pts = next;
    }
    return pts;
  }

  function convexHull(points: [number, number][]): [number, number][] {
    if (points.length < 3) return points;
    const sorted = [...points].sort(([ax, ay], [bx, by]) =>
      ax === bx ? ay - by : ax - bx
    );
    const cross = (O: [number, number], A: [number, number], B: [number, number]): number =>
      (A[0] - O[0]) * (B[1] - O[1]) - (A[1] - O[1]) * (B[0] - O[0]);

    const lower: [number, number][] = [];
    for (const p of sorted) {
      while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
      lower.push(p);
    }
    const upper: [number, number][] = [];
    for (let i = sorted.length - 1; i >= 0; i--) {
      const p = sorted[i];
      while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
      upper.push(p);
    }
    lower.pop();
    upper.pop();
    return lower.concat(upper);
  }

  /** Does the segment p1→p2 cross or lie inside the axis-aligned rect? */
  function segmentCrossesRect(
    p1x: number, p1y: number, p2x: number, p2y: number,
    minX: number, minY: number, maxX: number, maxY: number,
  ): boolean {
    // Fast path: either endpoint inside.
    const inside = (x: number, y: number) =>
      x >= minX && x <= maxX && y >= minY && y <= maxY;
    if (inside(p1x, p1y) || inside(p2x, p2y)) return true;
    // Bounding-box rejection.
    if (Math.max(p1x, p2x) < minX || Math.min(p1x, p2x) > maxX) return false;
    if (Math.max(p1y, p2y) < minY || Math.min(p1y, p2y) > maxY) return false;
    // Line intersects any of the 4 rect edges (Cohen–Sutherland-ish).
    const cross = (ax: number, ay: number, bx: number, by: number,
                   cx: number, cy: number, dx: number, dy: number): boolean => {
      const d1 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
      const d2 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax);
      const d3 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx);
      const d4 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx);
      return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
    };
    return (
      cross(p1x, p1y, p2x, p2y, minX, minY, maxX, minY) ||
      cross(p1x, p1y, p2x, p2y, maxX, minY, maxX, maxY) ||
      cross(p1x, p1y, p2x, p2y, maxX, maxY, minX, maxY) ||
      cross(p1x, p1y, p2x, p2y, minX, maxY, minX, minY)
    );
  }

  // ─── Family detection ──────────────────────────────────────────────

  function familyForNode(n: PositionedNode): string {
    let cur: PositionedNode | undefined = n;
    while (cur && cur.depth > 1) {
      cur = cur.parent_taxid !== null ? byTaxid.get(cur.parent_taxid) : undefined;
    }
    if (cur && FAMILY_COLORS[cur.name]) return cur.name;
    return 'cellular organisms';
  }

  function colorFor(n: PositionedNode, selected: boolean): string {
    const fam = familyForNode(n);
    const { h, s } = FAMILY_COLORS[fam] ?? FAMILY_COLORS['cellular organisms'];
    const l = Math.min(35 + n.depth * 5, 75);
    const a = selected ? 1 : n.depth === 0 ? 1 : 0.75;
    return `hsla(${h}, ${s}%, ${l}%, ${a})`;
  }

  // ─── Hull precomputation ────────────────────────────────────────────

  function collectDescendants(taxid: number, acc: PositionedNode[]): void {
    const kids = childrenMap.get(taxid) || [];
    for (const k of kids) {
      acc.push(k);
      if (k.taxid !== null) collectDescendants(k.taxid, acc);
    }
  }

  function precomputeHulls(): void {
    precomputedHulls = [];

    // For every node with at least 3 descendants, build a hull.
    for (const n of allNodes) {
      if (n.taxid === null) continue;
      const descendants: PositionedNode[] = [];
      collectDescendants(n.taxid, descendants);
      if (descendants.length < 3) continue;

      // Include the node itself so the hull encloses it.
      const points: [number, number][] = [
        [n.x, n.y],
        ...descendants.map((d) => [d.x, d.y] as [number, number]),
      ];
      const raw = convexHull(points);
      if (raw.length < 3) continue;

      // Pad each vertex outward from the centroid.
      const cx = raw.reduce((s, [x]) => s + x, 0) / raw.length;
      const cy = raw.reduce((s, [, y]) => s + y, 0) / raw.length;
      // Padding scales with subtree "size" roughly, so big clades get more breathing room.
      const basePad = 6;
      const depthScaledPad = basePad + Math.max(0, 30 - n.depth * 2);
      const padded: [number, number][] = raw.map(([x, y]) => {
        const dx = x - cx;
        const dy = y - cy;
        const d = Math.hypot(dx, dy) || 1;
        return [x + (dx / d) * depthScaledPad, y + (dy / d) * depthScaledPad];
      });
      const poly = chaikinSmooth(padded, 3);

      // Bounds for intersection tests.
      const xs = poly.map(([x]) => x);
      const ys = poly.map(([, y]) => y);
      const bounds = {
        minX: Math.min(...xs),
        maxX: Math.max(...xs),
        minY: Math.min(...ys),
        maxY: Math.max(...ys),
      };

      precomputedHulls.push({
        taxid: n.taxid,
        name: n.name,
        rank: n.rank,
        depth: n.depth,
        polygon: poly.map(([x, y]) => [y, x] as [number, number]), // Leaflet lat/lng
        family: familyForNode(n),
        bounds,
      });
    }

    // Initial render — create all hull layers once; we toggle opacity later.
    for (const h of precomputedHulls) {
      const { h: hue, s: sat } = FAMILY_COLORS[h.family] ?? FAMILY_COLORS['cellular organisms'];
      const polygon = L.polygon(h.polygon, {
        color: `hsla(${hue}, ${sat}%, 55%, 0)`,
        weight: 1,
        fillColor: `hsla(${hue}, ${sat}%, 45%, 0)`,
        fillOpacity: 0,
        interactive: false,
      }).addTo(hullLayer);

      // Rank label — rendered as a Leaflet tooltip anchored near the top of the hull.
      const topY = h.bounds.minY;
      const midX = (h.bounds.minX + h.bounds.maxX) / 2;
      const labelEl = L.marker([topY, midX], {
        icon: L.divIcon({
          className: 'hull-rank-label',
          html: `<span style="color: hsl(${hue}, ${sat}%, 70%); opacity: 0">${h.rank ?? ''}</span>`,
          iconSize: [80, 16],
          iconAnchor: [40, 8],
        }),
        interactive: false,
      }).addTo(hullLayer);

      hullRenderedMap.set(h.taxid, { polygon, label: labelEl });
    }
  }

  // ─── Main render ────────────────────────────────────────────────────

  async function render() {
    if (!map || !L) return;

    const b = map.getBounds();
    const sw = b.getSouthWest();
    const ne = b.getNorthEast();
    const vpMinX = sw.lng;
    const vpMaxX = ne.lng;
    const vpMinY = sw.lat;
    const vpMaxY = ne.lat;

    // Nodes whose position is inside the viewport.
    const inViewportTaxids = new Set<number>();
    for (const n of allNodes) {
      if (n.taxid === null) continue;
      if (n.x < vpMinX || n.x > vpMaxX) continue;
      if (n.y < vpMinY || n.y > vpMaxY) continue;
      inViewportTaxids.add(n.taxid);
    }

    // Render set: in-viewport nodes + full ancestor chains + direct children.
    const renderTaxids = new Set<number>(inViewportTaxids);
    for (const t of inViewportTaxids) {
      const n = byTaxid.get(t);
      if (!n) continue;
      // Ancestors to root.
      let p = n.parent_taxid;
      while (p !== null && !renderTaxids.has(p)) {
        renderTaxids.add(p);
        const pn = byTaxid.get(p);
        p = pn?.parent_taxid ?? null;
      }
      // Direct children.
      const kids = childrenMap.get(t) || [];
      for (const k of kids) {
        if (k.taxid !== null) renderTaxids.add(k.taxid);
      }
    }

    const renderNodes = Array.from(renderTaxids)
      .map((t) => byTaxid.get(t))
      .filter((n): n is PositionedNode => n !== undefined);

    // Shallowest depth in view — anchors the hull-depth window.
    let shallowestInView = Infinity;
    for (const t of inViewportTaxids) {
      const n = byTaxid.get(t);
      if (n && n.depth < shallowestInView) shallowestInView = n.depth;
    }
    if (shallowestInView === Infinity) shallowestInView = 0;

    // Selection.
    const selectedSet = new Set<number>();
    if (selectedTaxid !== null && selectedTaxid !== undefined) {
      const stack = [selectedTaxid];
      while (stack.length) {
        const t = stack.pop()!;
        selectedSet.add(t);
        const kids = childrenMap.get(t) || [];
        for (const k of kids) if (k.taxid !== null) stack.push(k.taxid);
      }
    }

    // ─── Hulls: opacity by depth window ──────────────────────────────
    const hullWindowMin = shallowestInView;
    const hullWindowMax = shallowestInView + HULL_DEPTH_BUDGET - 1;
    for (const h of precomputedHulls) {
      const rendered = hullRenderedMap.get(h.taxid);
      if (!rendered) continue;

      const inWindow = h.depth >= hullWindowMin && h.depth <= hullWindowMax;
      // Distance from window start → opacity decay.
      const relDepth = h.depth - hullWindowMin;
      const opacity = inWindow
        ? Math.max(0.08, 0.55 - relDepth * 0.08)
        : 0;

      const { h: hue, s: sat } = FAMILY_COLORS[h.family] ?? FAMILY_COLORS['cellular organisms'];
      rendered.polygon.setStyle({
        color: `hsla(${hue}, ${sat}%, 55%, ${opacity * 1.2})`,
        fillColor: `hsla(${hue}, ${sat}%, 45%, ${opacity * 0.7})`,
        fillOpacity: opacity * 0.7,
      });

      // Label: only visible if hull visible AND hull overlaps viewport.
      const hullVisibleInVp =
        h.bounds.minX < vpMaxX && h.bounds.maxX > vpMinX &&
        h.bounds.minY < vpMaxY && h.bounds.maxY > vpMinY;

      const labelEl = rendered.label.getElement();
      if (labelEl) {
        const span = labelEl.querySelector('span');
        if (span) {
          span.style.opacity = inWindow && hullVisibleInVp && h.rank ? '0.85' : '0';
        }
      }
    }

    // ─── Edges: render any edge that crosses the viewport ────────────
    linkLayer.clearLayers();
    for (const n of allNodes) {
      if (n.parent_taxid === null) continue;
      const parent = byTaxid.get(n.parent_taxid);
      if (!parent) continue;

      // Skip if both endpoints are way off and the segment doesn't cross viewport.
      if (!segmentCrossesRect(n.x, n.y, parent.x, parent.y, vpMinX, vpMinY, vpMaxX, vpMaxY)) {
        continue;
      }

      const inSel = selectedSet.size === 0 || (n.taxid !== null && selectedSet.has(n.taxid));
      L.polyline(
        [[parent.y, parent.x], [n.y, n.x]],
        {
          color: colorFor(n, inSel),
          weight: 0.6,
          opacity: inSel ? 0.5 : 0.1,
          interactive: false,
        }
      ).addTo(linkLayer);
    }

    // ─── Nodes ────────────────────────────────────────────────────────
    nodeLayer.clearLayers();
    const zoom = map.getZoom();

    // Label rule:
    //   depthShown = shallowestInView + (zoom-based budget)
    //   Species-rank leaves also become labeled once zoom is high enough.
    const zoomBudget = Math.floor(zoom + 4);
for (const n of renderNodes) {
      const inSel = selectedSet.size === 0 || (n.taxid !== null && selectedSet.has(n.taxid));

      const visualRadius = Math.max(1.5, 10 - n.depth * 0.4);
      // Hit target — always at least 10px so the cursor can reliably find it.
      const hitRadius = Math.max(visualRadius, 10);

      // Visible marker (no pointer events — lets the hit-target receive them).
      const visible = L.circleMarker([n.y, n.x], {
        radius: visualRadius,
        color: 'rgba(0,0,0,0.25)',
        weight: 0.5,
        fillColor: colorFor(n, inSel),
        fillOpacity: inSel ? 0.75 : 0.15,
        interactive: false,
      });
      visible.addTo(nodeLayer);

      // Invisible click target overlaid on the visible marker.
      const hit = L.circleMarker([n.y, n.x], {
        radius: hitRadius,
        color: 'transparent',
        fillColor: 'transparent',
        fillOpacity: 0,
        weight: 0,
        interactive: true,
        bubblingMouseEvents: false,
      });

      hit.on('click', (e: any) => {
        L.DomEvent.stopPropagation(e);
        if (onSelect) onSelect(n);
      });

      // Highlight on hover so the user knows the node is clickable.
      hit.on('mouseover', () => {
        visible.setStyle({ weight: 1.5, color: 'var(--accent)' });
        const el = (map.getContainer() as HTMLElement);
        el.style.cursor = 'pointer';
      });
      hit.on('mouseout', () => {
        visible.setStyle({ weight: 0.5, color: 'rgba(0,0,0,0.25)' });
        const el = (map.getContainer() as HTMLElement);
        el.style.cursor = '';
      });

      // Label rule stays on the hit target (so tooltips track with it).
      const withinBudget = n.depth <= shallowestInView + zoomBudget;
      const isSpeciesAtHighZoom = (n.rank === 'species' || n.rank === 'subspecies') && zoom >= 4;
      const shouldLabel = withinBudget || isSpeciesAtHighZoom;

      if (shouldLabel) {
        const tooltip = L.tooltip({
          permanent: true,
          direction: 'top',
          offset: [0, -visualRadius],
          className: 'tree-label',
        }).setContent(n.name);
        hit.bindTooltip(tooltip);
      } else {
        hit.bindTooltip(n.name, { sticky: true, className: 'tree-label' });
      }

      hit.addTo(nodeLayer);
    }
  }

  // ─── Lifecycle ──────────────────────────────────────────────────────

  onMount(async () => {
    L = (await import('leaflet')).default;
    await import('leaflet/dist/leaflet.css');

    map = L.map(container, {
      crs: L.CRS.Simple,
      minZoom: -10,
      maxZoom: 20,
      zoomSnap: 0.1,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 120,
      attributionControl: false,
      preferCanvas: true,
    });

    hullLayer = L.layerGroup().addTo(map);
    linkLayer = L.layerGroup().addTo(map);
    nodeLayer = L.layerGroup().addTo(map);
    labelLayer = L.layerGroup().addTo(map);

    map.on('moveend zoomend', render);
    map.on('click', () => {
      if (onBackgroundClick) onBackgroundClick();
    });

    allNodes = await source.getNodes({
      minX: -Infinity, maxX: Infinity, minY: -Infinity, maxY: Infinity, zoom: 99,
    });

    byTaxid = new Map();
    for (const n of allNodes) {
      if (n.taxid !== null) byTaxid.set(n.taxid, n);
    }

    childrenMap = new Map();
    for (const n of allNodes) {
      if (n.parent_taxid === null) continue;
      if (!childrenMap.has(n.parent_taxid)) childrenMap.set(n.parent_taxid, []);
      childrenMap.get(n.parent_taxid)!.push(n);
    }

    if (allNodes.length === 0) return;

    precomputeHulls();

    // Fit initial view.
    const xs = allNodes.map((n) => n.x);
    const ys = allNodes.map((n) => n.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    const width = maxX - minX;
    const dataHeight = maxY - minY;

    const ro = new ResizeObserver(() => {
      const cw = container.clientWidth;
      const ch = container.clientHeight;
      if (cw === 0 || ch === 0) return;

      const padding = 80;
      const availableW = cw - 2 * padding;
      const availableH = ch - 2 * padding;
      const scaleX = width > 0 ? availableW / width : 1;
      const scaleY = dataHeight > 0 ? availableH / dataHeight : 1;
      const scale = Math.min(scaleX, scaleY);
      const zoom = Math.log2(scale);

      map.invalidateSize();
      map.setView([centerY, centerX], zoom, { animate: false });
      ro.disconnect();
      render();
    });
    ro.observe(container);
  });

  onDestroy(() => {
    if (map) {
      map.off();
      map.remove();
      map = null;
    }
  });

  $effect(() => {
    const _ = selectedTaxid;
    if (map) render();
  });
</script>

<div bind:this={container} class="tree-map" style="height: {height}px;"></div>

<style>
  .tree-map {
    width: 100%;
    background: var(--bg-base);
    position: relative;
  }
  .tree-map :global(.leaflet-container) {
    background: var(--bg-base) !important;
    font-family: var(--font-body);
  }
  .tree-map :global(.tree-label) {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: var(--fg);
    font-family: var(--font-display);
    font-style: italic;
    font-size: 0.78rem;
    padding: 0 !important;
    text-shadow:
      -1px -1px 0 var(--bg-base),
      1px -1px 0 var(--bg-base),
      -1px 1px 0 var(--bg-base),
      1px 1px 0 var(--bg-base);
  }
  .tree-map :global(.tree-label::before) {
    display: none;
  }
  .tree-map :global(.hull-rank-label) {
    background: transparent !important;
    border: none !important;
    font-family: var(--font-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    text-align: center;
    text-shadow:
      -1px -1px 0 var(--bg-base),
      1px -1px 0 var(--bg-base),
      -1px 1px 0 var(--bg-base),
      1px 1px 0 var(--bg-base);
    transition: opacity 0.3s;
  }
</style>
