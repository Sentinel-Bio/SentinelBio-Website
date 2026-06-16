<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  interface StructEntry {
    name: string;
    data: string;        // PDB text (already superposed, shared frame)
    color: number;       // 0xRRGGBB
  }

  interface Props {
    structures: StructEntry[];
    height?: number;
    background?: string;
  }

  let { structures, height = 520, background = '#141418' }: Props = $props();

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
        [PluginConfig.Viewport.ShowExpand, true],
        [PluginConfig.Viewport.ShowControls, true],
        [PluginConfig.Viewport.ShowSettings, false],
        [PluginConfig.Viewport.ShowTrajectoryControls, false],
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

      await loadAll();
      loading = false;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Mol* init failed';
      loading = false;
    }
  });

  async function loadAll() {
    if (!plugin) return;
    const { Color } = await import('molstar/lib/mol-util/color');

    for (const s of structures) {
      const data = await plugin.builders.data.rawData({ data: s.data, label: s.name });
      const trajectory = await plugin.builders.structure.parseTrajectory(data, 'pdb');
      const model = await plugin.builders.structure.createModel(trajectory);
      const struct = await plugin.builders.structure.createStructure(model);
      // Uniform color per structure so each overlaid chain is distinguishable.
      await plugin.builders.structure.representation.addRepresentation(struct, {
        type: 'cartoon',
        color: 'uniform',
        colorParams: { value: Color(s.color) },
      });
    }
    // Fit all loaded structures in view.
    plugin.managers.camera.reset();
  }

  $effect(() => {
    const _ = structures;
    if (plugin) {
      plugin.clear();
      loadAll();
    }
  });

  onDestroy(() => {
    if (plugin) { plugin.dispose(); plugin = null; }
  });
</script>

<div class="superpose-viewer" style:height="{height}px" style:background={background}>
  {#if loading}<div class="loading mono dim">Loading Mol*…</div>{/if}
  {#if error}<div class="error mono">{error}</div>{/if}
  <div bind:this={container} class="molstar-container"></div>
</div>

{#if structures.length}
  <div class="legend mono">
    {#each structures as s}
      <span class="legend-item">
        <span class="swatch" style:background={'#' + s.color.toString(16).padStart(6, '0')}></span>
        {s.name}
      </span>
    {/each}
  </div>
{/if}

<style>
  .superpose-viewer { position: relative; width: 100%; border: 1px solid var(--border); }
  .molstar-container { width: 100%; height: 100%; position: absolute; top: 0; left: 0; }
  .loading, .error { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; z-index: 2; }
  .error { color: #c93535; }
  .legend { display: flex; flex-wrap: wrap; gap: 0.8rem; padding: 0.5rem 0.2rem; font-size: 0.72rem; }
  .legend-item { display: inline-flex; align-items: center; gap: 0.35rem; }
  .swatch { width: 0.8rem; height: 0.8rem; border-radius: 2px; display: inline-block; border: 1px solid rgba(255,255,255,0.2); }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
</style>
