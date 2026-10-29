"""保存先の決め方(インストール版 / ポータブル版 / 開発時)のテスト。"""
import sys

from eagle import config


def _frozen(monkeypatch, tmp_path):
    app = tmp_path / "Program Files" / "EAGLE"
    app.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "EAGLE.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    for k in ("EAGLE_HOME", "EAGLE_DATA_DIR", "EAGLE_BIN_DIR", "EAGLE_MODELS_DIR", "EAGLE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    return app


def test_installed_uses_user_area(monkeypatch, tmp_path):
    app = _frozen(monkeypatch, tmp_path)
    cfg = config.load_boot_config("desktop")
    base = tmp_path / "LocalAppData" / "EAGLE"
    assert cfg.home == app
    assert cfg.data_dir == base / "data"
    assert cfg.bin_dir == base / "bin" / "llama"
    assert cfg.models_dir == base / "models"


def test_portable_keeps_data_next_to_exe(monkeypatch, tmp_path):
    app = _frozen(monkeypatch, tmp_path)
    (app / config.PORTABLE_MARKER).write_text("portable", encoding="utf-8")
    cfg = config.load_boot_config("desktop")
    assert cfg.data_dir == app / "data"
    assert cfg.models_dir == app / "models"


def test_bundled_engine_next_to_exe_is_used(monkeypatch, tmp_path):
    app = _frozen(monkeypatch, tmp_path)
    (app / "bin" / "llama").mkdir(parents=True)
    (app / "bin" / "llama" / "llama-server.exe").write_bytes(b"")
    (app / "models").mkdir()
    (app / "models" / "m.gguf").write_bytes(b"")
    cfg = config.load_boot_config("desktop")
    assert cfg.bin_dir == app / "bin" / "llama"
    assert cfg.models_dir == app / "models"
    assert cfg.data_dir == tmp_path / "LocalAppData" / "EAGLE" / "data"
