/**
 * Renders markdown with inline + block KaTeX.
 *
 * Supports:
 *   $E=mc^2$           inline math
 *   $$\int f(x)\,dx$$  block math (on its own lines)
 *
 * KaTeX is swapped in before markdown parsing so the markdown parser
 * doesn't mangle the math.
 */
import { marked } from 'marked';
import katex from 'katex';

function renderMathBlocks(src: string): string {
  // Block math: $$...$$ on its own line(s). Replace with a pre-rendered HTML span.
  src = src.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
    try {
      return katex.renderToString(tex.trim(), { displayMode: true, throwOnError: false });
    } catch {
      return `<code>[invalid math: ${tex}]</code>`;
    }
  });
  // Inline math: $...$ (not preceded/followed by another $). A simple heuristic.
  src = src.replace(/(^|[^\$])\$([^\$\n]+?)\$(?!\$)/g, (_, pre, tex) => {
    try {
      return `${pre}${katex.renderToString(tex.trim(), { displayMode: false, throwOnError: false })}`;
    } catch {
      return `${pre}<code>[invalid math: ${tex}]</code>`;
    }
  });
  return src;
}

export function renderMarkdown(src: string): string {
  if (!src) return '';
  const withMath = renderMathBlocks(src);
  return marked.parse(withMath, { async: false }) as string;
}
