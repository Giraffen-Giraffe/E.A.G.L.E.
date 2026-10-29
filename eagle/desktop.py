"""アプリ版(デスクトップ)。1台の PC の中だけで完結する。

API サーバーを 127.0.0.1 で起動し、専用ウィンドウ(pywebview / WebView2)で画面を開く。
pywebview が使えない環境では、Edge / Chrome のアプリモード、最後は既定のブラウザで開く。
ウィンドウを閉じるとサーバーと llama-server も終了する。
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

from . import APP_NAME
from .api.app import create_app
from .config import BootConfig

LOG = logging.getLogger("eagle.desktop")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _existing_instance(port: int) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.5)
        return r.status_code == 200 and r.json().get("mode") == "desktop"
    except (httpx.HTTPError, ValueError):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _app_mode_browser() -> str | None:
    cands = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    return next((c for c in cands if c and os.path.exists(c)), None)


def _open_window(url: str, browser: bool) -> bool:
    """専用ウィンドウを開き、閉じられるまで待つ。待てない方式で開いた場合は False。"""
    if not browser:
        try:
            import webview  # pywebview
            webview.create_window(APP_NAME, url, width=1440, height=920, min_size=(1024, 680))
            webview.start()
            return True
        except Exception as exc:  # noqa: BLE001
            LOG.info("専用ウィンドウを開けないためブラウザで開きます: %s", exc)
    exe = _app_mode_browser()
    if exe:
        subprocess.Popen([exe, f"--app={url}", "--new-window"])
    else:
        webbrowser.open(url)
    return False


def run_desktop(cfg: BootConfig, browser: bool = False) -> int:
    port = cfg.port
    if not _port_free(port):
        if _existing_instance(port):
            LOG.info("既に起動しています。ウィンドウだけ開きます")
            _open_window(f"http://127.0.0.1:{port}/", browser)
            return 0
        port = _free_port()
    cfg.host, cfg.port = "127.0.0.1", port
    app = create_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    th = threading.Thread(target=server.run, name="eagle-server", daemon=True)
    th.start()
    url = f"http://127.0.0.1:{port}/"
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.1)
    LOG.info("%s を開きます: %s", APP_NAME, url)
    print(f"{APP_NAME} を起動しました: {url}  (終了するにはウィンドウを閉じるか Ctrl+C)")
    waited = _open_window(url, browser)
    try:
        if not waited:
            while th.is_alive():
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    server.should_exit = True
    th.join(timeout=20)
    return 0
