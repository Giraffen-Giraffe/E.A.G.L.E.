"""コマンドライン。

  python -m eagle desktop            … アプリ版(1台の PC で完結。ウィンドウで開く)
  python -m eagle server             … Webアプリ版(LAN 内のサーバーで共有。ブラウザから利用)
  python -m eagle setup-vlm          … 同梱 VLM(llama.cpp + モデル)を導入
  python -m eagle user add NAME      … 利用者を追加(サーバー版)
  python -m eagle process FILE...    … 画面を使わずに書類を処理(動作確認・一括処理用)
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import APP_NAME, __version__
from .config import DESKTOP, SERVER, load_boot_config, set_boot


def _logging(cfg, verbose: bool = False) -> None:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(cfg.logs_dir / "eagle.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "watchdog", "multipart", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_desktop(args) -> int:
    cfg = load_boot_config(DESKTOP, port=args.port)
    _logging(cfg, args.verbose)
    from .desktop import run_desktop
    return run_desktop(cfg, browser=args.browser)


def cmd_server(args) -> int:
    cfg = load_boot_config(SERVER, host=args.host, port=args.port)
    _logging(cfg, args.verbose)
    from .server import run_server
    return run_server(cfg)


def cmd_setup_vlm(args) -> int:
    cfg = load_boot_config(DESKTOP)
    _logging(cfg, args.verbose)
    from .vlm.setup import PRESETS, SetupProgress, detect_accelerator, run_setup
    acc = detect_accelerator()
    print(f"GPU: {acc['gpu'] or '(NVIDIA GPU なし)'}  VRAM: {acc['vram_mb']} MB  driver: {acc['driver'] or '-'}")
    print(f"モデル: {PRESETS[args.preset]['label']}(約 {PRESETS[args.preset]['size_gb']} GB)")
    print(f"保存先: {cfg.bin_dir} / {cfg.models_dir}")
    prog = SetupProgress()
    import threading
    import time

    t = threading.Thread(target=run_setup, kwargs=dict(
        bin_dir=cfg.bin_dir, models_dir=cfg.models_dir, preset=args.preset, variant=args.variant, tag=args.tag,
        progress=prog, skip_binary=args.skip_binary, skip_model=args.skip_model), daemon=True)
    t.start()
    last = ""
    while t.is_alive():
        d = prog.as_dict()
        line = f"\r{d['step'][:40]:40s} {d['file'][:44]:44s} {d['percent']:5.1f}% " \
               f"({d['downloaded'] / 1e6:,.0f}/{d['total'] / 1e6:,.0f} MB)"
        if line != last:
            sys.stdout.write(line)
            sys.stdout.flush()
            last = line
        time.sleep(0.5)
    print()
    if prog.state == "error":
        print("失敗:", prog.error)
        return 1
    if not args.skip_model:
        from .db import init_db
        from .settings import update_settings
        set_boot(cfg)
        init_db(cfg.database_url)
        p = PRESETS[args.preset]
        update_settings({"vlm": {"backend": "llama_cpp", "llama": {"model": p["model"], "mmproj": p["mmproj"]}}})
    print("完了しました。`python -m eagle desktop` で起動できます。")
    return 0


def cmd_user(args) -> int:
    cfg = load_boot_config(SERVER)
    set_boot(cfg)
    from .db import User, init_db, session_scope
    from .services import auth
    init_db(cfg.database_url)
    if args.action == "add":
        pw = args.password or getpass.getpass("パスワード(8文字以上): ")
        try:
            auth.create_user(args.username, pw, args.role, args.display_name or "")
        except ValueError as exc:
            print("失敗:", exc)
            return 1
        print(f"利用者 {args.username}({args.role})を追加しました")
    elif args.action == "list":
        with session_scope() as s:
            for u in s.query(User).order_by(User.id):
                print(f"{u.id:4d} {u.username:20s} {u.role:8s} {'有効' if u.active else '無効'}")
    elif args.action == "passwd":
        pw = args.password or getpass.getpass("新しいパスワード: ")
        with session_scope() as s:
            u = s.query(User).filter(User.username == args.username).first()
            if u is None:
                print("利用者が見つかりません")
                return 1
            auth.validate_password(pw)
            u.password_hash = auth.hash_password(pw)
        print("変更しました")
    return 0


def cmd_process(args) -> int:
    """画面なしで処理して結果を表示する(動作確認用)。"""
    import json
    from pathlib import Path

    cfg = load_boot_config(DESKTOP)
    _logging(cfg, args.verbose)
    set_boot(cfg)
    from .db import Document, init_db, session_scope
    from .pipeline.processor import process_document
    from .services.ingest import ingest_bytes
    from .vlm import llama_manager
    from .settings import get_settings
    init_db(cfg.database_url)
    llama_manager().configure(get_settings()["vlm"]["llama"])
    code = 0
    try:
        for f in args.files:
            p = Path(f)
            res = ingest_bytes(p.name, p.read_bytes(), source="cli", enqueue=False)
            print(f"== {p.name} (id={res['id']})")
            try:
                out = process_document(res["id"], {"force": args.force},
                                       lambda fr, m: print(f"  {fr * 100:5.1f}% {m}", flush=True))
            except Exception as exc:  # noqa: BLE001
                print(f"  失敗: {type(exc).__name__}: {exc}")
                with session_scope() as s:
                    d = s.get(Document, res["id"])
                    d.status, d.status_detail = "failed", str(exc)[:2000]
                code = 1
                continue
            with session_scope() as s:
                d = s.get(Document, res["id"])
                result = {"status": d.status, "detail": d.status_detail, "header": d.header,
                          "lines": [{k: getattr(li, k) for k in ("line_no", "order_number", "product_code",
                                                                  "product_name", "quantity", "unit", "unit_price",
                                                                  "amount", "due_date")} for li in d.lines],
                          "warnings": d.warnings, "sec": d.processing_sec}
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            if out.get("status") == "failed":
                code = 1
    finally:
        if not args.keep_server:
            llama_manager().stop()
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eagle", description=f"{APP_NAME} {__version__}")
    ap.add_argument("--verbose", "-v", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    d = sub.add_parser("desktop", help="アプリ版を起動(1台の PC で完結)")
    d.add_argument("--port", type=int)
    d.add_argument("--browser", action="store_true", help="専用ウィンドウではなくブラウザで開く")
    d.set_defaults(func=cmd_desktop)

    s = sub.add_parser("server", help="Webアプリ版を起動(LAN 内で共有)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_server)

    v = sub.add_parser("setup-vlm", help="同梱 VLM(llama.cpp + モデル)を導入")
    v.add_argument("--preset", default="standard", choices=["standard", "high", "light", "tiny"])
    v.add_argument("--variant", default="auto", help="auto / cuda-13 / cuda-12 / vulkan / cpu")
    v.add_argument("--tag", default=None, help="llama.cpp のビルド番号(既定は動作確認済みの版 / latest)")
    v.add_argument("--skip-binary", action="store_true")
    v.add_argument("--skip-model", action="store_true")
    v.set_defaults(func=cmd_setup_vlm)

    u = sub.add_parser("user", help="利用者の管理(サーバー版)")
    u.add_argument("action", choices=["add", "list", "passwd"])
    u.add_argument("username", nargs="?", default="")
    u.add_argument("--password")
    u.add_argument("--role", default="editor", choices=["admin", "editor", "viewer"])
    u.add_argument("--display-name")
    u.set_defaults(func=cmd_user)

    p = sub.add_parser("process", help="画面なしで書類を処理(動作確認用)")
    p.add_argument("files", nargs="+")
    p.add_argument("--force", action="store_true", help="注文書以外と判定されても処理する")
    p.add_argument("--keep-server", action="store_true", help="終了後も llama-server を止めない")
    p.set_defaults(func=cmd_process)

    args = ap.parse_args(argv)
    if getattr(args, "tag", "x") is None:
        from .vlm.setup import LLAMA_CPP_TAG
        args.tag = LLAMA_CPP_TAG
    if not args.cmd:
        # 引数なし(exe のダブルクリック等)はアプリ版を起動
        args = ap.parse_args(["desktop"] + (argv or []))
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
