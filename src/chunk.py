"""Parent-child chunking on MinerU markdown output.

Pipeline per document:
  1. MarkdownHeaderTextSplitter splits by #/##/### → header-anchored sections.
  2. Each section becomes a parent. If a section exceeds PARENT_SIZE, it's
     further split by RecursiveCharacterTextSplitter into multiple parents.
  3. Each parent is split into children of CHILD_SIZE / CHILD_OVERLAP.
  4. Tables and $$...$$ formula blocks are protected: detected and kept as
     atomic children, never split mid-row or mid-LaTeX.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Iterable

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .config import CHILD_OVERLAP, CHILD_SIZE, PARENT_OVERLAP, PARENT_SIZE
from .ingest import ParsedDoc

NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000001")

HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]


# Heading-normalization patterns. MinerU's PDF→markdown collapses every
# detected heading to `#` regardless of its real level in the document — see
# data/parsed/*.md, which are 100% H1, 0% h2/h3. We re-infer the level from
# the numbering text at the start of the line so MarkdownHeaderTextSplitter
# can build a real hierarchy. Order matters: deeper patterns must match
# before shallower ones (e.g. `1.1.1` before `1.1`).
#
# Each rule = (regex on heading body, target hash count).
_HEADING_RULES: list[tuple[re.Pattern, int]] = [
    # `附录 N-M ...`  → H2 (appendix subsection). Match before the bare `附录`.
    (re.compile(r"^附录\s*\d+\s*[-—]\s*\d+"), 2),
    # `附录` or `附录 N ...` → H1
    (re.compile(r"^附录(\s|$)"), 1),
    # `第 N 章 ...`  → H1
    (re.compile(r"^第\s*\d+\s*章"), 1),
    # `N.N.N ...`   → H3 (must precede the 2-dot rule)
    (re.compile(r"^\d+\.\d+\.\d+(?:\s|$|\.)"), 3),
    # `N.N ...`     → H2
    (re.compile(r"^\d+\.\d+(?:\s|$)"), 2),
    # `(N) ...`     → H4 (parenthesized bullet inside a subsection)
    (re.compile(r"^\(\s*\d+\s*\)"), 4),
]

# TOC entries from MinerU look like `# 第1章 概述……1` or `# 1.1 ... … 23` —
# trailing ellipsis + page number. We drop them as headings entirely (treat
# as body text) so they don't pollute the running h1 state when the real
# chapter heading appears later in the document.
_TOC_LINE_RE = re.compile(r"[…\.]{2,}\s*\d+\s*$")


def _normalize_heading_levels(markdown: str) -> str:
    """Rewrite `#` prefix on each heading line based on its numbering pattern.

    Compensates for MinerU's lossy heading-level detection (it puts every
    heading at H1). Lines that don't match any rule keep their original `#`
    count. TOC-style lines ending in `……<page>` are demoted to body text
    so they don't compete with the real chapter heading downstream.
    """
    out: list[str] = []
    for line in markdown.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            out.append(line)
            continue
        # Separate the hash prefix from the body.
        i = 0
        while i < len(stripped) and stripped[i] == "#":
            i += 1
        body = stripped[i:].lstrip()
        # TOC entry — strip the `#` so it becomes plain text and doesn't
        # register as a section boundary.
        if _TOC_LINE_RE.search(body):
            out.append(body)
            continue
        target_level: int | None = None
        for pattern, level in _HEADING_RULES:
            if pattern.match(body):
                target_level = level
                break
        if target_level is None:
            # Unrecognized heading — leave the original `#` count alone.
            out.append(line)
            continue
        out.append(("#" * target_level) + " " + body)
    return "\n".join(out)


@dataclass
class Parent:
    parent_id: str
    text: str
    doc_title: str
    category: str
    section_path: str
    source_path: str
    doc_type: str = "pdf"            # "pdf" | "transcript"
    start_time: str | None = None    # HH:MM:SS, only for transcripts
    company: str | None = None       # only set for category=="公司内部标准"


@dataclass
class Child:
    child_id: str
    parent_id: str
    text: str
    embed_text: str  # text with doc_title>section_path prepended for embedding
    doc_title: str
    category: str
    section_path: str
    source_path: str
    content_type: str  # prose | table | formula
    doc_type: str = "pdf"            # "pdf" | "transcript"
    start_time: str | None = None    # HH:MM:SS, only for transcripts
    company: str | None = None       # only set for category=="公司内部标准"


def _stable_id(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "||".join(parts)))


def _section_path(meta: dict) -> str:
    parts = [meta.get(k) for k in ("h1", "h2", "h3", "h4") if meta.get(k)]
    return " > ".join(parts) if parts else "(intro)"


PIPE_TABLE_RE = re.compile(r"(\n\|[^\n]*\|(?:\n\|[^\n]*\|)+)", re.MULTILINE)
HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
HTML_TR_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.DOTALL | re.IGNORECASE)
HTML_TABLE_OPEN_RE = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
HTML_TABLE_CLOSE_RE = re.compile(r"</table>\s*$", re.IGNORECASE)
FORMULA_RE = re.compile(r"\$\$.+?\$\$", re.DOTALL)

# Tables ≤ this size stay atomic in one parent (even if it overflows
# PARENT_SIZE). Larger tables are row-split with header propagation.
ATOMIC_TABLE_MAX = 2 * PARENT_SIZE


def _find_protected_spans(text: str) -> list[tuple[int, int, str]]:
    """Find table / formula spans. Returns sorted (start, end, kind) tuples
    with overlapping ranges resolved by keeping the earlier-starting span."""
    spans: list[tuple[int, int, str]] = []
    for m in HTML_TABLE_RE.finditer(text):
        spans.append((m.start(), m.end(), "table"))
    for m in PIPE_TABLE_RE.finditer(text):
        spans.append((m.start(), m.end(), "table"))
    for m in FORMULA_RE.finditer(text):
        spans.append((m.start(), m.end(), "formula"))
    spans.sort()
    # Drop spans that start before the previous span ended (overlap).
    deduped: list[tuple[int, int, str]] = []
    last_end = -1
    for s, e, k in spans:
        if s >= last_end:
            deduped.append((s, e, k))
            last_end = e
    return deduped


def _split_protected(text: str) -> list[tuple[str, str]]:
    """Split text into (content_type, chunk) segments where tables/formulas
    are isolated atomic chunks and prose is everything else."""
    spans = _find_protected_spans(text)

    out: list[tuple[str, str]] = []
    cursor = 0
    for start, end, ctype in spans:
        if start > cursor:
            prose = text[cursor:start].strip()
            if prose:
                out.append(("prose", prose))
        out.append((ctype, text[start:end].strip()))
        cursor = end
    if cursor < len(text):
        tail = text[cursor:].strip()
        if tail:
            out.append(("prose", tail))
    return out or [("prose", text.strip())]


def _split_table_with_header(table_html: str, max_size: int) -> list[str]:
    """Row-split an oversized HTML table, prepending the original first <tr>
    (header row) to every fragment so each chunk carries the column labels.

    Returns a list of complete `<table>...</table>` strings. If the table is
    already within `max_size` or can't be split (no rows / single row), it's
    returned unchanged.
    """
    if len(table_html) <= max_size:
        return [table_html]

    open_m = HTML_TABLE_OPEN_RE.match(table_html)
    if not open_m:
        return [table_html]
    open_tag = open_m.group(0)
    inner = table_html[open_m.end():]
    inner = HTML_TABLE_CLOSE_RE.sub("", inner).strip()

    rows = HTML_TR_RE.findall(inner)
    if len(rows) < 2:
        return [table_html]

    header = rows[0]
    body = rows[1:]
    close_tag = "</table>"

    wrapper_overhead = len(open_tag) + len(close_tag) + len(header)
    body_budget = max(max_size - wrapper_overhead, 200)

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for row in body:
        if current and current_size + len(row) > body_budget:
            chunks.append(open_tag + header + "".join(current) + close_tag)
            current = [row]
            current_size = len(row)
        else:
            current.append(row)
            current_size += len(row)
    if current:
        chunks.append(open_tag + header + "".join(current) + close_tag)
    return chunks


def _split_parents(section_text: str) -> list[str]:
    """Split a section into parent-sized chunks.

    Tables are kept atomic up to ATOMIC_TABLE_MAX. Tables larger than that are
    split row-by-row with header propagation, so every fragment carries the
    column-label row. Non-table prose uses the original recursive splitter.
    """
    if len(section_text) <= PARENT_SIZE:
        return [section_text]

    spans = _find_protected_spans(section_text)
    if not spans:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=PARENT_SIZE,
            chunk_overlap=PARENT_OVERLAP,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        )
        return splitter.split_text(section_text)

    segments: list[tuple[str, str]] = []  # (kind, text)
    cursor = 0
    for start, end, kind in spans:
        if start > cursor:
            prose = section_text[cursor:start]
            if prose.strip():
                segments.append(("prose", prose))
        segments.append((kind, section_text[start:end]))
        cursor = end
    if cursor < len(section_text):
        tail = section_text[cursor:]
        if tail.strip():
            segments.append(("prose", tail))

    prose_splitter = RecursiveCharacterTextSplitter(
        chunk_size=PARENT_SIZE,
        chunk_overlap=PARENT_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )

    parents: list[str] = []
    for kind, text in segments:
        if kind == "table" and text.lstrip().lower().startswith("<table"):
            if len(text) <= ATOMIC_TABLE_MAX:
                parents.append(text)
            else:
                parents.extend(_split_table_with_header(text, ATOMIC_TABLE_MAX))
        elif kind in ("table", "formula"):
            parents.append(text)
        else:
            if len(text) <= PARENT_SIZE:
                parents.append(text)
            else:
                parents.extend(prose_splitter.split_text(text))
    return parents


def _split_children(parent_text: str) -> list[tuple[str, str]]:
    """Return [(content_type, chunk_text)] for one parent."""
    segments = _split_protected(parent_text)
    children: list[tuple[str, str]] = []
    prose_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_SIZE,
        chunk_overlap=CHILD_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )
    for ctype, seg in segments:
        if ctype in ("table", "formula"):
            children.append((ctype, seg))
        else:
            for piece in prose_splitter.split_text(seg):
                if piece.strip():
                    children.append(("prose", piece))
    return children


TRANSCRIPT_TURN_RE = re.compile(
    r"^说话人\s+\d+\s+(\d{1,2}:\d{2}:\d{2})\s*$",
    re.MULTILINE,
)


def _parse_transcript_turns(markdown: str) -> list[tuple[str, str]]:
    """Split a transcript markdown into [(HH:MM:SS, body), ...] turns.

    Anything before the first speaker marker (title + meta table) is dropped —
    it carries no timestamp and the title is already in `doc_title`.
    """
    matches = list(TRANSCRIPT_TURN_RE.finditer(markdown))
    turns: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start_time = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[body_start:body_end].strip()
        if body:
            turns.append((start_time, body))
    return turns


def chunk_transcript(doc: ParsedDoc) -> tuple[list[Parent], list[Child]]:
    """Chunk a video transcript: one child per speaker turn, parents pack
    consecutive turns up to PARENT_SIZE. Parent inherits first child's
    start_time so citations can render `[doc @HH:MM:SS]`.
    """
    markdown = doc.markdown_path.read_text(encoding="utf-8")
    turns = _parse_transcript_turns(markdown)
    parents: list[Parent] = []
    children: list[Child] = []

    # Greedy-pack turns into parents.
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_size = 0
    for ts, body in turns:
        turn_text_len = len(body) + len(ts) + 16  # rough overhead for the marker
        if current and current_size + turn_text_len > PARENT_SIZE:
            groups.append(current)
            current = []
            current_size = 0
        current.append((ts, body))
        current_size += turn_text_len
    if current:
        groups.append(current)

    section_path = "transcript"
    for group in groups:
        first_ts = group[0][0]
        parent_text = "\n\n".join(f"说话人 {ts}\n{body}" for ts, body in group)
        parent_id = _stable_id(doc.doc_title, "transcript", first_ts, parent_text)
        parents.append(
            Parent(
                parent_id=parent_id,
                text=parent_text,
                doc_title=doc.doc_title,
                category=doc.category,
                section_path=section_path,
                source_path=str(doc.source_path),
                doc_type="transcript",
                start_time=first_ts,
                company=doc.company,
            )
        )
        for ts, body in group:
            child_text = f"说话人 {ts}\n{body}"
            embed_text = f"{doc.doc_title} @{ts}\n\n{body}"
            child_id = _stable_id(parent_id, "transcript", ts, body[:80])
            children.append(
                Child(
                    child_id=child_id,
                    parent_id=parent_id,
                    text=child_text,
                    embed_text=embed_text,
                    doc_title=doc.doc_title,
                    category=doc.category,
                    section_path=section_path,
                    source_path=str(doc.source_path),
                    content_type="prose",
                    doc_type="transcript",
                    start_time=ts,
                    company=doc.company,
                )
            )
    return parents, children


def chunk_document(doc: ParsedDoc) -> tuple[list[Parent], list[Child]]:
    if doc.doc_type == "transcript":
        return chunk_transcript(doc)

    markdown = doc.markdown_path.read_text(encoding="utf-8")
    # MinerU emits every heading as `#` regardless of its real level; re-infer
    # the level from numbering patterns so the splitter sees a real hierarchy.
    markdown = _normalize_heading_levels(markdown)
    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADERS, strip_headers=False)
    sections = header_splitter.split_text(markdown)

    parents: list[Parent] = []
    children: list[Child] = []
    for sec in sections:
        section_path = _section_path(sec.metadata)
        for parent_text in _split_parents(sec.page_content):
            # Hash the full parent text, not a prefix: header-propagated table
            # fragments share the same opening 80 chars (the column-label row)
            # and would otherwise collide on parent_id.
            parent_id = _stable_id(doc.doc_title, section_path, parent_text)
            parents.append(
                Parent(
                    parent_id=parent_id,
                    text=parent_text,
                    doc_title=doc.doc_title,
                    category=doc.category,
                    section_path=section_path,
                    source_path=str(doc.source_path),
                    doc_type="pdf",
                    company=doc.company,
                )
            )
            header_prefix = f"{doc.doc_title} > {section_path}\n\n"
            for ctype, child_text in _split_children(parent_text):
                child_id = _stable_id(parent_id, ctype, child_text[:80], str(len(children)))
                children.append(
                    Child(
                        child_id=child_id,
                        parent_id=parent_id,
                        text=child_text,
                        embed_text=header_prefix + child_text,
                        doc_title=doc.doc_title,
                        category=doc.category,
                        section_path=section_path,
                        source_path=str(doc.source_path),
                        content_type=ctype,
                        doc_type="pdf",
                        company=doc.company,
                    )
                )
    return parents, children


def chunk_all(docs: Iterable[ParsedDoc]) -> tuple[list[Parent], list[Child]]:
    all_parents: list[Parent] = []
    all_children: list[Child] = []
    for doc in docs:
        p, c = chunk_document(doc)
        print(f"[chunk] {doc.doc_title}: {len(p)} parents / {len(c)} children")
        all_parents.extend(p)
        all_children.extend(c)
    return all_parents, all_children
