"""アプリ全体の流れのテスト(VLM は応答を差し替える)。

取込 → 処理 → 修正・確定 → 学習 → 再処理で自動補正 → xlsx/csv 出力 → 外部DB 書き出し → 認証
"""
from __future__ import annotations

import io
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

H = {"X-Eagle-Request": "1"}


def _pdf(lines: list[tuple[float, float, str, float]]) -> bytes:
    """(x, y(上から), 文字, 大きさ) の並びから電子PDFを作る。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    for x, y, text, size in lines:
        c.setFont("HeiseiKakuGo-W5", size)
        c.drawString(x, 842 - y, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_pdf() -> bytes:
    return _pdf([(60, 80, "注文書", 20), (60, 120, "注文番号 PO-1234-5678  株式会社テスト商事", 10),
                 (60, 140, "品番 AB-1000 数量 2 単価 45,000 送料 1,500", 10)])


def fake_chat_json(self, system, user, images=None, schema=None, want=None):
    self.calls += 1
    if "明細表から各明細行" in user:
        return {"order_numbers": ["PO-1234-5678"], "line_items": [
            {"order_number": "", "raw_code": "AB-1OOO", "corrected_code": "QQ-1000", "product_name": "ユニット",
             "quantity": "2", "unit": "台", "unit_price": "45,000", "amount": "90,000", "due_date": "11/15", "notes": ""},
            {"order_number": "", "raw_code": "", "corrected_code": "", "product_name": "送料", "quantity": "1",
             "unit": "式", "unit_price": "1500", "amount": "1500", "due_date": "", "notes": ""},
        ]}
    if "companies" in user:
        return {"document_title": "注文書", "companies": [
            {"name": "サンプル工業株式会社", "department": "", "contact": "", "address": "", "tel": "", "fax": "", "role": "orderer"},
            {"name": "株式会社テスト商事", "department": "大阪支店", "contact": "山田 太郎", "address": "大阪府大阪市北区梅田1-2-3",
             "tel": "06-1234-5678", "fax": "", "role": "other"},
        ], "order_number": "PO-1234-5678", "order_date": "2026/10/27", "delivery_date": "", "buyer_contact": "",
            "ship_to": {"company": "", "department": "", "contact": "", "address": "", "tel": "", "fax": ""},
            "end_user": "", "customer_order_number": "", "subtotal": "91,500", "tax": "", "total": "", "payment_terms": "",
            "notes": ""}
    return {}


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    root = tmp_path_factory.mktemp("eagle")
    monkeypatch_module.setenv("EAGLE_HOME", str(root))
    monkeypatch_module.setenv("EAGLE_DATA_DIR", str(root / "data"))
    monkeypatch_module.setenv("EAGLE_BIN_DIR", str(root / "bin"))
    monkeypatch_module.setenv("EAGLE_MODELS_DIR", str(root / "models"))
    import eagle.vlm as vlm_pkg
    from eagle import masters, settings
    from eagle.vlm.client import VlmClient
    vlm_pkg._llama = None
    settings.invalidate()
    masters.invalidate()
    monkeypatch_module.setattr(VlmClient, "chat_json", fake_chat_json)
    monkeypatch_module.setattr(VlmClient, "model_name", lambda self: "fake-vlm")
    from eagle.config import load_boot_config
    from eagle.api.app import create_app
    app = create_app(load_boot_config("desktop"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _wait(c, doc_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = c.get(f"/api/documents/{doc_id}").json()
        if d["status"] not in ("queued", "processing"):
            return d
        time.sleep(0.2)
    raise AssertionError("処理が終わりません")


def test_csrf_and_host_guard(client):
    assert client.post("/api/templates", json={"name": "x"}).status_code == 403
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400


def test_full_flow(client):
    client.put("/api/settings", json={"extraction": {"own_company_names": ["サンプル工業株式会社"]}}, headers=H)
    products = "商品コード,品名,単位,単価\nAB-1000,ユニット,台,45000\n".encode("utf-8-sig")
    r = client.post("/api/masters/products/import", files={"file": ("p.csv", products)}, data={"replace": "true"}, headers=H)
    assert r.json()["imported"] == 1

    up = client.post("/api/upload", files=[("files", ("order.pdf", _make_pdf(), "application/pdf"))], headers=H).json()
    doc_id = up["results"][0]["id"]
    d = _wait(client, doc_id)
    assert d["status"] in ("extracted", "needs_review"), d["status_detail"]
    h = d["header"]
    # 自社は orderer と誤ラベルされても発注元にしない
    assert h["buyer_company"] == "株式会社テスト商事"
    assert h["order_number"] == "PO-1234-5678"
    assert h["order_date"] == "2026-10-27"
    codes = [ln["product_code"] for ln in d["lines"]]
    assert codes == ["AB-1000", "送料"]          # raw の AB-1OOO がマスタで確定
    assert d["lines"][0]["due_date"] == "2026-11-15"
    assert d["pages"] and d["pages"][0]["text_layer"]
    assert client.get(f"/api/documents/{doc_id}/pages/1").status_code == 200

    # 人が修正して確定 → 学習
    h2 = dict(h, buyer_contact="山田 太郎", payment_terms="月末締め翌月末払い")
    lines = d["lines"]
    lines[1]["product_code"] = "SHIP-01"
    r = client.put(f"/api/documents/{doc_id}", json={"header": h2, "lines": lines, "confirm": True}, headers=H).json()
    assert r["status"] == "confirmed"
    learn = client.get("/api/learning").json()
    assert learn["stats"]["confirmed_docs"] == 1
    assert any(x["field"] == "payment_terms" for x in learn["rules"])

    # 出力
    x = client.get("/api/export", params={"format": "xlsx", "ids": str(doc_id)})
    assert x.status_code == 200
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["注文書", "明細", "一覧(明細展開)"]
    assert wb["明細"].max_row == 3
    csv_text = client.get("/api/export", params={"format": "csv", "layout": "flat"}).content.decode("utf-8-sig")
    assert "PO-1234-5678" in csv_text and "AB-1000" in csv_text

    # 外部DB(SQLite)へ書き出し、再送しても重複しない
    from eagle.config import boot
    dbfile = boot().data_dir / "external.db"
    t = client.post("/api/integrations", json={"name": "test", "url": f"sqlite:///{dbfile.as_posix()}"}, headers=H).json()
    assert client.post("/api/integrations/test", json={"id": t["id"]}, headers=H).json()["ok"]
    for _ in range(2):
        assert client.post(f"/api/integrations/{t['id']}/push", json={"ids": [doc_id]}, headers=H).json()["pushed"] == 1
    con = sqlite3.connect(dbfile)
    assert con.execute("select count(*) from eagle_orders").fetchone()[0] == 1
    assert con.execute("select count(*) from eagle_order_lines").fetchone()[0] == 2
    assert con.execute("select order_number from eagle_orders").fetchone()[0] == "PO-1234-5678"
    con.close()


def test_template_and_reprocess_applies_learning(client):
    docs = client.get("/api/documents").json()["items"]
    doc_id = docs[0]["id"]
    t = client.post(f"/api/templates/from-document/{doc_id}", json={}, headers=H).json()
    assert t["match_keywords"] == ["テスト商事"]
    t = client.put(f"/api/templates/{t['id']}", json={"defaults": {"notes": "既定の備考"}}, headers=H).json()
    client.post(f"/api/documents/{doc_id}/reprocess", json={}, headers=H)
    d = _wait(client, doc_id)
    assert d["template"] == t["name"]
    # 学習した置換(支払条件)・型番(送料→SHIP-01)・テンプレート既定値が反映される
    assert d["header"]["notes"] == "既定の備考"
    assert [ln["product_code"] for ln in d["lines"]] == ["AB-1000", "SHIP-01"]


def test_watch_folder(client, tmp_path):
    from eagle.config import boot
    folder = boot().data_dir.parent / "inbox"
    folder.mkdir(exist_ok=True)
    client.put("/api/settings", json={"watch": {"folders": [{"path": str(folder), "recursive": False, "polling": True}]}},
               headers=H)
    before = client.get("/api/documents").json()["total"]
    (folder / "watched.pdf").write_bytes(_make_pdf())
    st = client.post("/api/watch/start", headers=H).json()
    assert st["running"] and st["folders"][0]["ok"]
    t0 = time.time()
    while time.time() - t0 < 30:
        if client.get("/api/documents").json()["total"] > before:
            break
        time.sleep(0.5)
    client.post("/api/watch/stop", headers=H)
    assert client.get("/api/documents").json()["total"] == before + 1
    assert (folder / "_eagle_done" / "watched.pdf").exists()


def test_non_order_skipped(client):
    data = _pdf([(60, 60, "現品票", 24), (60, 120, "品番 AB-1000 数量 2 ロット 123456 出荷日 2026/10/01", 10)])
    up = client.post("/api/upload", files=[("files", ("label.pdf", data, "application/pdf"))], headers=H).json()
    d = _wait(client, up["results"][0]["id"])
    assert d["status"] == "skipped"
