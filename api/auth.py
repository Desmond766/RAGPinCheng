"""Auth primitives — passwords, cookie sessions, FastAPI dependencies.

Wire model:
- Login / register issues a row in `auth_sessions`; the cookie value IS the
  primary key (random 32-byte hex). Revocation = delete the row.
- Cookie is `HttpOnly; SameSite=Lax; Path=/api`; `Secure` is on unless
  `SESSION_COOKIE_SECURE=false` (for plain-HTTP local dev).
- CSRF: a separate random token is stored in the auth_sessions row and
  returned by `/auth/me`. Mutating requests (POST/PATCH/DELETE) must echo
  it in the `X-CSRF-Token` header.
"""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Response, status
from passlib.context import CryptContext

from .db import connect, get_db

logger = logging.getLogger("api.auth")

COOKIE_NAME = "pc_sid"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days; matches retention window
COOKIE_PATH = "/api"

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def _cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false"


# ── password hashing ────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except Exception:
        return False


# ── session lifecycle ───────────────────────────────────────────────────────


@dataclass
class CurrentUser:
    id: int
    employee_id: str
    real_name: str
    role: str
    csrf_token: str


def issue_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str, int]:
    """Insert a new auth_sessions row. Returns (session_id, csrf_token, expires_at)."""
    sid = secrets.token_hex(32)
    csrf = secrets.token_hex(32)
    now = int(time.time())
    expires = now + SESSION_TTL_SECONDS
    conn.execute(
        "INSERT INTO auth_sessions (id, user_id, csrf_token, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, user_id, csrf, now, expires),
    )
    conn.commit()
    return sid, csrf, expires


def revoke_session(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE id = ?", (sid,))
    conn.commit()


def set_session_cookie(resp: Response, sid: str) -> None:
    resp.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path=COOKIE_PATH,
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(key=COOKIE_NAME, path=COOKIE_PATH)


# ── FastAPI dependencies ────────────────────────────────────────────────────


def _load_user(conn: sqlite3.Connection, sid: Optional[str]) -> Optional[CurrentUser]:
    if not sid:
        return None
    row = conn.execute(
        """
        SELECT s.csrf_token, s.expires_at, u.id, u.employee_id, u.real_name,
               u.role, u.is_active
        FROM auth_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (sid,),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] < int(time.time()):
        # Best-effort cleanup; sweeper would catch it eventually.
        conn.execute("DELETE FROM auth_sessions WHERE id = ?", (sid,))
        conn.commit()
        return None
    if not row["is_active"]:
        return None
    return CurrentUser(
        id=row["id"],
        employee_id=row["employee_id"],
        real_name=row["real_name"],
        role=row["role"],
        csrf_token=row["csrf_token"],
    )


def optional_user(
    pc_sid: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    conn: sqlite3.Connection = Depends(get_db),
) -> Optional[CurrentUser]:
    return _load_user(conn, pc_sid)


def require_user(
    pc_sid: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    conn: sqlite3.Connection = Depends(get_db),
) -> CurrentUser:
    user = _load_user(conn, pc_sid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return user


def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user


def require_csrf(
    user: CurrentUser = Depends(require_user),
    x_csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
) -> CurrentUser:
    """Use on POST/PATCH/DELETE handlers to require a matching CSRF token header."""
    if not x_csrf_token or not secrets.compare_digest(x_csrf_token, user.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf token invalid")
    return user


def require_csrf_admin(user: CurrentUser = Depends(require_csrf)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user


# ── admin bootstrap ─────────────────────────────────────────────────────────


def bootstrap_admin_from_env() -> None:
    """If ADMIN_EMPLOYEE_ID + ADMIN_PASSWORD are set and no admin exists yet,
    seed one. Idempotent: subsequent boots with the same env do nothing once
    an admin row is present.
    """
    emp = os.getenv("ADMIN_EMPLOYEE_ID")
    pw = os.getenv("ADMIN_PASSWORD")
    if not emp or not pw:
        return
    conn = connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin'").fetchone()
        if row["n"] > 0:
            return
        name = os.getenv("ADMIN_REAL_NAME") or "管理员"
        now = int(time.time())
        # If an account with this employee_id already exists, promote it
        # rather than failing on the UNIQUE constraint.
        existing = conn.execute(
            "SELECT id FROM users WHERE employee_id = ?", (emp,)
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE users SET role='admin', is_active=1 WHERE id = ?",
                (existing["id"],),
            )
            logger.info("promoted existing user '%s' to admin", emp)
        else:
            conn.execute(
                "INSERT INTO users (employee_id, real_name, password_hash, role, "
                "is_active, created_at) VALUES (?, ?, ?, 'admin', 1, ?)",
                (emp, name, hash_password(pw), now),
            )
            logger.info("admin user '%s' bootstrapped", emp)
        conn.commit()
    finally:
        conn.close()
