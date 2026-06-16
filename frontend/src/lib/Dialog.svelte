<script lang="ts">
  /**
   * Generic modal dialog shell.
   *
   * Usage:
   *   <Dialog open={open} onClose={() => open = false} title="Fetch gene">
   *     ...body content...
   *   </Dialog>
   *
   * - Closes on backdrop click, ESC key, or `onClose` callback.
   * - Locks body scroll while open.
   * - Centered, max-width 480px, scrolls internally if content is tall.
   */
  import { onMount } from 'svelte';
  import type { Snippet } from 'svelte';

  interface Props {
    open: boolean;
    onClose: () => void;
    title?: string;
    children: Snippet;
  }
  let { open, onClose, title = '', children }: Props = $props();

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && open) {
      e.preventDefault();
      onClose();
    }
  }

  $effect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  });

  onMount(() => {
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
</script>

{#if open}
  <div
    class="backdrop"
    onclick={onClose}
    role="presentation"
  >
    <div
      class="dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="dlg-title"
      onclick={(e) => e.stopPropagation()}
    >
      {#if title}
        <header class="head">
          <h3 id="dlg-title" class="title mono">{title}</h3>
          <button class="close mono" onclick={onClose} aria-label="close">×</button>
        </header>
      {/if}
      <div class="body">
        {@render children()}
      </div>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: 1rem;
  }
  .dialog {
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    width: 100%;
    max-width: 480px;
    max-height: 90vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.7rem 0.9rem;
    border-bottom: 1px solid var(--border);
  }
  .title {
    margin: 0;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--fg-dim);
  }
  .close {
    background: transparent;
    border: none;
    color: var(--fg-muted);
    cursor: pointer;
    font-size: 1.4rem;
    line-height: 1;
    padding: 0 0.3rem;
  }
  .close:hover { color: var(--accent); }
  .body {
    padding: 0.9rem;
    overflow-y: auto;
  }
  .mono { font-family: var(--font-mono); }
</style>
