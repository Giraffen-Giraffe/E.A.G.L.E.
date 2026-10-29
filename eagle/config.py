"""起動時設定(ブートストラップ)。

保存先・待受ポート・DB 接続先など「アプリ起動前に決まっている必要がある値」だけを扱う。
画面から変更できる実行時設定(VLM・抽出規則・監視フォルダ等)は settings.py が DB に持つ。

優先順位: 環境変数 EAGLE_* > 設定ファイル(eagle.toml) > 既定値
設定ファイルの探索順: $EAGLE_CONFIG → <home>/eagle.toml → <home>/config/eagle.toml
"""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as _toml  # type: ignore[no-redef]

DESKTOP = "desktop"
SERVER = "server"

DEFAULT_DESKTOP_PORT = 18760
DEFAULT_SERVER_PORT = 18770
DEFAULT_LLAMA_PORT = 18780


def app_home() -> Path:
    """アプリの基準フォルダ。PyInstaller で固めた場合は exe の隣、開発時はリポジトリ直下。"""
    env = os.environ.get("EAGLE_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


PORTABLE_MARKER = "portable.txt"


def user_base() -> Path:
    """利用者ごとの保存領域(Windows: %LOCALAPPDATA%\\EAGLE)。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "EAGLE"


def storage_base(home: Path) -> Path:
    """書類・DB・モデルの置き場所の基準。
    開発時とポータブル版(exe の隣に portable.txt がある)はアプリのフォルダ。
    インストール版は利用者ごとの領域(インストール先は書き込めないことがあり、アンインストールでも消えないように)。"""
    if not getattr(sys, "frozen", False):
        return home
    if (home / PORTABLE_MARKER).exists() and _writable(home / "data"):
        return home
    return user_base()


def _find_config_file(home: Path, base: Path) -> Path | None:
    env = os.environ.get("EAGLE_CONFIG", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    for p in (home / "eagle.toml", home / "config" / "eagle.toml", base / "eagle.toml"):
        if p.exists():
            return p
    return None


def _default_bin_dir(home: Path, base: Path) -> Path:
    """llama.cpp の置き場所。アプリと一緒に配られたもの(exe の隣)があればそれを使う。"""
    bundled = home / "bin" / "llama"
    if any(bundled.glob("llama-server*")):
        return bundled
    return base / "bin" / "llama"


def _default_models_dir(home: Path, base: Path) -> Path:
    bundled = home / "models"
    if any(bundled.glob("*.gguf")):
        return bundled
    return base / "models"


def _env(name: str, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class BootConfig:
    mode: str = DESKTOP
    home: Path = field(default_factory=app_home)
    data_dir: Path = Path()
    database_url: str = ""
    host: str = "127.0.0.1"
    port: int = DEFAULT_DESKTOP_PORT
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    session_hours: int = 12
    secret_key: str = ""
    bin_dir: Path = Path()
    models_dir: Path = Path()
    config_file: Path | None = None
    # 初回起動時に作る管理者(サーバー版)。未設定なら CLI で作成する。
    admin_user: str = ""
    admin_password: str = ""

    @property
    def auth_required(self) -> bool:
        return self.mode == SERVER

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.files_dir, self.logs_dir, self.exports_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_boot_config(mode: str = DESKTOP, **overrides) -> BootConfig:
    home = app_home()
    base = storage_base(home)
    cfg_file = _find_config_file(home, base)
    raw: dict = {}
    if cfg_file:
        with open(cfg_file, "rb") as f:
            raw = _toml.load(f)

    storage = raw.get("storage", {}) or {}
    server = raw.get("server", {}) or {}
    desktop = raw.get("desktop", {}) or {}
    vlm = raw.get("vlm", {}) or {}
    security = raw.get("security", {}) or {}

    def _path(v, base: Path) -> Path:
        p = Path(str(v)).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    data_dir = _env("EAGLE_DATA_DIR") or storage.get("data_dir")
    data_dir = _path(data_dir, home) if data_dir else base / "data"
    bin_dir = _env("EAGLE_BIN_DIR") or vlm.get("bin_dir")
    models_dir = _env("EAGLE_MODELS_DIR") or vlm.get("models_dir")

    section = server if mode == SERVER else desktop
    default_host = "0.0.0.0" if mode == SERVER else "127.0.0.1"
    default_port = DEFAULT_SERVER_PORT if mode == SERVER else DEFAULT_DESKTOP_PORT

    cfg = BootConfig(
        mode=mode,
        home=home,
        data_dir=data_dir,
        database_url=_env("EAGLE_DATABASE_URL") or storage.get("database_url") or "",
        host=_env("EAGLE_HOST") or section.get("host") or default_host,
        port=int(_env("EAGLE_PORT") or section.get("port") or default_port),
        ssl_certfile=_env("EAGLE_SSL_CERTFILE") or server.get("ssl_certfile", ""),
        ssl_keyfile=_env("EAGLE_SSL_KEYFILE") or server.get("ssl_keyfile", ""),
        session_hours=int(_env("EAGLE_SESSION_HOURS") or security.get("session_hours") or 12),
        bin_dir=_path(bin_dir, home) if bin_dir else _default_bin_dir(home, base),
        models_dir=_path(models_dir, home) if models_dir else _default_models_dir(home, base),
        config_file=cfg_file,
        admin_user=_env("EAGLE_ADMIN_USER", ""),
        admin_password=_env("EAGLE_ADMIN_PASSWORD", ""),
    )
    for k, v in overrides.items():
        if v is not None and hasattr(cfg, k):
            setattr(cfg, k, v)
    if not cfg.database_url:
        cfg.database_url = "sqlite:///" + (cfg.data_dir / "eagle.db").as_posix()
    cfg.ensure_dirs()
    cfg.secret_key = _load_or_create_secret(cfg.data_dir)
    return cfg


def _load_or_create_secret(data_dir: Path) -> str:
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


_BOOT: BootConfig | None = None


def set_boot(cfg: BootConfig) -> None:
    global _BOOT
    _BOOT = cfg


def boot() -> BootConfig:
    if _BOOT is None:
        set_boot(load_boot_config())
    return _BOOT  # type: ignore[return-value]
