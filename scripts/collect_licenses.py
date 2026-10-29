"""配布物に同梱するライセンス文書を集める。

    python scripts/collect_licenses.py <出力先フォルダ>   (例: dist/EAGLE)
<出力先>/licenses/ に次を置く:
    python/<パッケージ>/   実行時に使う Python パッケージのライセンス文書(パッケージ同梱のもの)
    frontend/             画面に組み込まれる JavaScript ライブラリ(React 等)のライセンス
    llama.cpp/            llama.cpp のライセンス(bin/llama を同梱する場合)
    Qwen3-VL/             モデルのライセンス(Apache-2.0。models を同梱する場合)
    INDEX.txt             一覧(名前・版・ライセンス)
"""
from __future__ import annotations

import importlib.metadata as md
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSE_RE = re.compile(r"(?i)(licen[cs]e|copying|notice|authors)")
FRONTEND_PACKAGES = ("react", "react-dom", "scheduler")
APACHE_URL = "https://www.apache.org/licenses/LICENSE-2.0.txt"
LLAMA_LICENSE_URL = "https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/LICENSE"


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_distributions() -> list[md.Distribution]:
    """eagle-engine[desktop] と uvicorn[standard] が実行時に使うパッケージ(推移的)。"""
    from packaging.requirements import Requirement

    seen: dict[str, md.Distribution] = {}

    def walk(name: str, extras: tuple[str, ...] = ()) -> None:
        k = _key(name)
        if k in seen:
            return
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            return
        seen[k] = dist
        for r in dist.requires or []:
            req = Requirement(r)
            if req.marker is None or any(req.marker.evaluate({"extra": e}) for e in ("",) + extras):
                walk(req.name, tuple(req.extras))

    walk("eagle-engine", ("desktop",))
    seen.pop("eagle-engine", None)
    return sorted(seen.values(), key=lambda d: _key(d.metadata["Name"]))


def license_of(dist: md.Distribution) -> str:
    m = dist.metadata
    lic = m.get("License-Expression") or ""
    if not lic:
        cls = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
        lic = ", ".join(cls) or (m.get("License") or "").splitlines()[0][:80] if (cls or m.get("License")) else ""
    return lic or "(パッケージのライセンス文書を参照)"


def _download(url: str, dest: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.read())
        return True
    except OSError as exc:
        print(f"  取得できませんでした {url}: {exc}")
        return False


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/EAGLE") / "licenses"
    if out.exists():
        shutil.rmtree(out)
    index = ["E.A.G.L.E. に含まれる第三者のソフトウェア", ""]

    # Python 本体(配布用 exe に同梱)
    py_lic = Path(sys.base_prefix) / "LICENSE.txt"
    if py_lic.exists():
        (out / "python" / "Python").mkdir(parents=True, exist_ok=True)
        shutil.copy2(py_lic, out / "python" / "Python" / "LICENSE.txt")
    index.append(f"Python {sys.version.split()[0]}  PSF-2.0")

    for dist in runtime_distributions():
        name, ver = dist.metadata["Name"], dist.version
        dest = out / "python" / name
        n = 0
        for f in dist.files or []:
            # ファイル名、または licenses/ 等のフォルダ名で判定する(PDFium の同梱ライブラリ分も拾う)
            if LICENSE_RE.search(str(f)) and not str(f).endswith((".py", ".pyc", ".pyi")):
                src = Path(dist.locate_file(f))
                if src.is_file():
                    rel = Path(*Path(str(f)).parts[1:]) if len(Path(str(f)).parts) > 1 else Path(str(f)).name
                    (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest / rel)
                    n += 1
        index.append(f"{name} {ver}  {license_of(dist)}" + ("" if n else "  ※ライセンス文書なし"))

    nm = ROOT / "frontend" / "node_modules"
    for pkg in FRONTEND_PACKAGES:
        src = nm / pkg / "LICENSE"
        if src.exists():
            (out / "frontend" / pkg).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / "frontend" / pkg / "LICENSE")
            index.append(f"{pkg} (JavaScript)  MIT")

    bundled = out.parent
    if (bundled / "bin" / "llama").exists():
        version = (bundled / "bin" / "llama" / "VERSION.txt")
        tag = version.read_text(encoding="utf-8").split()[0] if version.exists() else "master"
        _download(LLAMA_LICENSE_URL.format(tag=tag), out / "llama.cpp" / "LICENSE")
        for f in (bundled / "bin" / "llama").glob("LICENSE*"):
            shutil.copy2(f, out / "llama.cpp" / f.name)
        index.append(f"llama.cpp {tag}  MIT(同梱の CUDA ランタイムは NVIDIA CUDA EULA)")
    if (bundled / "models").exists() and any((bundled / "models").glob("*.gguf")):
        if _download(APACHE_URL, out / "Qwen3-VL" / "LICENSE"):
            (out / "Qwen3-VL" / "NOTICE.txt").write_text(
                "Qwen3-VL model weights (GGUF) by the Qwen team, Alibaba Cloud.\n"
                "Licensed under the Apache License, Version 2.0.\n"
                "https://huggingface.co/Qwen\n", encoding="utf-8")
        index.append("Qwen3-VL (GGUF)  Apache-2.0")

    out.mkdir(parents=True, exist_ok=True)
    (out / "INDEX.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"ライセンス文書を集めました: {out}({len(index) - 2} 件)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
