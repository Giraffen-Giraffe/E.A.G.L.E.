"""Webアプリ版(サーバー)。LAN 内で共有し、各自のブラウザから利用する。

ログインが必須。初回は管理者を作成する(対話入力、または環境変数 EAGLE_ADMIN_USER /
EAGLE_ADMIN_PASSWORD、または `python -m eagle user add 名前 --role admin`)。
"""
from __future__ import annotations

import getpass
import logging
import socket
import sys

import uvicorn

from . import APP_NAME
from .api.app import create_app
from .config import BootConfig, set_boot
from .db import init_db

LOG = logging.getLogger("eagle.server")


def _ensure_admin(cfg: BootConfig) -> None:
    from .services import auth
    if auth.user_count() > 0:
        return
    if cfg.admin_user and cfg.admin_password:
        auth.create_user(cfg.admin_user, cfg.admin_password, "admin")
        LOG.info("管理者 %s を作成しました(環境変数)", cfg.admin_user)
        return
    if sys.stdin is not None and sys.stdin.isatty():
        print("=== 初回設定: 管理者アカウントを作成します ===")
        while True:
            name = input("管理者のユーザー名 [admin]: ").strip() or "admin"
            pw = getpass.getpass("パスワード(8文字以上): ")
            if pw != getpass.getpass("パスワード(確認): "):
                print("パスワードが一致しません")
                continue
            try:
                auth.create_user(name, pw, "admin")
                print(f"管理者 {name} を作成しました")
                return
            except ValueError as exc:
                print("エラー:", exc)
    LOG.warning("利用者がいません。`python -m eagle user add admin --role admin` で管理者を作成してください")


def _lan_addresses() -> list[str]:
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in out if not a.startswith("127."))


def run_server(cfg: BootConfig) -> int:
    set_boot(cfg)
    init_db(cfg.database_url)
    _ensure_admin(cfg)
    app = create_app(cfg)
    scheme = "https" if cfg.ssl_certfile else "http"
    print(f"{APP_NAME} サーバーを起動します")
    if cfg.host in ("0.0.0.0", "::"):
        for a in _lan_addresses() or ["127.0.0.1"]:
            print(f"  {scheme}://{a}:{cfg.port}/")
    else:
        print(f"  {scheme}://{cfg.host}:{cfg.port}/")
    if not cfg.ssl_certfile:
        print("  ※ インターネットから到達できる環境では HTTPS(ssl_certfile / ssl_keyfile)かリバースプロキシを使ってください")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info", log_config=None, proxy_headers=True,
                ssl_certfile=cfg.ssl_certfile or None, ssl_keyfile=cfg.ssl_keyfile or None)
    return 0
