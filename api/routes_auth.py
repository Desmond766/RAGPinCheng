"""Auth endpoints — register, login, logout, me."""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response

from .auth import (
    COOKIE_NAME,
    CurrentUser,
    clear_session_cookie,
    hash_password,
    issue_session,
    require_user,
    revoke_session,
    set_session_cookie,
    verify_password,
)
from .db import get_db
from .schemas import AuthMeResponse, LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_dto(row: sqlite3.Row, csrf_token: str) -> AuthMeResponse:
    return AuthMeResponse(
        id=row["id"],
        employee_id=row["employee_id"],
        real_name=row["real_name"],
        role=row["role"],
        csrf_token=csrf_token,
    )


@router.post("/register", response_model=AuthMeResponse)
def register(
    body: RegisterRequest,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
) -> AuthMeResponse:
    emp = body.employee_id.strip()
    name = body.real_name.strip()
    pw = body.password
    if not emp or not name or not pw:
        raise HTTPException(status_code=400, detail="所有字段必填")
    if len(pw) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    existing = conn.execute(
        "SELECT id FROM users WHERE employee_id = ?", (emp,)
    ).fetchone()
    if existing is not None:
        raise HTTPException(status_code=409, detail="该工号已注册")

    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO users (employee_id, real_name, password_hash, role, "
        "is_active, created_at, last_login_at) "
        "VALUES (?, ?, ?, 'user', 1, ?, ?)",
        (emp, name, hash_password(pw), now, now),
    )
    user_id = cur.lastrowid
    conn.commit()

    sid, csrf, _ = issue_session(conn, user_id)
    set_session_cookie(response, sid)
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_dto(row, csrf)


@router.post("/login", response_model=AuthMeResponse)
def login(
    body: LoginRequest,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
) -> AuthMeResponse:
    emp = body.employee_id.strip()
    row = conn.execute(
        "SELECT * FROM users WHERE employee_id = ?", (emp,)
    ).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="工号或密码错误")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="账号已停用")

    now = int(time.time())
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
    conn.commit()

    sid, csrf, _ = issue_session(conn, row["id"])
    set_session_cookie(response, sid)
    return _user_dto(row, csrf)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    pc_sid: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    if pc_sid:
        revoke_session(conn, pc_sid)
    clear_session_cookie(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=AuthMeResponse)
def me(user: CurrentUser = Depends(require_user)) -> AuthMeResponse:
    return AuthMeResponse(
        id=user.id,
        employee_id=user.employee_id,
        real_name=user.real_name,
        role=user.role,
        csrf_token=user.csrf_token,
    )
