"""抽出パイプラインの純関数のテスト(VLM・DB 不要)。"""
from eagle.pipeline.customers import CustRec, DocEvidence, build_index, resolve
from eagle.pipeline.doc_type import is_non_order_title
from eagle.pipeline.normalize import (is_junk_ordernum, normalize_date, parse_num, verify_ordernum_with_text)
from eagle.pipeline.postprocess import CleanContext, clean_line_items
from eagle.pipeline.products import Product, ProductIndex
from eagle.pipeline.templates import match_template
from eagle.vlm.client import parse_json_loose

SHIP = ["送料", "運賃"]
SUMMARY = ["消費税", "合計", "小計"]


def test_parse_num():
    assert parse_num("¥1,234-") == 1234
    assert parse_num("１２，３００円") == 12300
    assert parse_num("△500") == -500
    assert parse_num("") is None
    assert parse_num("最短") is None


def test_normalize_date():
    assert normalize_date("2026年10月27日") == "2026-10-27"
    assert normalize_date("令和8年1月5日") == "2026-01-05"
    assert normalize_date("R8.1.5") == "2026-01-05"
    assert normalize_date("26/03/09") == "2026-03-09"
    # 年なし: 注文月より前の月は翌年
    assert normalize_date("1/07", "2026-11-20") == "2027-01-07"
    assert normalize_date("12/24", "2026-11-20") == "2026-12-24"
    assert normalize_date("最短") == ""


def test_junk_ordernum():
    for junk in ("21-03-18;09:18", "2026/10/27", "1/2", "001", "06-1234-5678", ""):
        assert is_junk_ordernum(junk), junk
    for ok in ("PO-2026-10-0315", "5512340001", "AB12345"):
        assert not is_junk_ordernum(ok), ok


