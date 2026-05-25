// Citation linker: turn inline `[doc §section]` / `[doc @HH:MM:SS]` markers
// produced by the LLM into markdown links with a sentinel `#cite-…` href.
// react-markdown renders them as <a>; Message.tsx intercepts the click and
// dispatches a CITATION_EVENT so the matching SourcesPanel can open + scroll.

import type { Source } from "../types";

export const CITATION_EVENT = "pincheng:citation-click";

export type CitationDetail = {
  messageId: string;
  sourceIndex: number;
};

// Group 1 = doc title, Group 2 = section path. Negative lookahead avoids
// eating real markdown links `[label](url)`.
const PDF_RE = /\[([^\]\n[]+?)\s*§\s*([^\]\n[]+?)\](?!\()/g;
// Video citations: [doc @HH:MM[:SS]]
const VID_RE = /\[([^\]\n[]+?)\s*@\s*(\d{1,2}:\d{2}(?::\d{2})?)\](?!\()/g;

export function linkifyCitations(markdown: string): string {
  // Run PDF after video so a transcript title containing "§" doesn't get hit.
  return markdown
    .replace(VID_RE, (m, doc, time) => {
      const href = `#cite-vid:${encodeURIComponent(doc.trim())}::${encodeURIComponent(time.trim())}`;
      return `[${m.slice(1, -1)}](${href})`;
    })
    .replace(PDF_RE, (m, doc, section) => {
      const href = `#cite-pdf:${encodeURIComponent(doc.trim())}::${encodeURIComponent(section.trim())}`;
      return `[${m.slice(1, -1)}](${href})`;
    });
}

export function resolveCitation(href: string, sources: Source[]): number {
  // href looks like `#cite-pdf:<doc>::<section>` or `#cite-vid:<doc>::<time>`.
  const m = href.match(/^#cite-(pdf|vid):(.+?)::(.+)$/);
  if (!m) return -1;
  const [, kind, encDoc, encTail] = m;
  const doc = decodeURIComponent(encDoc);
  const tail = decodeURIComponent(encTail);
  // Exact match first.
  let idx = sources.findIndex((s) => {
    if (s.doc_title !== doc) return false;
    if (kind === "vid") return (s.start_time || "") === tail;
    return (s.section_path || "") === tail;
  });
  if (idx >= 0) return idx;
  // Leaf match: the LLM is instructed to cite the leaf of the breadcrumb
  // (e.g. `(5) 钢材耐腐蚀性差`) while `section_path` stores the full path
  // (`第1章 概述 > 1.1 ... > 1.1.1 ... > (5) 钢材耐腐蚀性差`). Match by the
  // trailing segment so short citations still resolve.
  if (kind === "pdf") {
    idx = sources.findIndex(
      (s) =>
        s.doc_title === doc &&
        ((s.section_path || "").split(" > ").pop() || "") === tail,
    );
    if (idx >= 0) return idx;
  }
  // Lenient: prefix match on section (LLM occasionally truncates).
  if (kind === "pdf") {
    idx = sources.findIndex(
      (s) => s.doc_title === doc && (s.section_path || "").startsWith(tail.split(/\s/)[0]),
    );
    if (idx >= 0) return idx;
  }
  // Fallback to doc-title-only match.
  return sources.findIndex((s) => s.doc_title === doc);
}

export function dispatchCitation(detail: CitationDetail) {
  window.dispatchEvent(new CustomEvent<CitationDetail>(CITATION_EVENT, { detail }));
}
