<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  interface Props {
    /** PDB or CIF text content to display. */
    structureData?: string | null;
    /** Alternative: load by RCSB PDB ID. */
    pdbId?: string | null;
    format?: 'pdb' | 'cif' | 'mmcif';
    height?: number;
    background?: string;
  }

  let {
    structureData = null,
    pdbId = null,
    format = 'pdb',
    height = 500,
    background = '#141418',
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
        [PluginConfig.Viewport.ShowTrajectoryControls, false],
        [PluginConfig.Background.styles, [
          {
            label: 'Sentinel Dark',
            value: {
              variant: {
                name: 'off',
                params: {},
              },
            },
          },
        ]],
      ]; 
      spec.layout = {
        initial: {
          isExpanded: false,
          showControls: false,
          regionState: {
            top: 'hidden',
            left: 'hidden',
            right: 'hidden',
            bottom: 'hidden',
          },
        },
      };

      plugin = await createPluginUI({
        target: container,
        spec,
        render: renderReact18,
      });

      const { Color } = await import('molstar/lib/mol-util/color');
      plugin.canvas3d?.setProps({
        renderer: {
          backgroundColor: Color(0x141418),  // matches your --bg-base
        },
      });

      await loadStructure();
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
      const fmt = format === 'mmcif' ? 'mmcif' : format === 'cif' ? 'mmcif' : 'pdb';
      const trajectory = await plugin.builders.structure.parseTrajectory(data, fmt);
      await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default');
    }
  }

  $effect(() => {
    const _ = pdbId;
    const __ = structureData;
    if (plugin) {
      plugin.clear();
      loadStructure();
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
</div>

<style>
  .structure-viewer {
    position: relative;
    width: 100%;
    border: 1px solid var(--border);
  }
  .molstar-container {
    width: 100%;
    height: 100%;
    position: absolute;
    top: 0;
    left: 0;
  }
  .loading, .error {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    z-index: 1;
  }
  .error { color: #c93535; }

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
