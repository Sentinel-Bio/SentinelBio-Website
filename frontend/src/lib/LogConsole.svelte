<script lang="ts">
  interface Props {
    logs: string;
    status: string;
  }
  let { logs, status }: Props = $props();

  let pre = $state<HTMLPreElement | null>(null);
  let stick = $state(true); // auto-scroll to bottom unless the user scrolled up

  // Auto-scroll to the newest line when logs change, but respect the user
  // having scrolled up to read something.
  $effect(() => {
    // reference logs so this effect re-runs when they update
    const _ = logs;
    if (pre && stick) {
      pre.scrollTop = pre.scrollHeight;
    }
  });

  function onScroll() {
    if (!pre) return;
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
    stick = atBottom;
  }

  const active = $derived(status === 'running' || status === 'queued');
</script>

<div class="console" class:active>
  <div class="console-head mono">
    <span class="dim">run log</span>
    {#if active}<span class="live">● live</span>{/if}
    {#if !stick}
      <button class="jump mono" onclick={() => { stick = true; if (pre) pre.scrollTop = pre.scrollHeight; }}>
        ↓ follow
      </button>
    {/if}
  </div>
  <pre bind:this={pre} onscroll={onScroll} class="console-body mono">{logs || (active ? 'waiting for output…' : 'no log output')}</pre>
</div>

<style>
  .console {
    margin-top: 0.5rem;
    border: 1px solid var(--border);
    background: #0b0d10;
    border-radius: 3px;
    overflow: hidden;
  }
  .console.active { border-color: var(--accent); }
  .console-head {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.3rem 0.6rem;
    background: var(--bg-inset);
    border-bottom: 1px solid var(--border);
    font-size: 0.64rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .live { color: #7fd89c; font-weight: 600; letter-spacing: 0; }
  .jump {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    padding: 0.1rem 0.45rem;
    font-size: 0.62rem;
    cursor: pointer;
  }
  .console-body {
    margin: 0;
    padding: 0.55rem 0.7rem;
    max-height: 260px;
    overflow-y: auto;
    font-size: 0.7rem;
    line-height: 1.45;
    color: #cdd6e0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
</style>
