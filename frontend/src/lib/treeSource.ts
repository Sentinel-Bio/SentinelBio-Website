/**
 * Abstraction over how positioned tree nodes are fetched.
 *
 * Current implementation (AllNodesSource) gets the whole thing in one call.
 * A future TileSource would fetch only what's visible in the current viewport.
 *
 * Components depend on this interface, not the concrete fetcher.
 */

import { PUBLIC_BACKEND_URL } from '$env/static/public';
import { supabaseBrowser } from './supabase';

export interface PositionedNode {
  taxid: number | null;
  name: string;
  rank: string | null;
  parent_taxid: number | null;
  x: number;
  y: number;
  depth: number;
  leaf_count: number;
}

export interface Viewport {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  zoom: number;
}

export interface TreeDataSource {
  /** Fetch the nodes that should be rendered for a given viewport. */
  getNodes(viewport: Viewport): Promise<PositionedNode[]>;
}

/**
 * Fetches the full positioned tree once on construction. Filters client-side
 * based on viewport + zoom when asked.
 *
 * Good for <10k nodes. When we outgrow it, swap for a TileSource.
 */
export class AllNodesSource implements TreeDataSource {
  private cache: PositionedNode[] | null = null;
  private loading: Promise<PositionedNode[]> | null = null;

  private async loadAll(): Promise<PositionedNode[]> {
    if (this.cache) return this.cache;
    if (this.loading) return this.loading;

    this.loading = (async () => {
      const supabase = supabaseBrowser();
      const { data: { session } } = await supabase.auth.getSession();
      const headers: Record<string, string> = {};
      if (session?.access_token) {
        headers['Authorization'] = `Bearer ${session.access_token}`;
      }
      const r = await fetch(`${PUBLIC_BACKEND_URL}/api/species/tree/layout`, { headers });
      if (!r.ok) throw new Error(`${r.status}`);
      const body = await r.json();
      this.cache = body.nodes as PositionedNode[];
      return this.cache;
    })();

    return this.loading;
  }
async getNodes(viewport: Viewport): Promise<PositionedNode[]> {
    const all = await this.loadAll();
    // Return everything visible in the viewport regardless of depth —
    // Leaflet's canvas renderer handles a few thousand nodes fine.
    // When this grows past ~10k, switch to a TileSource.
    return all.filter((n) => {
      if (n.x < viewport.minX || n.x > viewport.maxX) return false;
      if (n.y < viewport.minY || n.y > viewport.maxY) return false;
      return true;
    });
  }
}

/** Tree data source restricted to descendants of a root taxid. */
export class SubtreeSource implements TreeDataSource {
  private rootTaxid: number;
  private cache: PositionedNode[] | null = null;

  constructor(rootTaxid: number) {
    this.rootTaxid = rootTaxid;
  }

  private async loadAll(): Promise<PositionedNode[]> {
    if (this.cache) return this.cache;
    const res = await fetch(
      `${PUBLIC_BACKEND_URL}/api/species/tree/layout?root_taxid=${this.rootTaxid}`
    );
    if (!res.ok) throw new Error(`layout fetch failed: ${res.status}`);
    const data = await res.json();
    this.cache = data.nodes;
    return this.cache!;
  }

  async getNodes(viewport: Viewport): Promise<PositionedNode[]> {
    const all = await this.loadAll();
    return all.filter((n) => {
      if (n.x < viewport.minX || n.x > viewport.maxX) return false;
      if (n.y < viewport.minY || n.y > viewport.maxY) return false;
      return true;
    });
  }
}
