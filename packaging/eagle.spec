# PyInstaller 定義: `pyinstaller packaging/eagle.spec` (scripts/build_desktop.ps1 から実行)
# 出力: dist/EAGLE/ (EAGLE.exe = アプリ版 / EAGLE-Server.exe = Webアプリ版)
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
static = ROOT / "eagle" / "web" / "static"
if not (static / "index.html").exists():
    raise SystemExit("画面が未ビルドです。先に frontend で `npm run build` を実行してください")

hidden = (collect_submodules("uvicorn") + collect_submodules("webview")
          + ["sqlalchemy.dialects.sqlite", "multipart", "python_multipart"])

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(static), "eagle/web/static")],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "reportlab"],
    noarchive=False,
)
# OpenCV の動画用 DLL(約 100MB)は使わない
a.binaries = [b for b in a.binaries if "videoio_ffmpeg" not in b[0].lower()]
pyz = PYZ(a.pure)
icon = str(ROOT / "packaging" / "eagle.ico") if (ROOT / "packaging" / "eagle.ico").exists() else None
app = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EAGLE", console=False, icon=icon)
srv = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EAGLE-Server", console=True, icon=icon)
coll = COLLECT(app, srv, a.binaries, a.datas, name="EAGLE")
