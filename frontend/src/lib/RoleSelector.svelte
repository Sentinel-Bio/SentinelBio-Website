<script lang="ts">
  interface Props {
    value: string;
    options: string[];
    onchange: (value: string) => void;
  }

  let { value, options, onchange }: Props = $props();

  // Is the current value one of the preset options? If not, user has custom text.
  let isCustom = $state(!options.includes(value) && value !== '');
  let customValue = $state(isCustom ? value : '');

  function handleSelectChange(e: Event) {
    const target = e.target as HTMLSelectElement;
    if (target.value === '__custom__') {
      isCustom = true;
      customValue = '';
      // Don't fire onchange yet — wait for user to type.
    } else {
      isCustom = false;
      onchange(target.value);
    }
  }

  function handleCustomBlur() {
    const v = customValue.trim();
    if (v && v !== value) onchange(v);
  }

  function handleCustomKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      (e.target as HTMLInputElement).blur();
    } else if (e.key === 'Escape') {
      isCustom = false;
      customValue = '';
    }
  }
</script>

{#if isCustom}
  <input
    class="role-input"
    type="text"
    bind:value={customValue}
    onblur={handleCustomBlur}
    onkeydown={handleCustomKey}
    placeholder="custom role…"
    autofocus
  />
{:else}
  <select class="role-select" value={value} onchange={handleSelectChange}>
    {#each options as opt}
      <option value={opt}>{opt}</option>
    {/each}
    <option value="__custom__">Other…</option>
  </select>
{/if}

<style>
  .role-select, .role-input {
    flex: 1;
    padding: 0.25rem 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 0.8rem;
  }
  .role-select:focus, .role-input:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }
</style>
