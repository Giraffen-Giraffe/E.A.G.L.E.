"""ポータブル版の zip を作る(exe 一式 + portable.txt)。

    python scripts/make_portable_zip.py <exe 一式のフォルダ> <出力 zip>
portable.txt があると、書類・データベース・モデルを exe と同じフォルダの中に保存する。
"""
from __future__ import annotations

import sys
import time
import zipfile
from pathlib import Path

MARKER = "このファイルがあると、書類・データベース・モデルをこのフォルダの中(data / bin / models)に保存します。\n"


def _read(path: Path) -> bytes:
    # 作成直後のファイルはウイルス対策ソフトの検査で一時的に開けないことがあるため、少し待って読み直す
    for attempt in range(10):
        try:
            return path.read_bytes()
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    return path.read_bytes()


def main() -> int:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    top = src.name
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(p for p in src.rglob("*") if p.is_file()):
            z.writestr(f"{top}/{f.relative_to(src).as_posix()}", _read(f))
        z.writestr(f"{top}/portable.txt", MARKER.encode("utf-8"))
    print(f"ポータブル版を作成しました: {out}({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
