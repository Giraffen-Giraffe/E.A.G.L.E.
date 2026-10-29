"""FastAPI アプリの組み立て(デスクトップ版/サーバー版 共通)。"""
from __future__ import annotations

import logging
import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import APP_NAME, __version__
from ..config import BootConfig, set_boot
from ..db import init_db
from ..services.external_db import auto_push
from ..services.jobs import worker
from ..services.watcher import watch_service
from ..settings import get_settings
from ..vlm import llama_manager
from . import admin, documents

LOG = logging.getLogger("eagle")
STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"

# Windows では拡張子の MIME 型をレジストリから読むため、.js が text/plain になる環境がある。
# その場合ブラウザが画面のスクリプト(ES モジュール)を実行しないので、明示的に登録する。
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
                    (".svg", "image/svg+xml"), (".json", "application/json"), (".woff2", "font/woff2")):
    mimetypes.add_type(_type, _ext)

FALLBACK_HTML = """<!doctype html><meta charset="utf-8"><title>E.A.G.L.E.</title>
<body style="font-family:sans-serif;padding:2rem"><h1>E.A.G.L.E.</h1>
<p>画面ファイルがビルドされていません。<code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code> を実行してください。</p>
<p>API は <a href="/docs">/docs</a> で確認できます。</p></body>"""


def _on_finished(doc_id: int, status: str) -> None:
    if status in ("extracted", "needs_review"):
        auto_push(doc_id, "extract")


def startup(cfg: BootConfig) -> None:
    init_db(cfg.database_url)
    st = get_settings()
    mgr = llama_manager()
    mgr.configure(st["vlm"]["llama"])
    if _on_finished not in worker.on_finished:
        worker.on_finished.append(_on_finished)
    worker.start()
    if st["watch"].get("enabled"):
        watch_service.start()
    if st["vlm"]["backend"] == "llama_cpp" and st["vlm"]["llama"].get("autostart", True):
        # モデルの読込には時間がかかるため、起動直後から裏で準備しておく
        threading.Thread(target=mgr.start, daemon=True).start()


def shutdown() -> None:
    watch_service.stop()
    worker.stop()
    llama_manager().stop()


def create_app(cfg: BootConfig) -> FastAPI:
    set_boot(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        startup(cfg)
        LOG.info("%s %s 起動 (%s モード / data=%s)", APP_NAME, __version__, cfg.mode, cfg.data_dir)
        yield
        shutdown()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan, docs_url="/docs", redoc_url=None)

    allowed_hosts = {"127.0.0.1", "localhost", "[::1]"}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # デスクトップ版は 127.0.0.1 でしか待ち受けない。DNS リバインディング対策として Host も確認する。
        if cfg.mode == "desktop":
            host = (request.headers.get("host") or "").rsplit(":", 1)[0]
            if host not in allowed_hosts:
                return JSONResponse({"detail": "invalid host"}, status_code=400)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    app.include_router(documents.router)
    app.include_router(admin.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "mode": cfg.mode}

    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        f = (STATIC_DIR / path).resolve()
        if path and f.is_file() and STATIC_DIR.resolve() in f.parents:
            return FileResponse(f)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(FALLBACK_HTML)

    return app
