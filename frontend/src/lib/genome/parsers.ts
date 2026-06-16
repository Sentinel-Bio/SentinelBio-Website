import type { Sequence, Feature, FeatureLocation, ParsedGenBank } from './types';

// ─── FASTA ────────────────────────────────────────────────────────────

/** Parse a FASTA string. Returns all sequences in the file (most have one). */
export function parseFasta(text: string): Sequence[] {
  const sequences: Sequence[] = [];
  let current: { id: string; description: string; chunks: string[] } | null = null;

  for (const rawLine of text.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (line.startsWith('>')) {
      if (current) {
        const seq = current.chunks.join('').toLowerCase();
        sequences.push({
          id: current.id,
          description: current.description || undefined,
          seq,
          length: seq.length,
        });
      }
      const header = line.slice(1).trim();
      const firstSpace = header.indexOf(' ');
      const id = firstSpace === -1 ? header : header.slice(0, firstSpace);
      const description = firstSpace === -1 ? '' : header.slice(firstSpace + 1);
      current = { id: id || 'sequence', description, chunks: [] };
    } else if (current) {
      current.chunks.push(line.trim());
    }
  }
  if (current) {
    const seq = current.chunks.join('').toLowerCase();
    sequences.push({
      id: current.id,
      description: current.description || undefined,
      seq,
      length: seq.length,
    });
  }
  return sequences;
}

// ─── GenBank ──────────────────────────────────────────────────────────

/** Parse a GenBank record. Handles join(), complement(), partial markers (< >),
 *  multi-line qualifiers. Returns the first record's sequence + features.
 *
 *  This is a deliberate subset of the spec — covers the cases that matter for
 *  display purposes. Doesn't validate, doesn't preserve all qualifiers,
 *  doesn't handle multi-record files (returns the first only).
 */
export function parseGenBank(text: string): ParsedGenBank {
  const lines = text.split('\n').map((l) => l.replace(/\r$/, ''));

  let id = 'unknown';
  const features: Feature[] = [];
  let sequence = '';

  let section: 'header' | 'features' | 'origin' = 'header';
  let currentFeatureType: string | null = null;
  let currentLocationLines: string[] = [];
  let currentQualifiers: Record<string, string> = {};
  let pendingQualifier: { key: string; value: string } | null = null;

  function commitFeature() {
    if (!currentFeatureType) return;
    const locStr = currentLocationLines.join('').replace(/\s+/g, '');
    if (!locStr) {
      currentFeatureType = null;
      currentLocationLines = [];
      currentQualifiers = {};
      return;
    }
    if (pendingQualifier) {
      finishPendingQualifier();
    }

    const { segments, complement } = parseLocation(locStr);
    if (segments.length === 0) {
      currentFeatureType = null;
      currentLocationLines = [];
      currentQualifiers = {};
      return;
    }

    const start = Math.min(...segments.map((s) => s.start));
    const end = Math.max(...segments.map((s) => s.end));

    // Pick the best display name from qualifiers.
    const name =
      currentQualifiers.gene ||
      currentQualifiers.locus_tag ||
      currentQualifiers.product ||
      currentQualifiers.label ||
      currentQualifiers.standard_name ||
      currentFeatureType;

    features.push({
      type: currentFeatureType,
      name,
      segments,
      complement,
      start,
      end,
      qualifiers: currentQualifiers,
    });

    currentFeatureType = null;
    currentLocationLines = [];
    currentQualifiers = {};
  }

  function finishPendingQualifier() {
    if (!pendingQualifier) return;
    let value = pendingQualifier.value;
    if (value.startsWith('"') && value.endsWith('"')) {
      value = value.slice(1, -1);
    }
    if (currentQualifiers[pendingQualifier.key]) {
      currentQualifiers[pendingQualifier.key] += '; ' + value;
    } else {
      currentQualifiers[pendingQualifier.key] = value;
    }
    pendingQualifier = null;
  }

  for (const line of lines) {
    if (line.startsWith('LOCUS')) {
      const tokens = line.split(/\s+/);
      if (tokens.length >= 2) id = tokens[1];
    } else if (line.startsWith('FEATURES')) {
      section = 'features';
    } else if (line.startsWith('ORIGIN')) {
      commitFeature();
      section = 'origin';
    } else if (line.startsWith('//')) {
      commitFeature();
      break;
    } else if (section === 'features') {
      // Feature key starts at column 5 (0-indexed). Continuations indent further.
      // Qualifier lines start with /key= at column 21.
      const featureKeyMatch = line.match(/^ {5}(\S+)\s+(.+)$/);
      const continuationMatch = line.match(/^ {21}(.+)$/);

      if (featureKeyMatch && !line.startsWith('     /')) {
        // New feature.
        commitFeature();
        currentFeatureType = featureKeyMatch[1];
        currentLocationLines = [featureKeyMatch[2]];
      } else if (continuationMatch && currentFeatureType) {
        const content = continuationMatch[1];
        if (content.startsWith('/')) {
          // New qualifier.
          finishPendingQualifier();
          const eqIdx = content.indexOf('=');
          if (eqIdx === -1) {
            // Boolean qualifier like /pseudo
            currentQualifiers[content.slice(1)] = 'true';
          } else {
            pendingQualifier = {
              key: content.slice(1, eqIdx),
              value: content.slice(eqIdx + 1),
            };
          }
        } else if (pendingQualifier) {
          // Qualifier value continuation.
          pendingQualifier.value += pendingQualifier.value.endsWith('"') ? '' : ' ';
          pendingQualifier.value += content;
        } else {
          // Location continuation.
          currentLocationLines.push(content);
        }
      }
    } else if (section === 'origin') {
      // Sequence lines: "  1 gatcgatcga tcgatcgatc..."
      const seqPart = line.replace(/[0-9\s]/g, '');
      sequence += seqPart.toLowerCase();
    }
  }

  return {
    sequence: {
      id,
      seq: sequence,
      length: sequence.length,
    },
    features,
  };
}

