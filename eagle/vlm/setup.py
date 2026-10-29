"""同梱 VLM の導入(llama.cpp 実行ファイル + GGUF モデルのダウンロード)。

導入は初回の一度だけ。以降はネットワーク不要で完全にローカルで動作する。
オフライン環境では、別の端末で取得した bin/llama と models/ をそのままコピーしてもよい。
"""
from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

LOG = logging.getLogger("eagle.setup")

# 動作確認済みの llama.cpp ビルド。"latest" を指定すると最新のビルドを使う。
LLAMA_CPP_TAG = "b11979"
GITHUB_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases"

HF = "https://huggingface.co/{repo}/resolve/main/{file}"

# 用途別のモデル構成(すべて公開モデル)。VRAM / メモリの目安は README を参照。
PRESETS: dict[str, dict] = {
    "standard": {
        "label": "標準 Qwen3-VL 8B (Q4_K_M)",
        "repo": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
        "model": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
        "mmproj": "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf",
        "size_gb": 5.8,
    },
    "high": {
        "label": "高精度 Qwen3-VL 8B (Q8_0)",
        "repo": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
        "model": "Qwen3VL-8B-Instruct-Q8_0.gguf",
        "mmproj": "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
        "size_gb": 9.9,
    },
    "light": {
        "label": "軽量 Qwen3-VL 4B (Q4_K_M)",
        "repo": "Qwen/Qwen3-VL-4B-Instruct-GGUF",
        "model": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
        "mmproj": "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
        "size_gb": 3.0,
    },
    "tiny": {
        "label": "最小 Qwen3-VL 2B (Q4_K_M)",
        "repo": "Qwen/Qwen3-VL-2B-Instruct-GGUF",
        "model": "Qwen3VL-2B-Instruct-Q4_K_M.gguf",
        "mmproj": "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf",
        "size_gb": 1.6,
    },
}


@dataclass
class SetupProgress:
    state: str = "idle"          # idle / running / done / error
    step: str = ""
    file: str = ""
    downloaded: int = 0
    total: int = 0
    error: str = ""
    log: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        pct = round(self.downloaded * 100.0 / self.total, 1) if self.total else 0.0
        return {"state": self.state, "step": self.step, "file": self.file, "downloaded": self.downloaded,
                "total": self.total, "percent": pct, "error": self.error, "log": self.log[-30:]}

    def note(self, msg: str) -> None:
        LOG.info(msg)
        self.log.append(msg)


# ── GPU 判定と llama.cpp の配布物選択 ───────────────────────────────────
def detect_accelerator() -> dict:
    """NVIDIA ドライバの有無と版数から、使う llama.cpp ビルドを決める。"""
    info = {"nvidia": False, "driver": "", "gpu": "", "vram_mb": 0}
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip().splitlines()
            if out:
                name, mem, drv = [x.strip() for x in out[0].split(",")[:3]]
                info.update(nvidia=True, gpu=name, vram_mb=int(float(mem)), driver=drv)
        except Exception:  # noqa: BLE001
            pass
    return info


def choose_variant(requested: str = "auto") -> str:
    if requested and requested != "auto":
        return requested
    acc = detect_accelerator()
    if acc["nvidia"]:
        try:
            major = int(acc["driver"].split(".")[0])
        except ValueError:
            major = 0
        # CUDA 13 系ビルドは R580 以降のドライバが必要。古いドライバは CUDA 12.4 ビルド。
        return "cuda-13" if major >= 580 else "cuda-12"
    if platform.system() == "Darwin":
        return "metal"
    return "vulkan" if platform.system() == "Windows" else "cpu"


def _asset_patterns(variant: str) -> list[str]:
    """リリース資産名の正規表現(本体, 付随ランタイム)。"""
    system = platform.system()
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if system == "Windows":
        if variant.startswith("cuda"):
            ver = r"13\.\d+" if variant == "cuda-13" else r"12\.\d+"
            return [rf"^llama-b\d+-bin-win-cuda-{ver}-{arch}\.zip$", rf"^cudart-llama-bin-win-cuda-{ver}-{arch}\.zip$"]
        return [rf"^llama-b\d+-bin-win-{variant}-{arch}\.zip$"]
    if system == "Darwin":
        return [rf"^llama-b\d+-bin-macos-{arch}\.tar\.gz$"]
    if variant.startswith("cuda"):
        ver = r"13\.\d+" if variant == "cuda-13" else r"12\.\d+"
        return [rf"^llama-b\d+-bin-ubuntu-cuda-{ver}-{arch}\.tar\.gz$"]
    if variant == "vulkan":
        return [rf"^llama-b\d+-bin-ubuntu-vulkan-{arch}\.tar\.gz$"]
    return [rf"^llama-b\d+-bin-ubuntu-{arch}\.tar\.gz$"]


