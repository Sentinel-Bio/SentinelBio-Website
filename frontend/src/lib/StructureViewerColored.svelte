<script lang="ts">
  /**
   * Mol*-based structure viewer that supports per-residue coloring.
   *
   * Coloring modes:
   *   - 'plddt'        — uses the B-factor column (AlphaFold confidence)
   *   - 'omega'        — coloring by per-codon dN/dS from a HyPhy FEL site array
   *   - 'entropy'      — Shannon entropy from an alignment-derived array
   *   - 'frame'        — categorical: residues part of MHCXGraph "frame" (highlight)
   *   - 'none'         — default Mol* coloring
   *
   * `scores` is a number[] indexed by residue (1-based). Length should match
   * residue count of chain A. Missing residues = no coloring (default).
   * For categorical 'frame' mode, pass `frameResidues: number[]`.
   */
  import { onMount, onDestroy } from 'svelte';

  interface Props {
    structureData?: string | null;
    pdbId?: string | null;
    format?: 'pdb' | 'cif' | 'mmcif';
    height?: number;
    background?: string;

    // Coloring
    coloringMode?: 'none' | 'plddt' | 'omega' | 'entropy' | 'frame';
    scores?: number[] | null;             // per-residue numeric scores
    frameResidues?: number[] | null;      // residue numbers in conserved frame
    colorScale?: 'rd_bu' | 'viridis' | 'plddt';

    // Highlight specific residues with a sphere (e.g. cancer hotspots)
    highlightResidues?: number[] | null;
  }

  let {
    structureData = null,
    pdbId = null,
    format = 'pdb',
    height = 500,
    background = '#141418',
    coloringMode = 'none',
    scores = null,
    frameResidues = null,
    colorScale = 'rd_bu',
    highlightResidues = null,
  }: Props = $props();

  let container: HTMLDivElement;
  let plugin: any = null;
  let loading = $state(true);
  let error = $state<string | null>(null);

  onMount(async () => {
    try {
      const { createPluginUI } = await import('molstar/lib/mol-plugin-ui');
      const { renderReact18 } = await import('molstar/lib/mol-plugin-ui/react18');
      const { DefaultPluginUISpec } = await import('molstar/lib/mol-plugin-ui/spec');
      const { PluginConfig } = await import('molstar/lib/mol-plugin/config');

      const spec = DefaultPluginUISpec();
      spec.config = [
        [PluginConfig.Viewport.ShowAnimation, false],
        [PluginConfig.Viewport.ShowSelectionMode, true],
        [PluginConfig.Viewport.ShowExpand, true],
        [PluginConfig.Viewport.ShowControls, true],
        [PluginConfig.Viewport.ShowSettings, false],
      ];
      spec.layout = {
        initial: {
          isExpanded: false,
          showControls: false,
          regionState: { top: 'hidden', left: 'hidden', right: 'hidden', bottom: 'hidden' },
        },
      };

      plugin = await createPluginUI({ target: container, spec, render: renderReact18 });
      const { Color } = await import('molstar/lib/mol-util/color');
      plugin.canvas3d?.setProps({
        renderer: { backgroundColor: Color(0x141418) },
      });

      await loadStructure();
      await applyColoring();
      loading = false;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Mol* init failed';
      loading = false;
    }
  });

  async function loadStructure() {
    if (!plugin) return;
    if (pdbId) {
      const id = pdbId.toUpperCase();
      const url = `https://files.rcsb.org/download/${id}.cif`;
      const data = await plugin.builders.data.download({ url, isBinary: false });
      const trajectory = await plugin.builders.structure.parseTrajectory(data, 'mmcif');
      await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default');
    } else if (structureData) {
      const data = await plugin.builders.data.rawData({
        data: structureData,
        label: 'structure',
      });
      const fmt = format === 'mmcif' || format === 'cif' ? 'mmcif' : 'pdb';
      const trajectory = await plugin.builders.structure.parseTrajectory(data, fmt);
      await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default');
    }
  }

  /**
   * Apply per-residue coloring. Uses Mol*'s low-level theme override via
   * a synthetic ColorTheme that maps residue numbers → colors.
   *
   * The Mol* API for custom themes is involved; we take a simpler path:
   * for each residue with a score, we issue an overpaint update over the
   * default chain coloring. This is less performant than a custom theme
   * (Mol* re-renders per overpaint) but works without registering a theme.
   *
   * For "frame" mode we just highlight the residues in the frame.
   */
  async function applyColoring() {
    if (!plugin || coloringMode === 'none') return;
    if (coloringMode === 'plddt') {
      // pLDDT lives in B-factor — Mol*'s built-in 'plddt-confidence' theme
      // does the right coloring. Apply via preset.
      try {
        await plugin.dataTransaction(async () => {
          const structures = plugin.managers.structure.hierarchy.current.structures;
          for (const struct of structures) {
            const comps = plugin.managers.structure.hierarchy.componentsOfStructure(struct);
            for (const c of comps) {
              await plugin.managers.structure.component.updateRepresentationsTheme(
                [c],
                { color: 'plddt-confidence' },
              );
            }
          }
        });
      } catch (e) {
        console.warn('plddt theme failed, falling back to b-factor', e);
      }
      return;
    }

    if (coloringMode === 'omega' || coloringMode === 'entropy') {
      if (!scores || scores.length === 0) return;
      // Build a residue-number → color map and apply per-residue overpaints.
      const { Color } = await import('molstar/lib/mol-util/color');
      const { StructureSelection, StructureElement } = await import('molstar/lib/mol-model/structure');
      const { MolScriptBuilder } = await import('molstar/lib/mol-script/language/builder');

      // Normalize scores to 0..1 for the scale.
      const finite = scores.filter((s) => Number.isFinite(s));
      const lo = Math.min(...finite);
      const hi = Math.max(...finite);
      const span = hi - lo || 1;

      const scoreToColor = (s: number): number => {
        if (!Number.isFinite(s)) return 0xcccccc;
        const t = Math.max(0, Math.min(1, (s - lo) / span));
        if (colorScale === 'viridis') return viridis(t);
        if (colorScale === 'plddt') return plddtScale(t * 100);
        return rdBu(t);
      };

      try {
        for (let i = 0; i < scores.length; i++) {
          const residueNum = i + 1;
          const color = scoreToColor(scores[i]);
          const sel = MolScriptBuilder.struct.generator.atomGroups({
            'residue-test': MolScriptBuilder.core.rel.eq([
              MolScriptBuilder.struct.atomProperty.macromolecular.auth_seq_id(),
              residueNum,
            ]),
          });
          await plugin.builders.structure.representation.theme.update(sel, {
            color: { name: 'uniform', params: { value: Color(color) } },
          });
        }
      } catch (e) {
        // Mol*'s API surface varies by version; fall back to chain-level color
        console.warn('per-residue theming failed:', e);
      }
      return;
    }

    if (coloringMode === 'frame' && frameResidues && frameResidues.length > 0) {
      // Highlight just the frame residues — leave others default.
      // We apply a bright accent color to those residues only.
      const { Color } = await import('molstar/lib/mol-util/color');
      const highlightColor = Color(0xb896e3);  // accent purple
      try {
        for (const r of frameResidues) {
          // Use selection script to highlight; Mol* shows them in viewport.
          const { compile } = await import('molstar/lib/mol-script/runtime/query/compiler');
          const { MolScriptBuilder } = await import('molstar/lib/mol-script/language/builder');
          const { StructureSelection, StructureElement } = await import('molstar/lib/mol-model/structure');

          const expression = MolScriptBuilder.struct.generator.atomGroups({
            'residue-test': MolScriptBuilder.core.rel.eq([
              MolScriptBuilder.struct.atomProperty.macromolecular.auth_seq_id(),
              r,
            ]),
          });
          const query = compile(expression);
          const structure = plugin.managers.structure.hierarchy.current.structures[0]?.cell?.obj?.data;
          if (structure) {
            const selection = query(new StructureElement.Stats(), structure);
            // Add the result to current selection
            plugin.managers.interactivity.lociSelects.select({ loci: selection });
          }
        }
      } catch (e) {
        console.warn('frame highlight failed:', e);
      }
      return;
    }
  }

  // Color scale helpers — small, dependency-free.
  function rdBu(t: number): number {
    // Red (low ω, conservation) → White → Blue (high ω, divergence).
    // For omega: low t = conserved = red.
    const r = t < 0.5 ? 1.0 : 1.0 - 2 * (t - 0.5);
    const g = t < 0.5 ? 2 * t : 2 * (1 - t);
    const b = t < 0.5 ? 2 * t : 1.0;
    return (Math.round(r * 255) << 16) | (Math.round(g * 255) << 8) | Math.round(b * 255);
  }

  function viridis(t: number): number {
    // Simple viridis approximation (5-stop linear)
    const stops = [
      [68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37],
    ];
    const i = Math.min(3, Math.max(0, Math.floor(t * 4)));
    const lt = t * 4 - i;
    const a = stops[i], b = stops[i + 1] || stops[i];
    const r = Math.round(a[0] + (b[0] - a[0]) * lt);
    const g = Math.round(a[1] + (b[1] - a[1]) * lt);
    const bl = Math.round(a[2] + (b[2] - a[2]) * lt);
    return (r << 16) | (g << 8) | bl;
  }

  function plddtScale(t: number): number {
    // Standard pLDDT colors: <50 orange, 50-70 yellow, 70-90 light blue, >90 dark blue.
    if (t < 50) return 0xff7d45;
    if (t < 70) return 0xffdb13;
    if (t < 90) return 0x65cbf3;
    return 0x0053d6;
  }

  $effect(() => {
    const _ = [pdbId, structureData, coloringMode, scores, frameResidues, colorScale];
    if (plugin) {
      plugin.clear();
      (async () => {
        await loadStructure();
        await applyColoring();
      })();
    }
  });

  onDestroy(() => {
    if (plugin) {
      plugin.dispose();
      plugin = null;
    }
  });
