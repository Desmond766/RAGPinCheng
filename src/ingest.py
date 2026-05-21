"""Parse PDFs via MinerU — cloud API (fast) or local CLI (slow fallback).

If MINERU_API_KEY is set in .env, uses the cloud API (GPU servers, ~1 min/PDF).
Otherwise falls back to the local mineru CLI (CPU, ~5-15 min/PDF).

Cloud flow per PDF:
  1. POST /file-urls/batch  → presigned S3 upload URL
  2. PUT  <signed URL>      → upload the raw PDF bytes
  3. POST /extract/task/batch → submit extraction
  4. GET  /extract/task/{id} → poll until done
  5. Download the markdown result URL and cache it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests
from pypdf import PdfReader, PdfWriter

from .config import (
    DOCS_DIR,
    MINERU_API_BASE,
    MINERU_API_KEY,
    MINERU_MAX_PAGES,
    PARSED_DIR,
)


@dataclass
class ParsedDoc:
    source_path: Path
    category: str
    doc_title: str
    markdown_path: Path
    doc_type: str = "pdf"  # "pdf" | "transcript"
    company: str | None = None  # second-level folder under 公司内部标准/


def _safe_stem(pdf: Path) -> str:
    rel = pdf.relative_to(DOCS_DIR)
    return rel.with_suffix("").as_posix().replace("/", "__")


# ── Cloud API path ────────────────────────────────────────────────────────────

def _api_headers() -> dict:
    return {"Authorization": f"Bearer {MINERU_API_KEY}", "Content-Type": "application/json"}


def _split_pdf_for_cloud(pdf: Path, work_dir: Path) -> list[Path]:
    """Return a list of PDF parts each ≤ MINERU_MAX_PAGES pages.

    If the input is already within the limit, returns [pdf] unchanged.
    Otherwise writes split parts to work_dir as <stem>__partNN.pdf.
    """
    reader = PdfReader(str(pdf))
    n_pages = len(reader.pages)
    if n_pages <= MINERU_MAX_PAGES:
        return [pdf]

    work_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, start in enumerate(range(0, n_pages, MINERU_MAX_PAGES), start=1):
        end = min(start + MINERU_MAX_PAGES, n_pages)
        writer = PdfWriter()
        for page_idx in range(start, end):
            writer.add_page(reader.pages[page_idx])
        part_path = work_dir / f"{pdf.stem}__part{i:02d}.pdf"
        with part_path.open("wb") as fh:
            writer.write(fh)
        parts.append(part_path)
        print(f"  [split] {pdf.name}: part {i} pages {start + 1}-{end} → {part_path.name}")
    return parts


def _cloud_parse_batch(parts: list[Path]) -> list[str]:
    """Submit N PDF parts as one MinerU batch. Returns markdown per part, in order.

    Flow per the official docs (https://mineru.net/apiManage/docs):
      1. POST /file-urls/batch → presigned URLs + batch_id
      2. PUT each file to its URL (no Content-Type header)
      3. GET /extract-results/batch/{batch_id} until every entry's state == 'done'

    Extraction is auto-submitted post-upload — there is NO /extract/task/batch call.
    """
    # 1. Request presigned upload URLs.
    files_meta = [{"name": p.name, "data_id": p.name} for p in parts]
    resp = requests.post(
        f"{MINERU_API_BASE}/file-urls/batch",
        headers=_api_headers(),
        json={
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
            "model_version": "vlm",
            "files": files_meta,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") not in (0, None) or "data" not in body:
        raise RuntimeError(f"MinerU /file-urls/batch error: {body}")
    data = body["data"]
    file_urls = data["file_urls"]  # list[str], one URL per file in input order
    batch_id = data["batch_id"]

    # 2. Upload each part in parallel with retry-on-timeout. Send raw bytes
    # (sets Content-Length; avoids chunked encoding which S3 rejects). Per
    # docs: do NOT set Content-Type.
    def _upload_part(part: Path, presigned_url) -> None:
        url = presigned_url["url"] if isinstance(presigned_url, dict) else presigned_url
        payload = part.read_bytes()
        print(f"  [upload] {part.name}: {len(payload):,} bytes → S3")
        last_exc: Exception | None = None
        for attempt in range(1, 4):  # 3 attempts: ~exponential backoff between
            try:
                put_resp = requests.put(url, data=payload, timeout=600)
                if not put_resp.ok:
                    raise RuntimeError(
                        f"S3 upload failed for {part.name} "
                        f"(status={put_resp.status_code}): {put_resp.text[:500]}"
                    )
                return
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt < 3:
                    backoff = 2 ** attempt  # 2s, 4s
                    print(f"  [retry] {part.name} attempt {attempt} failed ({exc}); retrying in {backoff}s")
                    time.sleep(backoff)
        raise RuntimeError(f"S3 upload exhausted retries for {part.name}: {last_exc}")

    with ThreadPoolExecutor(max_workers=min(4, len(parts))) as pool:
        futures = [
            pool.submit(_upload_part, part, url)
            for part, url in zip(parts, file_urls)
        ]
        for f in as_completed(futures):
            f.result()  # re-raise on failure

    # 3. Poll batch results — no overall deadline; wait as long as MinerU needs.
    # Individual HTTP requests still have per-call timeouts to detect a dead
    # connection, but the polling loop itself runs until all parts succeed or
    # any part fails. Cancel with Ctrl-C if you genuinely want to give up.
    print(f"  [cloud] uploaded {len(parts)} part(s), polling batch {batch_id} ...")
    result: dict = {}
    while True:
        poll = requests.get(
            f"{MINERU_API_BASE}/extract-results/batch/{batch_id}",
            headers=_api_headers(),
            timeout=30,
        )
        poll.raise_for_status()
        pbody = poll.json()
        if pbody.get("code") not in (0, None) or "data" not in pbody:
            raise RuntimeError(f"MinerU poll error: {pbody}")
        result = pbody["data"]
        entries = result.get("extract_result", []) or []
        if entries:
            states = [(e.get("file_name", e.get("data_id", "?")), e.get("state", "")) for e in entries]
            n_done = sum(1 for _, s in states if s == "done")
            print(f"  [poll] {n_done}/{len(states)} done — {states}")
            if all(s == "done" for _, s in states):
                break
            if any(s == "failed" for _, s in states):
                failed = [e for e in entries if e.get("state") == "failed"]
                raise RuntimeError(f"MinerU batch had failed part(s): {failed}")
        time.sleep(10)

    # 4. Map results back to input parts and download each markdown.
    entries = result.get("extract_result", []) or []
    by_name: dict[str, dict] = {}
    for e in entries:
        key = e.get("file_name") or e.get("data_id") or ""
        by_name[key] = e
    markdowns: list[str] = []
    for part in parts:
        entry = by_name.get(part.name)
        if not entry:
            raise RuntimeError(
                f"No result entry for {part.name}; got keys: {list(by_name)}"
            )
        md_url = entry.get("full_zip_url") or entry.get("md_url") or entry.get("markdown_url")
        if not md_url:
            raise RuntimeError(f"No markdown URL for {part.name}: {entry}")
        md_resp = requests.get(md_url, timeout=600)
        md_resp.raise_for_status()
        if md_url.endswith(".zip"):
            import io
            import zipfile
            zf = zipfile.ZipFile(io.BytesIO(md_resp.content))
            md_files = [n for n in zf.namelist() if n.endswith(".md")]
            if not md_files:
                raise RuntimeError(f"No .md in zip for {part.name}")
            best = max(md_files, key=lambda n: len(zf.read(n)))
            markdowns.append(zf.read(best).decode("utf-8"))
        else:
            markdowns.append(md_resp.text)
    return markdowns


def _cloud_parse(pdf: Path) -> str:
    """Parse one PDF via the MinerU cloud API, splitting into ≤200-page parts
    if necessary and concatenating the resulting markdown."""
    split_dir = PARSED_DIR / f"_split_{_safe_stem(pdf)}"
    try:
        parts = _split_pdf_for_cloud(pdf, split_dir)
        markdowns = _cloud_parse_batch(parts)
        if len(markdowns) == 1:
            return markdowns[0]
        joined = []
        for i, md in enumerate(markdowns, 1):
            joined.append(f"<!-- part {i}/{len(markdowns)} -->\n\n{md.strip()}\n")
        return "\n\n".join(joined)
    finally:
        if split_dir.exists():
            shutil.rmtree(split_dir, ignore_errors=True)


# ── Local CLI path ────────────────────────────────────────────────────────────

def _mineru_exe() -> str:
    scripts_dir = Path(sys.executable).parent
    for name in ("mineru.exe", "mineru"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    found = shutil.which("mineru")
    if found:
        return found
    raise RuntimeError(
        "mineru CLI not found. Install with `pip install mineru[core]` "
        "or activate the project venv."
    )


def _local_parse(pdf: Path) -> str:
    """Run local mineru CLI and return markdown string."""
    work_dir = PARSED_DIR / f"_work_{_safe_stem(pdf)}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    cmd = [_mineru_exe(), "-p", str(pdf), "-o", str(work_dir), "-m", "auto"]
    subprocess.run(cmd, check=True)
    md_files = list(work_dir.rglob("*.md"))
    if not md_files:
        raise RuntimeError(f"MinerU produced no markdown for {pdf}")
    best = max(md_files, key=lambda p: p.stat().st_size)
    text = best.read_text(encoding="utf-8")
    shutil.rmtree(work_dir, ignore_errors=True)
    return text


# ── Public interface ──────────────────────────────────────────────────────────

def iter_pdfs() -> Iterator[Path]:
    for p in sorted(DOCS_DIR.rglob("*.pdf")):
        yield p


TRANSCRIPTIONS_DIR = DOCS_DIR / "教学视频"
_TRANSCRIPT_PREFIX = "MinerU_markdown_文字记录："
_TRANSCRIPT_TITLE_RE = __import__("re").compile(
    r"^\s*\**\s*文字记录[:：]\s*(.+?)\s*\**\s*$"
)


def iter_transcripts() -> Iterator[Path]:
    """Yield only `文字记录：` markdown files; `智能纪要：` files are skipped."""
    if not TRANSCRIPTIONS_DIR.exists():
        return
    for p in sorted(TRANSCRIPTIONS_DIR.glob("*.md")):
        if p.name.startswith(_TRANSCRIPT_PREFIX):
            yield p


def _transcript_title(md_path: Path) -> str:
    """Read the title from the first non-empty line of a transcript file.

    Falls back to the filename stem (minus the MinerU prefix) if the first
    line doesn't match the expected `**文字记录：<title>**` shape.
    """
    try:
        with md_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                m = _TRANSCRIPT_TITLE_RE.match(line)
                if m:
                    return m.group(1).strip()
                break
    except OSError:
        pass
    stem = md_path.stem
    if stem.startswith(_TRANSCRIPT_PREFIX):
        stem = stem[len(_TRANSCRIPT_PREFIX):]
    return stem


def ingest_all(force: bool = False) -> list[ParsedDoc]:
    use_cloud = bool(MINERU_API_KEY)
    if use_cloud:
        print("[ingest] Using MinerU cloud API")
    else:
        print("[ingest] MINERU_API_KEY not set — using local CLI (slow)")

    docs: list[ParsedDoc] = []
    for pdf in iter_pdfs():
        stem = _safe_stem(pdf)
        final_md = PARSED_DIR / f"{stem}.md"
        parts = pdf.relative_to(DOCS_DIR).parts
        category = parts[0] if len(parts) > 1 else "uncategorized"
        # Only 公司内部标准 carries a company name (second-level folder).
        company = parts[1] if category == "公司内部标准" and len(parts) > 2 else None
        doc_title = pdf.stem

        if final_md.exists() and not force:
            print(f"[skip] {pdf.name}")
            docs.append(ParsedDoc(pdf, category, doc_title, final_md, company=company))
            continue

        print(f"[parse] {pdf.name}")
        try:
            markdown = _cloud_parse(pdf) if use_cloud else _local_parse(pdf)
        except Exception as exc:
            print(f"  [warn] failed ({exc}); skipping {pdf.name}")
            continue

        final_md.write_text(markdown, encoding="utf-8")
        docs.append(ParsedDoc(pdf, category, doc_title, final_md, doc_type="pdf", company=company))

    # Transcripts are already markdown — no MinerU pass needed.
    for md_path in iter_transcripts():
        doc_title = _transcript_title(md_path)
        docs.append(
            ParsedDoc(
                source_path=md_path,
                category="教学视频",
                doc_title=doc_title,
                markdown_path=md_path,
                doc_type="transcript",
            )
        )
        print(f"[transcript] {doc_title}")

    return docs


if __name__ == "__main__":
    force = "--force" in sys.argv
    result = ingest_all(force=force)
    print(f"\nParsed {len(result)} documents.")
