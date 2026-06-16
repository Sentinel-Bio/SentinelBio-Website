<script lang="ts">
  import { resolveAncestors } from '$lib/api';

  interface LineageEntry {
    rank?: string | null;
    name: string;
    taxid?: number | null;
  }

  interface Props {
    /** Rich ordered lineage from species.data.lineage — preferred when available. */
    lineage?: LineageEntry[] | null;
    /** Fallback: flat taxonomy map (unreliable ordering). */
    taxonomyMap?: Record<string, string> | null;
    currentRank: string | null;
    currentName: string;
  }

  let { lineage = null, taxonomyMap = null, currentName }: Props = $props();

  // Prefer the rich ordered list; fall back to the flat map if not available.
  const orderedAncestors = $derived.by<LineageEntry[]>(() => {
    if (lineage && lineage.length > 0) {
      // Already ordered root → leaf by NCBI. Just exclude self.
      return lineage.filter((e) => e.name !== currentName);
    }
    if (taxonomyMap) {
      // Best-effort reorder of flat map using known NCBI rank order.
      const RANK_ORDER = [
        'no rank', 'domain', 'superkingdom', 'kingdom', 'subkingdom',
        'superphylum', 'phylum', 'subphylum', 'infraphylum',
        'superclass', 'class', 'subclass', 'infraclass',
        'superorder', 'order', 'suborder', 'infraorder', 'parvorder',
        'superfamily', 'family', 'subfamily',
        'tribe', 'subtribe', 'genus', 'subgenus', 'section',
        'species group', 'species subgroup', 'species', 'subspecies', 'varietas', 'forma'
      ];
      const entries = Object.entries(taxonomyMap)
        .filter(([, n]) => n && n !== currentName)
        .map(([rank, name]) => ({ rank, name }));
      entries.sort((a, b) => {
        const ai = RANK_ORDER.indexOf(a.rank);
        const bi = RANK_ORDER.indexOf(b.rank);
        if (ai === -1 && bi === -1) return 0;
        if (ai === -1) return 1;
        if (bi === -1) return -1;
        return ai - bi;
      });
      return entries;
    }
    return [];
  });

  let resolved = $state<Record<string, { ncbi_tax_id: number } | null>>({});

  $effect(() => {
    // For entries that already carry a taxid (from rich lineage), we don't
    // need to resolve. For the rest, look them up.
    const needsResolution = orderedAncestors
      .filter((e) => !e.taxid)
      .map((e) => e.name);
    if (needsResolution.length > 0) {
      resolveAncestors(needsResolution).then((r) => (resolved = r));
    }
  });

  function slugify(name: string) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  function taxidFor(entry: LineageEntry): number | null {
    if (entry.taxid) return entry.taxid;
    return resolved[entry.name]?.ncbi_tax_id ?? null;
  }
</script>

<nav class="taxo-crumbs" aria-label="Taxonomy">
  {#each orderedAncestors as entry, i}
    {#if i > 0}
      <span class="sep">›</span>
    {/if}
    {@const tid = taxidFor(entry)}
    {#if tid}
      <a 
        href="/species/{tid}/{slugify(entry.name)}"
        class="crumb linked"
        title={entry.rank ?? ''}
      >{entry.name}</a>
    {:else}
      <span class="crumb" title={entry.rank ?? ''}>{entry.name}</span>
    {/if}
  {/each}
  {#if orderedAncestors.length > 0}
    <span class="sep">›</span>
  {/if}
  <span class="crumb current">{currentName}</span>
</nav>

<style>
  .taxo-crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    font-family: var(--font-display);
    font-style: italic;
    font-size: 0.95rem;
    color: var(--fg-muted);
    margin: 1rem 0 1.5rem;
  }
  .sep {
    color: var(--fg-dim);
    font-style: normal;
    font-family: var(--font-mono);
    font-size: 0.85rem;
  }
  .crumb {
    color: var(--fg-muted);
    text-decoration: none;
  }
  .crumb.linked {
    color: var(--accent);
    border-bottom: 1px dotted transparent;
    transition: border-color 0.15s;
  }
  .crumb.linked:hover {
    border-bottom-color: var(--accent);
  }
  .crumb.current {
    color: var(--fg);
    font-weight: 500;
  }
</style>
