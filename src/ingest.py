"""Run MinerU on each PDF in docs/ and cache the resulting markdown.

MinerU 2.x exposes a `mineru` CLI. We invoke it per-PDF and copy/rename the
markdown output into data/parsed/<pdf_stem>.md, keyed by relative path so
that re-runs skip already-parsed files.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config import DOCS_DIR, PARSED_DIR


def _mineru_exe() -> str:
    """Locate the mineru executable even when the venv isn't activated."""
    # 1. Same dir as the current Python interpreter (works for venvs)
    scripts_dir = Path(sys.executable).parent
    for name in ("mineru.exe", "mineru"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    # 2. Fall back to PATH
    found = shutil.which("mineru")
    if found:
        return found
    raise RuntimeError(
        "mineru CLI not found. Install with `pip install mineru[core]` "
        "or activate the project venv."
    )


@dataclass
class ParsedDoc:
    source_path: Path
    category: str
    doc_title: str
    markdown_path: Path


def _safe_stem(pdf: Path) -> str:
    rel = pdf.relative_to(DOCS_DIR)
    return rel.with_suffix("").as_posix().replace("/", "__")


def _run_mineru(pdf: Path, out_dir: Path) -> Path:
    """Run mineru on a single PDF. Returns the path to the produced .md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [_mineru_exe(), "-p", str(pdf), "-o", str(out_dir), "-m", "auto"]
    subprocess.run(cmd, check=True)
    md_files = list(out_dir.rglob("*.md"))
    if not md_files:
        raise RuntimeError(f"MinerU produced no markdown for {pdf}")
    # Prefer the largest .md (MinerU sometimes emits a layout.md side-file)
    return max(md_files, key=lambda p: p.stat().st_size)


def iter_pdfs() -> Iterator[Path]:
    for p in sorted(DOCS_DIR.rglob("*.pdf")):
        yield p


def ingest_all(force: bool = False) -> list[ParsedDoc]:
    docs: list[ParsedDoc] = []
    for pdf in iter_pdfs():
        stem = _safe_stem(pdf)
        final_md = PARSED_DIR / f"{stem}.md"
        category = pdf.relative_to(DOCS_DIR).parts[0] if len(pdf.relative_to(DOCS_DIR).parts) > 1 else "uncategorized"
        doc_title = pdf.stem

        if final_md.exists() and not force:
            print(f"[skip] {pdf.name} -> {final_md.name}")
            docs.append(ParsedDoc(pdf, category, doc_title, final_md))
            continue

        print(f"[parse] {pdf.name}")
        work_dir = PARSED_DIR / f"_work_{stem}"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        produced = _run_mineru(pdf, work_dir)
        shutil.copyfile(produced, final_md)
        shutil.rmtree(work_dir, ignore_errors=True)

        docs.append(ParsedDoc(pdf, category, doc_title, final_md))
    return docs


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = ingest_all(force=force)
    print(f"\nParsed {len(result)} documents.")
