"""同梱 llama.cpp (llama-server) の起動・停止・状態管理。

bin/llama/ に llama-server、models/ に GGUF(本体 + mmproj)を置くと、アプリが
127.0.0.1 の専用ポートで llama-server を起動し、OpenAI 互換 API として利用する。
外部へは待ち受けないため、書類画像が端末の外に出ることはない。
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import httpx

from .client import VlmUnavailable

LOG = logging.getLogger("eagle.llama")

EXE_NAMES = ("llama-server.exe", "llama-server")


_job_handle = None


def _bind_to_parent(proc: subprocess.Popen) -> None:
    """Windows: アプリが強制終了されても llama-server が残らない(GPU メモリを掴み続けない)ように、
    「親が終われば子も終わる」ジョブオブジェクトに入れる。"""
    global _job_handle
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job_handle is None:
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
            _job_handle = job
        k32.AssignProcessToJobObject(wintypes.HANDLE(_job_handle), wintypes.HANDLE(int(proc._handle)))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001  紐付けできなくても動作は続ける
        LOG.debug("llama-server をジョブオブジェクトに入れられませんでした: %s", exc)


def find_server_binary(bin_dir: Path) -> Path | None:
    if not bin_dir.exists():
        return None
    for name in EXE_NAMES:
        p = bin_dir / name
        if p.exists():
            return p
    for depth_glob in ("*/", "*/*/", "*/*/*/"):
        for name in EXE_NAMES:
            hits = sorted(bin_dir.glob(depth_glob + name))
            if hits:
                return hits[0]
    return None


def list_models(models_dir: Path) -> dict:
    models, projectors = [], []
    if models_dir.exists():
        for p in sorted(models_dir.rglob("*.gguf")):
            item = {"name": str(p.relative_to(models_dir)).replace("\\", "/"), "size": p.stat().st_size}
            (projectors if p.name.lower().startswith("mmproj") else models).append(item)
    return {"models": models, "mmproj": projectors}


def _tokens(name: str) -> set[str]:
    import re
    base = re.sub(r"\.gguf$", "", name.lower()).replace("mmproj-", "")
    return {t for t in re.split(r"[-_.]", base) if t and not re.fullmatch(r"q\d.*|f16|f32|bf16|iq\d.*", t)}


def auto_select(models_dir: Path, model: str = "", mmproj: str = "") -> tuple[Path | None, Path | None]:
    found = list_models(models_dir)
    m = (models_dir / model) if model else None
    if m is None or not m.exists():
        m = (models_dir / found["models"][0]["name"]) if found["models"] else None
    p = (models_dir / mmproj) if mmproj else None
    if (p is None or not p.exists()) and m is not None and found["mmproj"]:
        mt = _tokens(m.name)
        best = max(found["mmproj"], key=lambda it: len(mt & _tokens(Path(it["name"]).name)))
        p = models_dir / best["name"]
    return m, p


class LlamaServerManager:
    def __init__(self, bin_dir: Path, models_dir: Path, log_dir: Path):
        # llama-server は実行ファイルのフォルダで起動するため、相対パスは使えない
        self.bin_dir = Path(bin_dir).resolve()
        self.models_dir = Path(models_dir).resolve()
        self.log_path = log_dir / "llama-server.log"
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"        # stopped / starting / ready / external / error
        self.error = ""
        self.model: Path | None = None
        self.mmproj: Path | None = None
        self.port = 0
        self.started_at = 0.0
        self._lock = threading.RLock()
        self._settings: dict = {}
        self._tail: deque[str] = deque(maxlen=60)

    def configure(self, llama_settings: dict) -> None:
        with self._lock:
            self._settings = dict(llama_settings or {})

    def current_model(self) -> str:
        return self.model.name if self.model else ""

    # ── 状態 ─────────────────────────────────────────────────────────
    def _healthy(self, port: int) -> bool:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def status(self) -> dict:
        with self._lock:
            if self.proc is not None and self.proc.poll() is not None and self.state in ("starting", "ready"):
                self.state = "error"
                self.error = self.error or f"llama-server が終了しました(code={self.proc.returncode})"
            binary = find_server_binary(self.bin_dir)
            return {
                "state": self.state,
                "error": self.error,
                "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
                "port": self.port or int(self._settings.get("port", 0) or 0),
                "model": self.model.name if self.model else "",
                "mmproj": self.mmproj.name if self.mmproj else "",
                "binary": str(binary) if binary else "",
                "binary_found": binary is not None,
                "models_dir": str(self.models_dir),
                "bin_dir": str(self.bin_dir),
                "available": list_models(self.models_dir),
                "log_tail": list(self._tail)[-20:],
                "uptime_sec": round(time.time() - self.started_at, 1) if self.state == "ready" else 0,
            }

    # ── 起動・停止 ───────────────────────────────────────────────────
    def start(self) -> dict:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return self.status()
            s = self._settings
            port = int(s.get("port") or 18780)
            self.port = port
            if self._healthy(port):
                # 既に同じポートで llama-server が動いている(手動起動・別プロセス)ならそれを使う
                self.state, self.error = "external", ""
                return self.status()
            binary = find_server_binary(self.bin_dir)
            if binary is None:
                self.state = "error"
                self.error = f"llama-server が見つかりません({self.bin_dir})。`python -m eagle setup-vlm` を実行してください"
                return self.status()
            model, mmproj = auto_select(self.models_dir, str(s.get("model") or ""), str(s.get("mmproj") or ""))
            if model is None:
                self.state = "error"
                self.error = f"GGUF モデルがありません({self.models_dir})。`python -m eagle setup-vlm` を実行してください"
                return self.status()
            if mmproj is None:
                self.state = "error"
                self.error = "画像エンコーダ(mmproj-*.gguf)がありません。画像を読むには mmproj が必要です"
                return self.status()
            args = [
                str(binary), "-m", str(model), "--mmproj", str(mmproj),
                "--host", "127.0.0.1", "--port", str(port),
                "-c", str(int(s.get("ctx") or 16384)),
                "-ngl", str(int(s.get("gpu_layers") if s.get("gpu_layers") is not None else 999)),
                "--parallel", "1",
                "--no-webui",
            ]
            threads = int(s.get("threads") or 0)
            if threads > 0:
                args += ["-t", str(threads)]
            extra = str(s.get("extra_args") or "").strip()
            if extra:
                args += shlex.split(extra, posix=(os.name != "nt"))
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            LOG.info("llama-server を起動します: %s", " ".join(args))
            self._tail.clear()
            self.proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(binary.parent),
                creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            _bind_to_parent(self.proc)
            self.model, self.mmproj = model, mmproj
            self.state, self.error = "starting", ""
            self.started_at = time.time()
            threading.Thread(target=self._pump_logs, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._watch_ready, args=(self.proc, port), daemon=True).start()
            return self.status()

    def _pump_logs(self, proc: subprocess.Popen) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as log:
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    self._tail.append(line.rstrip())
        except Exception:  # noqa: BLE001
            pass

    def _watch_ready(self, proc: subprocess.Popen, port: int) -> None:
        deadline = time.time() + 900
        while time.time() < deadline:
            if proc.poll() is not None:
                with self._lock:
                    if self.proc is proc:
                        self.state = "error"
                        tail = " / ".join(list(self._tail)[-5:])
                        self.error = f"llama-server が起動に失敗しました(code={proc.returncode}) {tail}"
                return
            if self._healthy(port):
                with self._lock:
                    if self.proc is proc:
                        self.state = "ready"
                        LOG.info("llama-server 準備完了 (%s, port=%d, %.1fs)",
                                 self.current_model(), port, time.time() - self.started_at)
                return
            time.sleep(1.0)
        with self._lock:
            if self.proc is proc:
                self.state, self.error = "error", "llama-server の起動がタイムアウトしました"

    def stop(self) -> dict:
        with self._lock:
            proc = self.proc
            self.proc = None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self.state = "stopped" if self.state != "external" else "stopped"
            self.error = ""
            return self.status()

    def restart(self) -> dict:
        self.stop()
        return self.start()

    def ensure_ready(self, timeout: float = 900.0) -> int:
        """推論に使えるポートを返す。止まっていれば起動し、準備完了まで待つ。"""
        st = self.status()
        if st["state"] in ("stopped", "error"):
            st = self.start()
            if st["state"] == "error":
                raise VlmUnavailable(st["error"])
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                state, err, port = self.state, self.error, self.port
            if state in ("ready", "external"):
                return port
            if state == "error":
                raise VlmUnavailable(err or "llama-server を起動できません")
            time.sleep(0.5)
        raise VlmUnavailable("llama-server の準備待ちがタイムアウトしました")
