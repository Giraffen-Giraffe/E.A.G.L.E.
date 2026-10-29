"""PyInstaller 用の起動口。

EAGLE.exe        … アプリ版(引数なし=デスクトップ。コンソール窓なし)
EAGLE-Server.exe … Webアプリ版(引数なし=サーバー。コンソール窓あり)
どちらも引数を付ければ `python -m eagle` と同じコマンドを受け付ける。
"""
import sys
from pathlib import Path

from eagle.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        args = ["server"] if Path(sys.executable).stem.lower().endswith("server") else ["desktop"]
    raise SystemExit(main(args))