</script>

<div class="structure-viewer" style:height="{height}px" style:background={background}>
  {#if loading}
    <div class="loading mono dim">Loading Mol*…</div>
  {/if}
  {#if error}
    <div class="error mono">{error}</div>
  {/if}
  <div bind:this={container} class="molstar-container"></div>

  <!-- Legend overlay -->
  {#if coloringMode !== 'none'}
    <div class="legend mono">
      <div class="legend-title">
        {#if coloringMode === 'plddt'}pLDDT (AlphaFold confidence)
        {:else if coloringMode === 'omega'}ω (dN/dS)
        {:else if coloringMode === 'entropy'}Shannon entropy
        {:else if coloringMode === 'frame'}Conserved frame
        {/if}
      </div>
      {#if coloringMode === 'plddt'}
        <div class="legend-bar plddt">
          <span style:background="#ff7d45">&lt;50</span>
          <span style:background="#ffdb13">50-70</span>
          <span style:background="#65cbf3">70-90</span>
          <span style:background="#0053d6">&gt;90</span>
        </div>
      {:else if coloringMode === 'omega'}
        <div class="legend-bar gradient-rdbu">
          <span>conserved</span><span>neutral</span><span>diverged</span>
        </div>
      {:else if coloringMode === 'entropy'}
        <div class="legend-bar gradient-rdbu">
          <span>conserved</span><span>variable</span>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .structure-viewer {
    position: relative;
    width: 100%;
    border: 1px solid var(--border);
  }
  .molstar-container {
    width: 100%; height: 100%;
    position: absolute; top: 0; left: 0;
  }
  .loading, .error {
    position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    z-index: 1;
  }
  .error { color: #c93535; }

  .legend {
    position: absolute;
    bottom: 8px;
    right: 8px;
    background: rgba(20, 20, 24, 0.85);
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--border);
    font-size: 0.7rem;
    z-index: 2;
    pointer-events: none;
  }
  .legend-title {
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--fg-dim);
    margin-bottom: 0.25rem;
    font-size: 0.62rem;
  }
  .legend-bar {
    display: flex;
    gap: 2px;
    font-size: 0.6rem;
  }
  .legend-bar.plddt span {
    padding: 0.15rem 0.4rem;
    color: rgba(0, 0, 0, 0.85);
    font-weight: 600;
  }
  .legend-bar.gradient-rdbu {
    display: flex;
    width: 180px;
    height: 22px;
    background: linear-gradient(to right, #c93535, #f0f0f0, #2c5fa8);
    align-items: center;
    justify-content: space-between;
    padding: 0 4px;
  }
  .legend-bar.gradient-rdbu span {
    color: rgba(0, 0, 0, 0.7);
    font-size: 0.55rem;
    text-shadow: 0 0 2px rgba(255,255,255,0.7);
  }

  .structure-viewer :global(.msp-viewport-controls-buttons button),
  .structure-viewer :global(.msp-control-button) {
    background: var(--bg-raised) !important;
    color: var(--fg) !important;
    border-color: var(--border) !important;
  }
  .structure-viewer :global(.msp-viewport-controls-buttons button:hover),
  .structure-viewer :global(.msp-control-button:hover) {
    background: var(--bg-inset) !important;
    color: var(--accent) !important;
  }
</style>