def _release_assets(tag: str) -> tuple[str, list[dict]]:
    headers = {"Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        if tag and tag != "latest":
            r = c.get(f"{GITHUB_RELEASES}/tags/{tag}", headers=headers)
            r.raise_for_status()
            rel = r.json()
            return rel["tag_name"], rel.get("assets", [])
        r = c.get(GITHUB_RELEASES, params={"per_page": 10}, headers=headers)
        r.raise_for_status()
        for rel in r.json():
            if re.fullmatch(r"b\d+", rel.get("tag_name", "")) and rel.get("assets"):
                return rel["tag_name"], rel["assets"]
    raise RuntimeError("llama.cpp のリリースが見つかりません")


# ── ダウンロード ────────────────────────────────────────────────────────
def download(url: str, dest: Path, progress: SetupProgress | None = None, expected_size: int = 0) -> Path:
    """再開可能なダウンロード(.part に書き、完了後に改名)。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (not expected_size or dest.stat().st_size == expected_size):
        if progress:
            progress.note(f"取得済み: {dest.name}")
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0), follow_redirects=True) as c:
        with c.stream("GET", url, headers=headers) as r:
            if r.status_code == 416:      # 既に全量ある
                part.rename(dest)
                return dest
            r.raise_for_status()
            if r.status_code != 206:
                have = 0
            total = have + int(r.headers.get("content-length") or 0)
            if progress:
                progress.file, progress.total, progress.downloaded = dest.name, total, have
            with open(part, "ab" if have else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    if progress:
                        progress.downloaded += len(chunk)
    part.replace(dest)
    if progress:
        progress.note(f"取得完了: {dest.name}")
    return dest


def _extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            t.extractall(target, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(target)
    for p in target.rglob("llama-server"):
        p.chmod(p.stat().st_mode | 0o111)


def install_llama_cpp(bin_dir: Path, variant: str = "auto", tag: str = LLAMA_CPP_TAG,
                      progress: SetupProgress | None = None, cache_dir: Path | None = None) -> Path:
    variant = choose_variant(variant)
    if variant == "metal":
        variant = "cpu"   # macOS ビルドは Metal 対応が既定で入っている
    tag, assets = _release_assets(tag)
    pats = _asset_patterns(variant)
    if progress:
        progress.step = f"llama.cpp {tag} ({variant})"
        progress.note(f"llama.cpp {tag} / {variant} を導入します")
    cache_dir = cache_dir or bin_dir.parent / ".download"
    for pat in pats:
        asset = next((a for a in assets if re.match(pat, a["name"])), None)
        if asset is None:
            raise RuntimeError(f"llama.cpp {tag} に {variant} 用の配布物がありません({pat})")
        arc = download(asset["browser_download_url"], cache_dir / asset["name"], progress, int(asset.get("size") or 0))
        _extract(arc, bin_dir)
        arc.unlink(missing_ok=True)   # 展開したら圧縮ファイルは不要(数百 MB ある)
    try:
        cache_dir.rmdir()
    except OSError:
        pass
    from .llama_server import find_server_binary
    exe = find_server_binary(bin_dir)
    if exe is None:
        raise RuntimeError("展開後に llama-server が見つかりません")
    (bin_dir / "VERSION.txt").write_text(f"{tag} {variant}\n", encoding="utf-8")
    # 再配布に備えて llama.cpp のライセンス文書も置いておく(取得できなくても導入は続ける)
    try:
        r = httpx.get(f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/LICENSE", timeout=30,
                      follow_redirects=True)
        if r.status_code == 200:
            (bin_dir / "LICENSE-llama.cpp.txt").write_text(r.text, encoding="utf-8")
    except httpx.HTTPError:
        pass
    return exe


def _hf_size(repo: str, file: str) -> int:
    try:
        r = httpx.head(HF.format(repo=repo, file=file), follow_redirects=True, timeout=30)
        return int(r.headers.get("x-linked-size") or r.headers.get("content-length") or 0)
    except httpx.HTTPError:
        return 0


def install_model(models_dir: Path, preset: str = "standard", progress: SetupProgress | None = None) -> tuple[Path, Path]:
    if preset not in PRESETS:
        raise ValueError(f"未知のプリセット: {preset}(選択肢: {', '.join(PRESETS)})")
    p = PRESETS[preset]
    if progress:
        progress.step = p["label"]
        progress.note(f"モデル {p['label']} を取得します(約 {p['size_gb']} GB)")
    out = []
    for key in ("model", "mmproj"):
        name = p[key]
        out.append(download(HF.format(repo=p["repo"], file=name), models_dir / name, progress, _hf_size(p["repo"], name)))
    return out[0], out[1]


def run_setup(bin_dir: Path, models_dir: Path, preset: str = "standard", variant: str = "auto",
              tag: str = LLAMA_CPP_TAG, progress: SetupProgress | None = None,
              skip_binary: bool = False, skip_model: bool = False,
              on_done: Callable[[Path | None, Path | None], None] | None = None) -> None:
    progress = progress or SetupProgress()
    progress.state, progress.error = "running", ""
    try:
        from .llama_server import find_server_binary
        if not skip_binary:
            if find_server_binary(bin_dir) is None:
                install_llama_cpp(bin_dir, variant, tag, progress)
            else:
                progress.note(f"llama-server は導入済みです({find_server_binary(bin_dir)})")
        model = mmproj = None
        if not skip_model:
            model, mmproj = install_model(models_dir, preset, progress)
        progress.state, progress.step = "done", "完了"
        progress.note("導入が完了しました")
        if on_done:
            on_done(model, mmproj)
    except Exception as exc:  # noqa: BLE001
        progress.state, progress.error = "error", str(exc)
        progress.note(f"失敗: {exc}")
        LOG.exception("VLM の導入に失敗しました")


class SetupRunner:
    """画面からの導入を別スレッドで実行し、進捗を返す。"""

    def __init__(self) -> None:
        self.progress = SetupProgress()
        self._thread: threading.Thread | None = None

    def start(self, **kwargs) -> dict:
        if self._thread and self._thread.is_alive():
            return self.progress.as_dict()
        self.progress = SetupProgress(state="running")
        self._thread = threading.Thread(target=run_setup, kwargs={**kwargs, "progress": self.progress}, daemon=True)
        self._thread.start()
        return self.progress.as_dict()

    def status(self) -> dict:
        return self.progress.as_dict()
