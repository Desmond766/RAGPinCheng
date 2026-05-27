"""Admin endpoints — user management, cross-user conversation read,
system stats, feedback-log viewer, manual sweep.

All endpoints require an authenticated user with role='admin'. Read endpoints
use `require_admin`; mutating endpoints add the CSRF check via
`require_csrf_admin`.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import (
    CurrentUser,
    hash_password,
    require_admin,
    require_csrf_admin,
)
from .conversation_runtime import sweep_once
from .db import get_db
from .feedback import FEEDBACK_PATH
from .schemas import (
    AdminConversationListResponse,
    AdminConversationSummaryDTO,
    AdminFeedbackEntry,
    AdminFeedbackResponse,
    AdminStatsResponse,
    AdminUserDTO,
    AdminUserListResponse,
    AdminUserPatchRequest,
    SweepResponse,
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
