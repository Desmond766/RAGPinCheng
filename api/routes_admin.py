"""Admin endpoints — user management, cross-user conversation read,
system stats, feedback-log viewer, manual sweep.

All endpoints require an authenticated user with role='admin'. Read endpoints
use `require_admin`; mutating endpoints add the CSRF check via
`require_csrf_admin`.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from src.config import DOCS_DIR, SECOND_LEVEL_CATEGORIES
from src.indexing_pipeline import (
    delete_document as delete_indexed_document,
    list_indexed_documents,
)

from .auth import (
    CurrentUser,
    hash_password,
    require_admin,
    require_csrf_admin,
)
from .conversation_runtime import sweep_once
from .db import get_db
from .feedback import FEEDBACK_PATH
from .indexing import create_job, enqueue
from .schemas import (
    AdminConversationListResponse,
    AdminConversationSummaryDTO,
    AdminFeedbackEntry,
    AdminFeedbackResponse,
    AdminStatsResponse,
    AdminUserDTO,
    AdminUserListResponse,
    AdminUserPatchRequest,
    CategoryNodeDTO,
    CategoryTreeResponse,
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    IndexJobDTO,
    IndexJobListResponse,
    IndexedDocumentDTO,
    IndexedDocumentListResponse,
    SweepResponse,
    UploadResponse,
)

logger = logging.getLogger("api.routes_admin")

router = APIRouter(prefix="/admin", tags=["admin"])


# ── users ──────────────────────────────────────────────────────────────────


@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminUserListResponse:
    rows = conn.execute(
        """
        SELECT u.id, u.employee_id, u.real_name, u.role, u.is_active,
               u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id) AS conv_count
        FROM users u
        ORDER BY u.created_at DESC
        """
    ).fetchall()
    return AdminUserListResponse(
        users=[
            AdminUserDTO(
                id=r["id"],
                employee_id=r["employee_id"],
                real_name=r["real_name"],
                role=r["role"],
                is_active=bool(r["is_active"]),
                created_at=r["created_at"],
                last_login_at=r["last_login_at"],
                conversation_count=r["conv_count"],
            )
            for r in rows
        ]
    )


@router.patch("/users/{user_id}", response_model=AdminUserDTO)
def patch_user(
    user_id: int,
    body: AdminUserPatchRequest,
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminUserDTO:
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    updates: list[str] = []
    params: list = []
    if body.is_active is not None:
        if target["id"] == admin.id and not body.is_active:
            raise HTTPException(status_code=400, detail="不能停用当前管理员账号")
        updates.append("is_active = ?")
        params.append(1 if body.is_active else 0)
        # Stopping the user invalidates all their cookies so they can't keep
        # using the app from already-open tabs.
        if not body.is_active:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="role 必须是 user 或 admin")
        if target["id"] == admin.id and body.role != "admin":
            raise HTTPException(status_code=400, detail="不能取消当前管理员的权限")
        updates.append("role = ?")
        params.append(body.role)
    if body.reset_password is not None:
        if len(body.reset_password) < 6:
            raise HTTPException(status_code=400, detail="密码至少 6 位")
        updates.append("password_hash = ?")
        params.append(hash_password(body.reset_password))
        # Revoke all of the user's existing sessions on a password reset.
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))

    if updates:
        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    r = conn.execute(
        """
        SELECT u.id, u.employee_id, u.real_name, u.role, u.is_active,
               u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id) AS conv_count
        FROM users u WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()
    return AdminUserDTO(
        id=r["id"],
        employee_id=r["employee_id"],
        real_name=r["real_name"],
        role=r["role"],
        is_active=bool(r["is_active"]),
        created_at=r["created_at"],
        last_login_at=r["last_login_at"],
        conversation_count=r["conv_count"],
    )


# ── cross-user conversation browsing ───────────────────────────────────────


