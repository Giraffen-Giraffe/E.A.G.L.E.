"""API 共通: 認証・権限・CSRF 対策。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ..config import boot
from ..services import auth

SESSION_COOKIE = "eagle_session"
CSRF_HEADER = "x-eagle-request"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_USER = {"id": 0, "username": "local", "display_name": "ローカル", "role": "admin"}


def current_user(request: Request) -> dict:
    # 書き込み系の要求には独自ヘッダを必須にする。他サイトのフォーム/スクリプトからは
    # CORS の事前確認を通らないため付けられない(CSRF 対策)。
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "不正な要求です(X-Eagle-Request ヘッダがありません)")
    if not boot().auth_required:
        return LOCAL_USER
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            token = authz[7:].strip()
    user = auth.user_for_token(token)
    if user is None:
        raise HTTPException(401, "ログインが必要です")
    return user


def require(role: str):
    need = auth.ROLE_RANK[role]

    def _dep(user: dict = Depends(current_user)) -> dict:
        if auth.ROLE_RANK.get(user["role"], -1) < need:
            raise HTTPException(403, "この操作の権限がありません")
        return user
    return _dep


viewer = require("viewer")
editor = require("editor")
admin = require("admin")