/** Parse a GenBank location string. Handles:
 *    123..456
 *    complement(123..456)
 *    join(1..50,200..300,400..500)
 *    complement(join(...))
 *    join(complement(...),...)  (rare but valid)
 *    <100..>500  (partial markers — we just strip the < and >)
 *
 *  Returns segments in original order; complement flag tells the renderer
 *  whether to draw an arrow pointing left. */
function parseLocation(loc: string): { segments: FeatureLocation[]; complement: boolean } {
  let complement = false;
  let inner = loc.trim();

  // Strip outer complement(...).
  if (inner.startsWith('complement(') && inner.endsWith(')')) {
    complement = true;
    inner = inner.slice('complement('.length, -1);
  }

  // Strip outer join(...).
  if (inner.startsWith('join(') && inner.endsWith(')')) {
    inner = inner.slice('join('.length, -1);
  }
  // Could also be order(...) — rarer but used. Treat the same.
  if (inner.startsWith('order(') && inner.endsWith(')')) {
    inner = inner.slice('order('.length, -1);
  }

  const segments: FeatureLocation[] = [];
  // Split on top-level commas only.
  for (const part of splitTopLevelCommas(inner)) {
    let seg = part.trim();
    // Inner complement(x..y) — common in mixed-strand joins. We strip but
    // don't propagate per-segment strand because our model is feature-level.
    if (seg.startsWith('complement(') && seg.endsWith(')')) {
      seg = seg.slice('complement('.length, -1);
    }
    // Strip < and > partial markers.
    seg = seg.replace(/[<>]/g, '');
    // Single base: "123" without ".."
    if (!seg.includes('..')) {
      const n = parseInt(seg, 10);
      if (!isNaN(n)) segments.push({ start: n, end: n });
      continue;
    }
    const [s, e] = seg.split('..').map((x) => parseInt(x.trim(), 10));
    if (!isNaN(s) && !isNaN(e)) {
      segments.push({ start: s, end: e });
    }
  }

  return { segments, complement };
}

function splitTopLevelCommas(s: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let current = '';
  for (const ch of s) {
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    if (ch === ',' && depth === 0) {
      out.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  if (current) out.push(current);
  return out;
}
