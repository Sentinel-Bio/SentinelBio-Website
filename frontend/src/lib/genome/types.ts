/** Shared types for the custom genome viewer. */

export interface Sequence {
  /** Identifier from FASTA header or GenBank LOCUS line. */
  id: string;
  /** Optional description (full FASTA header after the first whitespace). */
  description?: string;
  /** Raw nucleotide string, lowercased, no whitespace. */
  seq: string;
  /** Length in base pairs. Derived but cached. */
  length: number;
}

export interface FeatureLocation {
  /** 1-based inclusive start. Matches GenBank conventions. */
  start: number;
  /** 1-based inclusive end. */
  end: number;
}

export interface Feature {
  /** Feature type from GenBank: gene, CDS, exon, mRNA, rRNA, tRNA, etc. */
  type: string;
  /** Display name — gene symbol if available, otherwise product or feature type. */
  name: string;
  /** A feature can span discontiguous segments (e.g. multi-exon CDS via join()). */
  segments: FeatureLocation[];
  /** True if the feature is on the minus strand. */
  complement: boolean;
  /** Outermost start (min of segments). For sorting and binary search. */
  start: number;
  /** Outermost end (max of segments). */
  end: number;
  /** Raw qualifier dict from GenBank — gene, product, note, etc. */
  qualifiers: Record<string, string>;
}

export interface ParsedGenBank {
  sequence: Sequence;
  features: Feature[];
}

export interface ViewportRange {
  start: number;
  end: number;
}