@router.get("/users/{user_id}/conversations", response_model=AdminConversationListResponse)
def list_user_conversations(
    user_id: int,
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminConversationListResponse:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.user_id, c.created_at, c.updated_at, c.turn_index,
               u.employee_id, u.real_name
        FROM conversations c
        JOIN users u ON u.id = c.user_id
        WHERE c.user_id = ?
        ORDER BY c.updated_at DESC
        """,
        (user_id,),
    ).fetchall()
    return AdminConversationListResponse(
        conversations=[
            AdminConversationSummaryDTO(
                id=r["id"],
                title=r["title"],
                user_id=r["user_id"],
                employee_id=r["employee_id"],
                real_name=r["real_name"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                turn_index=r["turn_index"],
            )
            for r in rows
        ]
    )


@router.get("/conversations", response_model=AdminConversationListResponse)
def list_all_conversations(
    limit: int = Query(200, ge=1, le=1000),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminConversationListResponse:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.user_id, c.created_at, c.updated_at, c.turn_index,
               u.employee_id, u.real_name
        FROM conversations c
        JOIN users u ON u.id = c.user_id
        ORDER BY c.updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return AdminConversationListResponse(
        conversations=[
            AdminConversationSummaryDTO(
                id=r["id"],
                title=r["title"],
                user_id=r["user_id"],
                employee_id=r["employee_id"],
                real_name=r["real_name"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                turn_index=r["turn_index"],
            )
            for r in rows
        ]
    )


# ── stats ──────────────────────────────────────────────────────────────────


@router.get("/stats", response_model=AdminStatsResponse)
def stats(
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminStatsResponse:
    cutoff = int(time.time()) - 7 * 24 * 60 * 60
    users_total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    users_active = conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_active = 1").fetchone()["n"]
    conv_total = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
    conv_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE updated_at >= ?", (cutoff,)
    ).fetchone()["n"]
    msg_total = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    msg_7d = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE created_at >= ?", (cutoff,)
    ).fetchone()["n"]
    return AdminStatsResponse(
        users_total=users_total,
        users_active=users_active,
        conversations_total=conv_total,
        conversations_7d=conv_7d,
        messages_total=msg_total,
        messages_7d=msg_7d,
    )


# ── feedback viewer ────────────────────────────────────────────────────────


@router.get("/feedback", response_model=AdminFeedbackResponse)
def feedback(
    limit: int = Query(200, ge=1, le=2000),
    _admin: CurrentUser = Depends(require_admin),
) -> AdminFeedbackResponse:
    path: Path = FEEDBACK_PATH
    if not path.exists():
        return AdminFeedbackResponse(entries=[], total=0)
    # Tail the file: load all lines, return the most recent `limit`.
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()
    total = len(lines)
    tail = lines[-limit:]
    entries: list[AdminFeedbackEntry] = []
    for line in reversed(tail):  # newest first
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        entries.append(AdminFeedbackEntry(**{
            k: d.get(k) for k in AdminFeedbackEntry.model_fields.keys()
        }))
    return AdminFeedbackResponse(entries=entries, total=total)


# ── manual sweep (admin-triggered, for tests + ops) ────────────────────────


@router.post("/sweep", response_model=SweepResponse)
def trigger_sweep(
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> SweepResponse:
    conv, sess = sweep_once()
    return SweepResponse(deleted_conversations=conv, deleted_auth_sessions=sess)


# ── indexing: upload + jobs + documents ────────────────────────────────────


# Filenames coming from the browser can carry path separators or shell-hostile
# chars. Reject anything that isn't a plain-ish name; admins can rename their
# files locally before uploading.
_SAFE_NAME_RE = re.compile(r"^[\w\-.一-鿿（）()【】\[\] ]+$")

# Cap individual uploads — MinerU cloud accepts ~200 MB per file. Tune via
# env if you regularly handle larger PDFs.
import os as _os
MAX_UPLOAD_BYTES = int(_os.getenv("MAX_UPLOAD_MB", "200")) * 1024 * 1024


def _job_row_to_dto(r: sqlite3.Row) -> IndexJobDTO:
    stats = {}
    if r["stats_json"]:
        try:
            stats = json.loads(r["stats_json"]) or {}
        except Exception:
            stats = {}
    return IndexJobDTO(
        id=r["id"],
        user_id=r["user_id"],
        employee_id=r["employee_id"] if "employee_id" in r.keys() else None,
        real_name=r["real_name"] if "real_name" in r.keys() else None,
        filename=r["filename"],
        category=r["category"],
        doc_type=r["doc_type"],
        source_path=r["source_path"],
        file_size=r["file_size"],
        status=r["status"],
        error=r["error"],
        parents=stats.get("parents"),
        children=stats.get("children"),
        created_at=r["created_at"],
        started_at=r["started_at"],
        finished_at=r["finished_at"],
    )


def _classify_doc_type(filename: str) -> str | None:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".md"):
        return "transcript"
    return None


@router.get("/index/category-tree", response_model=CategoryTreeResponse)
def category_tree(
    _admin: CurrentUser = Depends(require_admin),
) -> CategoryTreeResponse:
    """Walk `docs/` one level deep so the upload UI knows existing categories
    and (for two-level categories) the existing subcategories under each.

    Reads disk directly rather than asking Qdrant: a brand-new folder with
    no indexed content yet still shows up, and an admin can pick it as the
    destination for a fresh upload without first having to index something.
    """
    seen_categories: dict[str, list[str]] = {}
    if DOCS_DIR.exists():
        for top in sorted(DOCS_DIR.iterdir()):
            if not top.is_dir() or top.name.startswith("."):
                continue
            subs: list[str] = []
            for child in sorted(top.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    subs.append(child.name)
            seen_categories[top.name] = subs

    # Union of folder-derived names and the canonical two-level set so the
    # admin still sees 公司内部标准 / 客户标准 even before any subfolder exists.
    all_names = sorted(set(seen_categories.keys()) | set(SECOND_LEVEL_CATEGORIES))
    nodes: list[CategoryNodeDTO] = []
    for name in all_names:
        nodes.append(CategoryNodeDTO(
            name=name,
            two_level=name in SECOND_LEVEL_CATEGORIES,
            subcategories=seen_categories.get(name, []),
        ))
    return CategoryTreeResponse(
        categories=nodes,
        second_level_categories=sorted(SECOND_LEVEL_CATEGORIES),
    )


@router.post("/upload", response_model=UploadResponse)
async def upload_documents(
    files: list[UploadFile] = File(...),
    category: str = Form(...),
    subcategory: str = Form(""),
    admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> UploadResponse:
    """Accept one or more files for indexing. Each file becomes one job row.

    Files are written to `docs/<category>/[<subcategory>/]<filename>` (matches
    the manual ingest convention), then the corresponding job is enqueued.
    The single background worker drains the queue FIFO; concurrent uploads
    queue up instead of running in parallel.

    For categories in `SECOND_LEVEL_CATEGORIES` (currently 客户标准 and
    公司内部标准), `subcategory` is REQUIRED — the second-level folder
    is the customer name / company name and gets stored as the `company`
    field on each parent for downstream filtering.
    """
    cat = category.strip()
    if not cat or not _SAFE_NAME_RE.match(cat):
        raise HTTPException(status_code=400, detail="category 名称非法")

    sub = subcategory.strip()
    if cat in SECOND_LEVEL_CATEGORIES:
        if not sub:
            raise HTTPException(
                status_code=400,
                detail=f"分类「{cat}」需要指定子分类（客户名 / 公司名）",
            )
        if not _SAFE_NAME_RE.match(sub):
            raise HTTPException(status_code=400, detail="子分类名称非法")
    elif sub:
        # Don't silently accept an unused subcategory on a flat category —
        # surfaces typos and prevents the file landing somewhere unexpected.
        raise HTTPException(
            status_code=400,
            detail=f"分类「{cat}」不支持子分类",
        )

    category_dir = DOCS_DIR / cat / sub if sub else DOCS_DIR / cat
    category_dir.mkdir(parents=True, exist_ok=True)

    accepted: list[IndexJobDTO] = []
    skipped: list[dict] = []

    for uf in files:
        name = (uf.filename or "").strip()
        if not name or not _SAFE_NAME_RE.match(name):
            skipped.append({"filename": name or "(empty)", "reason": "文件名包含非法字符"})
            continue
        doc_type = _classify_doc_type(name)
        if doc_type is None:
            skipped.append({"filename": name, "reason": "仅支持 .pdf 和 .md (教学视频转写)"})
            continue

        target = category_dir / name
        # Stream to disk so we don't load the whole file into memory.
        total = 0
        try:
            with target.open("wb") as fh:
                while True:
                    chunk = await uf.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        # Stop early; clean up partial file.
                        fh.close()
                        if target.exists():
                            target.unlink()
                        skipped.append({
                            "filename": name,
                            "reason": f"文件超过 {MAX_UPLOAD_BYTES // (1024*1024)}MB 上限",
                        })
                        break
                    fh.write(chunk)
        except Exception as exc:
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            skipped.append({"filename": name, "reason": f"写入失败：{exc}"})
            continue
        if total > MAX_UPLOAD_BYTES:
            continue  # already skipped above

        job_id = create_job(
            user_id=admin.id,
            filename=name,
            category=cat,
            doc_type=doc_type,
            source_path=target,
            file_size=total,
        )
        enqueue(job_id)

        row = conn.execute(
            """
            SELECT j.*, u.employee_id, u.real_name
            FROM index_jobs j LEFT JOIN users u ON u.id = j.user_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
        accepted.append(_job_row_to_dto(row))

    return UploadResponse(accepted=accepted, skipped=skipped)


@router.get("/index/jobs", response_model=IndexJobListResponse)
def list_index_jobs(
    limit: int = Query(100, ge=1, le=1000),
    _admin: CurrentUser = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> IndexJobListResponse:
    rows = conn.execute(
        """
        SELECT j.*, u.employee_id, u.real_name
        FROM index_jobs j LEFT JOIN users u ON u.id = j.user_id
        ORDER BY j.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return IndexJobListResponse(jobs=[_job_row_to_dto(r) for r in rows])


@router.post("/index/jobs/{job_id}/retry", response_model=IndexJobDTO)
def retry_index_job(
    job_id: int,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> IndexJobDTO:
    row = conn.execute("SELECT status, source_path FROM index_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    if row["status"] not in ("failed", "done"):
        raise HTTPException(status_code=400, detail="只有失败或已完成的任务可以重试")
    if not Path(row["source_path"]).exists():
        raise HTTPException(status_code=400, detail="源文件已不存在，请重新上传")
    conn.execute(
        "UPDATE index_jobs SET status='pending', error=NULL, "
        "stats_json=NULL, started_at=NULL, finished_at=NULL "
        "WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    enqueue(job_id)
    row = conn.execute(
        """
        SELECT j.*, u.employee_id, u.real_name
        FROM index_jobs j LEFT JOIN users u ON u.id = j.user_id
        WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()
    return _job_row_to_dto(row)


@router.delete("/index/jobs/{job_id}", status_code=204)
def delete_index_job(
    job_id: int,
    _admin: CurrentUser = Depends(require_csrf_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    row = conn.execute("SELECT status FROM index_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    if row["status"] not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="进行中的任务不能删除")
    conn.execute("DELETE FROM index_jobs WHERE id = ?", (job_id,))
    conn.commit()


@router.get("/index/documents", response_model=IndexedDocumentListResponse)
def list_documents(
    _admin: CurrentUser = Depends(require_admin),
) -> IndexedDocumentListResponse:
    docs = list_indexed_documents()
    return IndexedDocumentListResponse(
        documents=[
            IndexedDocumentDTO(
                source_path=d.source_path,
                doc_title=d.doc_title,
                category=d.category,
                doc_type=d.doc_type,
                company=d.company,
                parent_count=d.parent_count,
            )
            for d in docs
        ]
    )


@router.delete("/index/documents", response_model=DeleteDocumentResponse)
def delete_document(
    body: DeleteDocumentRequest,
    _admin: CurrentUser = Depends(require_csrf_admin),
) -> DeleteDocumentResponse:
    result = delete_indexed_document(body.source_path, delete_file=body.delete_file)
    return DeleteDocumentResponse(
        parents_deleted=result["parents_deleted"],
        file_deleted=bool(result["file_deleted"]),
    )
