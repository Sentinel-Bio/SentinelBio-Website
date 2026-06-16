<script lang="ts">
  /**
   * Drag-and-drop / click-to-upload zone.
   * Supports multiple files. Shows a preview list with detected mime+category
   * AFTER upload (server is authoritative for detection).
   */
  import { uploadProjectFile, type ProjectFile } from '$lib/api';

  interface Props {
    projectSlug: string;
    speciesId?: string | null;
    onUploaded?: (files: ProjectFile[]) => void;
  }
  let { projectSlug, speciesId = null, onUploaded }: Props = $props();

  interface UploadState {
    file: File;
    status: 'pending' | 'uploading' | 'done' | 'failed';
    error?: string;
    result?: ProjectFile;
  }
  let queue = $state<UploadState[]>([]);
  let isDragging = $state(false);

  function handlePicked(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    const newItems: UploadState[] = Array.from(fileList).map((f) => ({
      file: f, status: 'pending',
    }));
    queue = [...queue, ...newItems];
    processQueue();
  }

  let processing = false;
  async function processQueue() {
    if (processing) return;
    processing = true;
    try {
      for (let i = 0; i < queue.length; i++) {
        if (queue[i].status !== 'pending') continue;
        queue[i] = { ...queue[i], status: 'uploading' };
        queue = [...queue];
        try {
          const result = await uploadProjectFile(projectSlug, {
            file: queue[i].file,
            species_id: speciesId ?? undefined,
          });
          queue[i] = { ...queue[i], status: 'done', result };
        } catch (e) {
          queue[i] = {
            ...queue[i],
            status: 'failed',
            error: e instanceof Error ? e.message : 'upload failed',
          };
        }
        queue = [...queue];
      }
    } finally {
      processing = false;
    }
    // Notify parent of all done uploads in this batch.
    const completed = queue.filter((q) => q.status === 'done' && q.result).map((q) => q.result!);
    if (completed.length > 0) onUploaded?.(completed);
  }

  function clearDone() {
    queue = queue.filter((q) => q.status !== 'done');
  }

  function clearAll() {
    queue = [];
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    isDragging = false;
    handlePicked(e.dataTransfer?.files ?? null);
  }
  function onDragOver(e: DragEvent) {
    e.preventDefault();
    isDragging = true;
  }
  function onDragLeave() {
    isDragging = false;
  }

  function bytesPretty(n: number): string {
    if (n > 1e9) return (n / 1e9).toFixed(2) + ' GB';
    if (n > 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n > 1e3) return (n / 1e3).toFixed(0) + ' KB';
    return n + ' B';
  }
</script>

<div
  class="upload-zone"
  class:dragging={isDragging}
  ondrop={onDrop}
  ondragover={onDragOver}
  ondragleave={onDragLeave}
  role="region"
  aria-label="upload area"
>
  <div class="drop-prompt">
    <div class="title mono">drop files to upload</div>
    <div class="sub dim mono small">FASTA · FASTQ · PDB · CIF · GFF · Newick · GenBank · etc.</div>
    <label class="upload-btn">
      Choose files
      <input
        type="file"
        multiple
        onchange={(e) => handlePicked((e.target as HTMLInputElement).files)}
        hidden
      />
    </label>
  </div>

  {#if queue.length > 0}
    <div class="queue">
      <div class="queue-head">
        <span class="mono dim small">{queue.length} file{queue.length === 1 ? '' : 's'} in queue</span>
        <div>
          <button class="mini" onclick={clearDone}>Clear done</button>
          <button class="mini" onclick={clearAll}>Clear all</button>
        </div>
      </div>
      <ul>
        {#each queue as item (item.file.name + item.file.size)}
          <li class="row" class:done={item.status === 'done'} class:failed={item.status === 'failed'}>
            <div class="info">
              <div class="mono name">{item.file.name}</div>
              <div class="mono dim small">
                {bytesPretty(item.file.size)} · {item.status}
                {#if item.status === 'done' && item.result}
                  → {item.result.mime_hint}
                  {#if item.result.source_metadata?.category}
                    / {item.result.source_metadata.category}
                  {/if}
                {/if}
                {#if item.status === 'failed'}— {item.error}{/if}
              </div>
            </div>
            <div class="status-pill mono small status-{item.status}">
              {#if item.status === 'pending'}…
              {:else if item.status === 'uploading'}⋯
              {:else if item.status === 'done'}✓
              {:else}✗
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .upload-zone {
    border: 2px dashed var(--border-strong);
    padding: 1rem;
    margin-bottom: 1rem;
    background: var(--bg-base);
    transition: background 0.15s, border-color 0.15s;
  }
  .upload-zone.dragging {
    border-color: var(--accent);
    background: var(--accent-dim);
  }
  .drop-prompt {
    text-align: center;
    padding: 0.6rem 0.8rem;
  }
  .title {
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    color: var(--fg-dim);
    margin-bottom: 0.25rem;
  }
  .sub { margin-bottom: 0.6rem; }
  .upload-btn {
    display: inline-block;
    padding: 0.4rem 0.9rem;
    font-family: var(--font-mono);
    font-size: 0.78rem;
    border: 1px solid var(--border-strong);
    color: var(--fg-muted);
    cursor: pointer;
  }
  .upload-btn:hover { color: var(--accent); border-color: var(--accent); }

  .queue {
    margin-top: 0.8rem;
    padding-top: 0.8rem;
    border-top: 1px dashed var(--border);
  }
  .queue-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.4rem;
  }
  .queue ul { list-style: none; padding: 0; margin: 0; }
  .row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.35rem 0.5rem;
    margin-bottom: 0.2rem;
    background: var(--bg-inset);
    border-left: 2px solid var(--border);
  }
  .row.done { border-left-color: #5fa872; }
  .row.failed { border-left-color: #c93535; }
  .info { min-width: 0; flex: 1; }
  .name {
    font-size: 0.82rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status-pill {
    padding: 0.15rem 0.5rem;
    border: 1px solid var(--border);
    color: var(--fg-muted);
    flex-shrink: 0;
  }
  .status-done { color: #5fa872; border-color: #5fa872; }
  .status-failed { color: #c93535; border-color: #c93535; }
  .small { font-size: 0.66rem; }
  .mini {
    padding: 0.15rem 0.5rem;
    font-size: 0.66rem;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
    margin-left: 0.2rem;
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mono { font-family: var(--font-mono); }
  .dim { color: var(--fg-dim); }
</style>
