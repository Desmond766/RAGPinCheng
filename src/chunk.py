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
from dataclasses import dataclass, field
from typing import Iterable

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .config import CHILD_OVERLAP, CHILD_SIZE, PARENT_OVERLAP, PARENT_SIZE
from .ingest import ParsedDoc

NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000001")

HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]


@dataclass
class Parent:
    parent_id: str
    text: str
    doc_title: str
    category: str
    section_path: str
    source_path: str


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


def _stable_id(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "||".join(parts)))


def _section_path(meta: dict) -> str:
    parts = [meta.get(k) for k in ("h1", "h2", "h3") if meta.get(k)]
    return " > ".join(parts) if parts else "(intro)"


TABLE_RE = re.compile(r"(\n\|[^\n]*\|(?:\n\|[^\n]*\|)+)", re.MULTILINE)
FORMULA_RE = re.compile(r"\$\$.+?\$\$", re.DOTALL)


def _split_protected(text: str) -> list[tuple[str, str]]:
    """Split text into (content_type, chunk) segments where tables/formulas
    are isolated atomic chunks and prose is everything else."""
    spans: list[tuple[int, int, str]] = []
    for m in TABLE_RE.finditer(text):
        spans.append((m.start(), m.end(), "table"))
    for m in FORMULA_RE.finditer(text):
        spans.append((m.start(), m.end(), "formula"))
    spans.sort()

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


def _split_parents(section_text: str) -> list[str]:
    if len(section_text) <= PARENT_SIZE:
        return [section_text]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=PARENT_SIZE,
        chunk_overlap=PARENT_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )
    return splitter.split_text(section_text)


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


def chunk_document(doc: ParsedDoc) -> tuple[list[Parent], list[Child]]:
    markdown = doc.markdown_path.read_text(encoding="utf-8")
    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADERS, strip_headers=False)
    sections = header_splitter.split_text(markdown)

    parents: list[Parent] = []
    children: list[Child] = []
    for sec in sections:
        section_path = _section_path(sec.metadata)
        for parent_text in _split_parents(sec.page_content):
            parent_id = _stable_id(doc.doc_title, section_path, parent_text[:80])
            parents.append(
                Parent(
                    parent_id=parent_id,
                    text=parent_text,
                    doc_title=doc.doc_title,
                    category=doc.category,
                    section_path=section_path,
                    source_path=str(doc.source_path),
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
