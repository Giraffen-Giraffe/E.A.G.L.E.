"""利用者認証(サーバー版)。デスクトップ版では認証を行わない(127.0.0.1 のみで待ち受け)。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import timedelta

from ..db import SessionToken, User, session_scope, utcnow

ROLES = ("admin", "editor", "viewer")
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}
PBKDF2_ITER = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return "pbkdf2_sha256${}${}${}".format(PBKDF2_ITER, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(it))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("パスワードは8文字以上にしてください")


def create_user(username: str, password: str, role: str = "editor", display_name: str = "") -> int:
    username = (username or "").strip()
    if not username:
        raise ValueError("ユーザー名を入力してください")
    if role not in ROLES:
        raise ValueError(f"権限は {', '.join(ROLES)} のいずれかです")
    validate_password(password)
    with session_scope() as s:
        if s.query(User).filter(User.username == username).first():
            raise ValueError("同じユーザー名が既にあります")
        u = User(username=username, display_name=display_name or username, password_hash=hash_password(password),
                 role=role)
        s.add(u)
        s.flush()
        return u.id


def user_count() -> int:
    with session_scope() as s:
        return s.query(User).count()


# ── ログイン試行の制限(総当たり対策) ─────────────────────────────────────
_fail: dict[str, list[float]] = {}
_fail_lock = threading.Lock()
MAX_FAILS, WINDOW_SEC = 5, 300


def _locked(key: str) -> bool:
    now = time.time()
    with _fail_lock:
        hits = [t for t in _fail.get(key, []) if now - t < WINDOW_SEC]
        _fail[key] = hits
        return len(hits) >= MAX_FAILS


def _note_fail(key: str) -> None:
    with _fail_lock:
        _fail.setdefault(key, []).append(time.time())


def login(username: str, password: str, client_ip: str, hours: int) -> tuple[str, dict]:
    key = f"{(username or '').lower()}|{client_ip}"
    if _locked(key):
        raise PermissionError("ログイン失敗が続いたため一時的にロックしています。5分後に再試行してください")
    with session_scope() as s:
        u = s.query(User).filter(User.username == (username or "").strip()).first()
        if u is None or not u.active or not verify_password(password or "", u.password_hash):
            _note_fail(key)
            raise PermissionError("ユーザー名またはパスワードが違います")
        token = secrets.token_urlsafe(32)
        s.add(SessionToken(token_hash=_hash_token(token), user_id=u.id, expires_at=utcnow() + timedelta(hours=hours)))
        u.last_login = utcnow()
        # 期限切れセッションの掃除
        s.query(SessionToken).filter(SessionToken.expires_at < utcnow()).delete()
        return token, {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def user_for_token(token: str) -> dict | None:
    if not token:
        return None
    with session_scope() as s:
        st = s.get(SessionToken, _hash_token(token))
        if st is None or st.expires_at < utcnow():
            return None
        u = s.get(User, st.user_id)
        if u is None or not u.active:
            return None
        return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role}


def logout(token: str) -> None:
    with session_scope() as s:
        s.query(SessionToken).filter(SessionToken.token_hash == _hash_token(token)).delete()


def revoke_user_sessions(user_id: int) -> None:
    with session_scope() as s:
        s.query(SessionToken).filter(SessionToken.user_id == user_id).delete()
