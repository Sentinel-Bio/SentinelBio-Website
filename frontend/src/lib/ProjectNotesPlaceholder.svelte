<script lang="ts">
  interface Props {
    title: string;
    body: string;
  }
  let { title, body }: Props = $props();
</script>

<div class="placeholder">
  <h3>{title}</h3>
  <p>{body}</p>
</div>

<style>
  .placeholder {
    padding: 4rem 2rem;
    text-align: center;
    background: var(--bg-inset);
    border: 1px dashed var(--border-strong);
  }
  h3 {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.22em;
    color: var(--fg-dim);
    margin: 0 0 1rem;
  }
  p {
    color: var(--fg-muted);
    max-width: 40rem;
    margin: 0 auto;
    line-height: 1.6;
  }
</style>
