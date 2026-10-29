"""サーバー版(ログイン必須)の認証・権限のテスト。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

H = {"X-Eagle-Request": "1"}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("EAGLE_HOME", str(tmp_path))
    monkeypatch.setenv("EAGLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EAGLE_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("EAGLE_MODELS_DIR", str(tmp_path / "models"))
    import eagle.vlm as vlm_pkg
    from eagle import settings
    vlm_pkg._llama = None
    settings.invalidate()
    from eagle.api.app import create_app
    from eagle.config import load_boot_config
    from eagle.db import init_db
    from eagle.services import auth
    cfg = load_boot_config("server")
    app = create_app(cfg)
    with TestClient(app, base_url="http://10.0.0.5") as c:
        init_db(cfg.database_url)
        auth.create_user("admin", "admin-pass-1", "admin")
        auth.create_user("viewer", "viewer-pass-1", "viewer")
        yield c


def test_password_hash_roundtrip():
    from eagle.services.auth import hash_password, verify_password
    h = hash_password("correct horse")
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)


def test_login_required_and_roles(server):
    assert server.get("/api/documents").status_code == 401
    assert server.post("/api/auth/login", json={"username": "admin", "password": "x"}, headers=H).status_code == 401
    r = server.post("/api/auth/login", json={"username": "viewer", "password": "viewer-pass-1"}, headers=H)
    assert r.status_code == 200 and r.json()["user"]["role"] == "viewer"
    assert server.get("/api/documents").status_code == 200
    # 閲覧者は設定変更・取込ができない
    assert server.put("/api/settings", json={"export": {"csv_encoding": "cp932"}}, headers=H).status_code == 403
    assert server.post("/api/upload", files=[("files", ("a.pdf", b"%PDF-1.4"))], headers=H).status_code == 403
    server.post("/api/auth/logout", headers=H)
    assert server.get("/api/documents").status_code == 401

    server.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-1"}, headers=H)
    assert server.put("/api/settings", json={"export": {"csv_encoding": "cp932"}}, headers=H).status_code == 200
    users = server.get("/api/users").json()
    assert {u["username"] for u in users} == {"admin", "viewer"}


def test_login_lockout(server):
    for _ in range(5):
        server.post("/api/auth/login", json={"username": "admin", "password": "bad"}, headers=H)
    r = server.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-1"}, headers=H)
    assert r.status_code == 401 and "ロック" in r.json()["detail"]