def test_verify_ordernum_with_text():
    text = "注文番号 9081726354\n... フッター 90826354"
    assert verify_ordernum_with_text("90826354", text) == "9081726354"
    assert verify_ordernum_with_text("9081726354", text) == "9081726354"


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1,}\n```') == {"a": 1}
    assert parse_json_loose('結果は {"b": [1, 2]} です') == {"b": [1, 2]}
    assert parse_json_loose("") == {}


def _pidx(**kw):
    return ProductIndex([Product("AB-1000", "ユニット", "台", 45000), Product("AB-1000H", "高耐久", "台", 62000),
                         Product("XY-200-B", "ケーブル", "本", 1200), Product("MAXUNIT-LAN", "x", "台", 9000)], **kw)


def test_product_lookup_chain():
    idx = _pidx(internal_prefixes=["ZZ"], abbreviations={"MX": "MAXUNIT"})
    assert idx.lookup("AB-1000").method == "exact"
    assert idx.lookup("ab 1000").product.code == "AB-1000"            # 正規化
    assert idx.lookup("ZZ-XY200B").product.code == "XY-200-B"         # 接頭辞
    assert idx.lookup("XY-2OO-B").product.code == "XY-200-B"          # O↔0
    assert idx.lookup("MX-LAN").product.code == "MAXUNIT-LAN"         # 略記
    assert idx.lookup("AB-1000Hケーブル付").product.code == "AB-1000H"  # 最長包含
    assert idx.lookup("QQ-999").product is None
    # 単価が長い型番を示す
    assert idx.price_fix("AB-1000", 62000) == "AB-1000H"


def test_clean_line_items():
    ctx = CleanContext(summary_keywords=SUMMARY, shipping_keywords=SHIP, shipping_code="送料", products=_pidx(),
                       custom_keys=[])
    items = [
        {"raw_code": "AB-1OOO", "corrected_code": "AB-1000", "product_name": "ユニット", "quantity": "2", "unit_price": "45,000", "amount": "90,000", "page": 1},
        {"raw_code": "", "corrected_code": "", "product_name": "送料", "quantity": "1", "unit_price": "1500", "amount": "1500", "page": 1},
        {"raw_code": "", "corrected_code": "", "product_name": "消費税", "quantity": "", "unit_price": "", "amount": "9150", "page": 1},
        {"raw_code": "XY-200-B", "corrected_code": "XY-200-B", "product_name": "ケーブル(送料込み)", "quantity": "3", "unit_price": "1200", "amount": "", "page": 1},
        # 2ページ目が1ページ目の控え
        {"raw_code": "AB-1OOO", "corrected_code": "AB-1000", "product_name": "ユニット", "quantity": "2", "unit_price": "45,000", "amount": "90,000", "page": 2},
        {"raw_code": "", "corrected_code": "", "product_name": "送料", "quantity": "1", "unit_price": "1500", "amount": "1500", "page": 2},
        {"raw_code": "XY-200-B", "corrected_code": "XY-200-B", "product_name": "ケーブル(送料込み)", "quantity": "3", "unit_price": "1200", "amount": "", "page": 2},
    ]
    lines, warnings = clean_line_items(items, ctx)
    assert [ln["product_code"] for ln in lines] == ["AB-1000", "送料", "XY-200-B"]
    assert lines[2]["amount"] == 3600                 # 金額の補完
    assert lines[2]["product_code"] != "送料"          # 『送料込み』は送料行ではない
    assert any("控えページ" in w for w in warnings)
    assert any("税/合計" in w for w in warnings)


def test_phantom_shipping_removed_with_text_layer():
    ctx = CleanContext(summary_keywords=SUMMARY, shipping_keywords=SHIP, doc_text="品番 AB-1000 数量 2 合計 90000")
    lines, warnings = clean_line_items([
        {"corrected_code": "AB-1000", "quantity": "2", "unit_price": "45000", "amount": "90000"},
        {"product_name": "送料", "quantity": "1", "unit_price": "1500", "amount": "1500"},
    ], ctx)
    assert len(lines) == 1
    assert any("送料" in w for w in warnings)


def test_learned_code_applied_before_master():
    ctx = CleanContext(shipping_keywords=SHIP, products=_pidx(), learned_codes={"QQ999": "AB-1000H"})
    lines, _ = clean_line_items([{"raw_code": "QQ-999", "corrected_code": "QQ-999", "quantity": "1"}], ctx)
    assert lines[0]["product_code"] == "AB-1000H"


def _customers():
    recs = [
        CustRec("C001", "株式会社テスト商事", "大阪支店", "山田 太郎", "大阪府", "大阪市北区", "梅田1-2-3", "530-0001", "06-1234-5678", order=1),
        CustRec("C002", "株式会社テスト商事", "大阪支店", "鈴木 一郎", "大阪府", "大阪市北区", "梅田1-2-3", "530-0001", "06-1234-5678", order=2),
        CustRec("C003", "株式会社テスト商事", "東京本社", "", "東京都", "港区", "芝9-9-9", "100-0005", "03-1111-2222", order=3),
        CustRec("C010", "株式会社ダミー物流", "東京センター", "", "東京都", "江東区", "豊洲4-5-6", "135-0061", "03-9876-5432", order=4),
    ]
    return build_index([r.finalize() for r in recs])


def test_customer_resolution_picks_person():
    idx = _customers()
    ev = DocEvidence("株式会社テスト商事 大阪支店 鈴木 一郎 06-1234-5678", "")
    r = resolve(ev, idx, {}, role="buyer", min_score=3.0, contact="鈴木 一郎")
    assert r is not None and r.code == "C002"


def test_customer_resolution_needs_evidence_for_ship_to():
    idx = _customers()
    # 社名だけでは確定しない(人手に回す)
    assert resolve(DocEvidence("株式会社ダミー物流", ""), idx, {}, role="ship_to") is None
    r = resolve(DocEvidence("株式会社ダミー物流 東京センター 〒135-0061", ""), idx, {}, role="ship_to")
    assert r is not None and r.code == "C010"


def test_template_matching():
    tpls = [{"id": 1, "name": "A", "match_keywords": ["テスト商事"], "enabled": True},
            {"id": 2, "name": "B", "match_keywords": ["グローバルトレーディング"], "enabled": True},
            {"id": 3, "name": "C", "match_keywords": ["re:PO-\\d{4}"], "enabled": True}]
    assert match_template(tpls, ["株式会社テスト商事 大阪支店"]).template["id"] == 1
    # 長い語は1文字の誤読を許容
    assert match_template(tpls, ["グローバルトレーデイング株式会社"]).template["id"] == 2
    assert match_template(tpls, [], text="注文番号 PO-2026-1").template["id"] == 3
    assert match_template(tpls, ["無関係株式会社"]) is None


def test_non_order_title():
    skip, order = ["現品票", "納品書"], ["注文", "発注"]
    assert is_non_order_title("現品票", skip, order)
    assert is_non_order_title("納 品 書", skip, order)
    assert not is_non_order_title("注文書兼納品書", skip, order)
    assert not is_non_order_title("注文書", skip, order)


def test_pdf_text_layer_lines_and_rotation():
    """テキスト層: 文字の大きさが違っても同じ行は左から並び、回転したページでも座標が表示の向きになる。"""
    import io

    import pypdfium2 as pdfium
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    from eagle.pipeline.loader import load_pdf_bytes

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    c.setFont("HeiseiKakuGo-W5", 9)
    c.drawString(60, 842 - 100, "納入先")
    c.setFont("HeiseiKakuGo-W5", 12)
    c.drawString(120, 842 - 101, "株式会社サンプル 物流センター")
    c.setFont("HeiseiKakuGo-W5", 24)
    c.drawString(250, 842 - 50, "注 文 書")
    c.showPage()
    c.save()
    page = load_pdf_bytes(buf.getvalue(), 72, 1)[0]
    assert page.has_text_layer is False or page.text  # 短い本文
    assert page.text_in_box((0.05, 0.09, 0.9, 0.04)) == "納入先 株式会社サンプル 物流センター"
    assert "注 文 書" in page.title_spans

    doc = pdfium.PdfDocument(buf.getvalue())
    doc[0].set_rotation(90)
    out = io.BytesIO()
    doc.save(out)
    doc.close()
    rot = load_pdf_bytes(out.getvalue(), 72, 1)[0]
    assert rot.image.width > rot.image.height          # 横長に表示される
    w = next(w for w in rot.words if w.text.startswith("納入先"))
    assert w.x0 > 0.8                                  # 上にあった語は右端へ
