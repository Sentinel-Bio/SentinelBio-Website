<script lang="ts">
  import { renderMarkdown } from '$lib/markdown';
  import { adminUploadImage } from '$lib/api';

  interface Props {
    value: string;
    onChange: (v: string) => void;
    placeholder?: string;
    rows?: number;
  }

  let { value, onChange, placeholder = '', rows = 18 }: Props = $props();

  let textarea: HTMLTextAreaElement;
  let showPreview = $state(true);
  let uploading = $state(false);
  let fileInput: HTMLInputElement;

  function insertAtCursor(text: string) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const next = value.slice(0, start) + text + value.slice(end);
    onChange(next);
    // After Svelte reflows, restore cursor just after the inserted text.
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.selectionStart = textarea.selectionEnd = start + text.length;
    });
  }

  async function handleImageUpload(e: Event) {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    uploading = true;
    try {
      const { url } = await adminUploadImage(file);
      const alt = file.name.replace(/\.[^.]+$/, '');
      insertAtCursor(`\n\n![${alt}](${url})\n\n`);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'upload failed');
    }
    uploading = false;
    (e.target as HTMLInputElement).value = '';
  }

  function wrapSelection(before: string, after: string = before) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selected = value.slice(start, end);
    const next = value.slice(0, start) + before + selected + after + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.selectionStart = start + before.length;
      textarea.selectionEnd = end + before.length;
    });
  }

  function insertHeading() { insertAtCursor('\n\n## Heading\n\n'); }
  function insertLink() { wrapSelection('[', '](https://)'); }
  function insertInlineMath() { wrapSelection('$', '$'); }
  function insertBlockMath() { insertAtCursor('\n\n$$\n\n$$\n\n'); }
</script>

<div class="md-editor" class:split={showPreview}>
  <div class="md-toolbar">
    <button type="button" onclick={() => wrapSelection('**')} title="Bold"><strong>B</strong></button>
    <button type="button" onclick={() => wrapSelection('*')} title="Italic"><em>I</em></button>
    <button type="button" onclick={() => wrapSelection('`')} title="Code"><code>&lt;&gt;</code></button>
    <button type="button" onclick={insertHeading} title="Heading">H</button>
    <button type="button" onclick={insertLink} title="Link">🔗</button>
    <button type="button" onclick={insertInlineMath} title="Inline math">$x$</button>
    <button type="button" onclick={insertBlockMath} title="Block math">$$</button>
    <button type="button" onclick={() => fileInput.click()} disabled={uploading} title="Upload image">
      {uploading ? '…' : '🖼'}
    </button>
    <input
      bind:this={fileInput}
      type="file"
      accept="image/*"
      style="display: none;"
      onchange={handleImageUpload}
    />
    <span class="spacer"></span>
    <button type="button" onclick={() => (showPreview = !showPreview)}>
      {showPreview ? 'Hide preview' : 'Show preview'}
    </button>
  </div>

  <div class="md-panes">
    <textarea
      bind:this={textarea}
      {value}
      oninput={(e) => onChange((e.target as HTMLTextAreaElement).value)}
      {rows}
      {placeholder}
    ></textarea>
    {#if showPreview}
      <div class="md-preview md-rendered">
        {@html renderMarkdown(value || '*Preview appears here as you type.*')}
      </div>
    {/if}
  </div>
</div>

<style>
  .md-editor {
    border: 1px solid var(--border-strong);
    background: var(--bg-base);
  }

  .md-toolbar {
    display: flex;
    gap: 0.25rem;
    padding: 0.5rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
    align-items: center;
  }
  .md-toolbar button {
    padding: 0.35rem 0.65rem;
    background: transparent;
    border: 1px solid transparent;
    color: var(--fg-muted);
    font-family: var(--font-mono);
    font-size: 0.82rem;
    cursor: pointer;
    transition: all 0.15s;
    text-transform: none;
    letter-spacing: 0;
  }
  .md-toolbar button:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
    background: var(--accent-dim);
  }
  .md-toolbar button:disabled { opacity: 0.5; cursor: not-allowed; }
  .md-toolbar code { font-size: 0.72rem; }
  .spacer { flex: 1; }

  .md-panes {
    display: grid;
    grid-template-columns: 1fr;
  }
  .md-editor.split .md-panes {
    grid-template-columns: 1fr 1fr;
  }

  textarea {
    width: 100%;
    min-height: 400px;
    border: none;
    padding: 1rem;
    font-family: var(--font-mono);
    font-size: 0.88rem;
    line-height: 1.65;
    background: var(--bg-inset);
    color: var(--fg);
    resize: vertical;
  }
  textarea:focus {
    outline: none;
    box-shadow: inset 0 0 0 2px var(--accent-dim);
  }

  .md-preview {
    padding: 1rem 1.25rem;
    overflow-y: auto;
    max-height: 700px;
    background: var(--bg-base);
    border-left: 1px solid var(--border);
    line-height: 1.65;
  }
  .md-preview :global(img) {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1em 0;
  }
  .md-preview :global(h1) { font-size: 1.5rem; margin-top: 1.5rem; }
  .md-preview :global(h2) { font-size: 1.25rem; margin-top: 1.25rem; }
  .md-preview :global(h3) { font-size: 1.1rem; margin-top: 1rem; }
  .md-preview :global(p) { margin: 0.75em 0; }
  .md-preview :global(code) {
    background: var(--bg-inset);
    padding: 0.15em 0.4em;
    font-family: var(--font-mono);
    font-size: 0.88em;
  }
  .md-preview :global(pre) {
    background: var(--bg-inset);
    padding: 0.9em;
    overflow-x: auto;
  }

  @media (max-width: 900px) {
    .md-editor.split .md-panes {
      grid-template-columns: 1fr;
    }
    .md-preview {
      border-left: none;
      border-top: 1px dashed var(--border);
    }
  }
</style>
