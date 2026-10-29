"""監視モード: 指定フォルダに置かれた注文書を自動で取り込む。

ファイルの書き込みが終わる(サイズが一定時間変わらない)のを待ってから取り込み、
取込後は設定に従って _eagle_done フォルダへ移動(または残す/削除)する。
ネットワーク共有などファイル変更通知が届かない場所は「ポーリング」を有効にする。
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from ..db import log_event
from ..settings import get_settings
from .ingest import UnsupportedFile, ingest_path

LOG = logging.getLogger("eagle.watch")

DONE_DIR = "_eagle_done"
ERROR_DIR = "_eagle_error"
TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".partial", ".download")


def _ignored(path: Path, extensions: set[str]) -> bool:
    if any(part in (DONE_DIR, ERROR_DIR) for part in path.parts):
        return True
    name = path.name
    if name.startswith(("~$", ".")) or name.lower().endswith(TEMP_SUFFIXES):
        return True
    return path.suffix.lower() not in extensions


def wait_until_stable(path: Path, timeout: float = 60.0, interval: float = 0.7) -> bool:
    last, stable_since = -1, None
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            size = path.stat().st_size
            with open(path, "rb"):
                pass   # 他プロセスが書込中で開けない場合は例外
        except (FileNotFoundError, PermissionError, OSError):
            time.sleep(interval)
            continue
        if size == last and size > 0:
            if stable_since and time.time() - stable_since >= 1.0:
                return True
            stable_since = stable_since or time.time()
        else:
            last, stable_since = size, None
        time.sleep(interval)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, svc: "WatchService", root: Path):
        self.svc, self.root = svc, root

    def on_created(self, event):
        if not event.is_directory:
            self.svc.submit(Path(event.src_path), self.root)

    def on_moved(self, event):
        if not event.is_directory:
            self.svc.submit(Path(event.dest_path), self.root)


class WatchService:
    def __init__(self) -> None:
        self._observers: list = []
        self._pending: dict[str, Path] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._running = False
        self.processed = 0
        self.errors: list[str] = []
        self.folders: list[dict] = []
        self._extensions: set[str] = set()

    def submit(self, path: Path, root: Path) -> None:
        if _ignored(path, self._extensions):
            return
        with self._lock:
            self._pending[str(path)] = root
        self._wake.set()

    def _loop(self) -> None:
        while self._running:
            self._wake.wait(timeout=2.0)
            self._wake.clear()
            with self._lock:
                batch = list(self._pending.items())
                self._pending.clear()
            for p, root in batch:
                if self._running:
                    self._handle(Path(p), root)

    def _handle(self, path: Path, root: Path) -> None:
        if not path.exists() or not wait_until_stable(path):
            return
        after = str(get_settings()["watch"].get("after") or "move")
        try:
            res = ingest_path(path, source="watch")
            self.processed += 1
            log_event(f"監視フォルダから取込: {path}", "watch", document_id=res.get("id"))
            self._after(path, root, after, DONE_DIR)
        except UnsupportedFile as exc:
            LOG.info("監視: 対象外のファイル %s (%s)", path, exc)
        except Exception as exc:  # noqa: BLE001
            msg = f"{path.name}: {exc}"
            self.errors = (self.errors + [msg])[-20:]
            LOG.exception("監視: 取込に失敗 %s", path)
            log_event(f"監視フォルダの取込に失敗: {msg}", "watch", "error")
            self._after(path, root, "move", ERROR_DIR)

    @staticmethod
    def _after(path: Path, root: Path, after: str, sub: str) -> None:
        try:
            if after == "delete" and sub == DONE_DIR:
                path.unlink(missing_ok=True)
            elif after == "move":
                dest_dir = root / sub
                dest_dir.mkdir(exist_ok=True)
                dest = dest_dir / path.name
                if dest.exists():
                    dest = dest_dir / f"{path.stem}_{time.strftime('%Y%m%d%H%M%S')}{path.suffix}"
                shutil.move(str(path), str(dest))
        except OSError as exc:
            LOG.warning("監視: 取込後の移動に失敗 %s: %s", path, exc)

    # ── 開始・停止 ───────────────────────────────────────────────────
    def start(self) -> dict:
        self.stop()
        cfg = get_settings()["watch"]
        self._extensions = {e.lower() if e.startswith(".") else "." + e.lower() for e in cfg.get("extensions") or []}
        self.folders = []
        self._running = True
        self._worker = threading.Thread(target=self._loop, name="eagle-watch", daemon=True)
        self._worker.start()
        for f in cfg.get("folders") or []:
            path = Path(str(f.get("path") or "")).expanduser()
            entry = {"path": str(path), "recursive": bool(f.get("recursive")), "polling": bool(f.get("polling")),
                     "ok": False, "error": ""}
            if not str(f.get("path") or "").strip() or not path.is_dir():
                entry["error"] = "フォルダが見つかりません"
                self.folders.append(entry)
                continue
            try:
                obs = PollingObserver(timeout=5) if entry["polling"] else Observer()
                obs.schedule(_Handler(self, path), str(path), recursive=entry["recursive"])
                obs.start()
                self._observers.append(obs)
                entry["ok"] = True
                # 監視開始前から置かれていたファイルも取り込む
                pattern = "**/*" if entry["recursive"] else "*"
                for p in sorted(path.glob(pattern)):
                    if p.is_file():
                        self.submit(p, path)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
            self.folders.append(entry)
        log_event(f"監視を開始しました({sum(1 for f in self.folders if f['ok'])}フォルダ)", "watch")
        return self.status()

    def stop(self) -> dict:
        was = self._running
        self._running = False
        self._wake.set()
        for obs in self._observers:
            try:
                obs.stop()
                obs.join(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self._observers = []
        if was:
            log_event("監視を停止しました", "watch")
        return self.status()

    def status(self) -> dict:
        with self._lock:
            pending = len(self._pending)
        return {"running": self._running, "folders": self.folders, "pending": pending,
                "processed": self.processed, "errors": self.errors[-10:]}


watch_service = WatchService()
