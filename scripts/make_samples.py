"""動作確認用のサンプル(架空の注文書・マスタ)を作る。

実在の会社・製品・人物とは関係のない架空データのみを出力する。
    python scripts/make_samples.py [出力先フォルダ]   (既定: samples/)
出力:
    sample_order.pdf       電子PDF(テキスト層あり)
    sample_order_scan.png  スキャン画像(わずかに傾き・ノイズあり)
    sample_order_fax.pdf   画像だけのPDF(FAX 想定)
    products.csv           商品マスタの例
    customers.csv          顧客マスタの例
"""
from __future__ import annotations

import csv
import io
import random
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FONT = "HeiseiKakuGo-W5"   # reportlab 内蔵の日本語フォント(CID)
W, H = 595, 842            # A4(pt)
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

ITEMS = [
    ("AB-1000", "制御ユニット 標準型", 2, "台", 45000, "11/15"),
    ("XY-200-B", "接続ケーブル 2m", 10, "本", 1200, "11/15"),
    ("CD-55K", "電源アダプタ", 3, "個", 3500, "11/20"),
    ("送料", "", 1, "式", 1500, ""),
]


class _Page:
    """左上原点の座標で描くための薄いラッパー(reportlab は左下原点)。"""

    def __init__(self, c: canvas.Canvas):
        self.c = c

    def text(self, x, y, s, size=10.0):
        self.c.setFont(FONT, size)
        self.c.drawString(x, H - y, s)

    def line(self, a, b, width=1.0):
        self.c.setLineWidth(width)
        self.c.line(a[0], H - a[1], b[0], H - b[1])

    def rect(self, x0, y0, x1, y1, width=1.0):
        self.c.setLineWidth(width)
        self.c.rect(x0, H - y1, x1 - x0, y1 - y0)


def _t(page: _Page, x, y, text, size=10.0):
    page.text(x, y, text, size)


def make_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(W, H))
    page = _Page(c)
    _t(page, 250, 70, "注 文 書", 22)
    _t(page, 330, 100, "注文番号: PO-2026-10-0315", 9)
    _t(page, 330, 115, "発行日: 2026年10月27日", 9)
    _t(page, 50, 120, "サンプル工業株式会社 御中", 13)
    page.line((50, 126), (250, 126))
    _t(page, 50, 150, "下記のとおり注文いたします。", 9.5)

    # 発注元ブロック
    page.rect(290, 135, 550, 232, width=0.7)
    _t(page, 298, 150, "【発注元】", 8.5)
    _t(page, 298, 166, "株式会社テスト商事 大阪支店", 11)
    _t(page, 298, 181, "〒530-0001 大阪府大阪市北区梅田1-2-3", 8)
    _t(page, 298, 195, "TEL 06-1234-5678", 8)
    _t(page, 298, 208, "FAX 06-1234-5679", 8)
    _t(page, 298, 222, "発注担当: 山田 太郎", 9)

    # 納入先ブロック
    page.rect(50, 240, 545, 285, width=0.7)
    _t(page, 58, 256, "納入先", 9)
    _t(page, 110, 256, "株式会社ダミー物流 東京センター  受付係", 10)
    _t(page, 110, 274, "〒135-0061 東京都江東区豊洲4-5-6   TEL 03-9876-5432", 8.5)

    # 明細表
    cols = [50, 75, 150, 300, 340, 370, 425, 485, 545]
    heads = ["No", "品番", "品名", "数量", "単位", "単価", "金額", "納期"]
    top, rowh = 300, 24
    rows = len(ITEMS) + 1
    for i in range(rows + 1):
        page.line((cols[0], top + i * rowh), (cols[-1], top + i * rowh), width=0.6)
    for x in cols:
        page.line((x, top), (x, top + rows * rowh), width=0.6)
    for i, h in enumerate(heads):
        _t(page, cols[i] + 4, top + 16, h, 9)
    subtotal = 0
    for n, (code, name, qty, unit, price, due) in enumerate(ITEMS, start=1):
        y = top + n * rowh + 16
        amount = qty * price
        subtotal += amount
        vals = [str(n), code, name, str(qty), unit, f"{price:,}", f"{amount:,}", due]
        for i, v in enumerate(vals):
            _t(page, cols[i] + 4, y, v, 8.8)
    tax = subtotal // 10
    y = top + rows * rowh + 22
    _t(page, 380, y, f"小計   {subtotal:,}", 9.5)
    _t(page, 380, y + 16, f"消費税 {tax:,}", 9.5)
    _t(page, 380, y + 32, f"合計   {subtotal + tax:,}", 10.5)
    _t(page, 50, y + 70, "備考: 納品書に注文番号を記載してください。", 9)
    _t(page, 50, 800, "1/1", 8)
    c.showPage()
    c.save()


def make_scan(pdf: Path, png: Path, fax_pdf: Path) -> None:
    doc = pdfium.PdfDocument(str(pdf))
    img = doc[0].render(scale=200 / 72).to_pil().convert("L")
    doc.close()
    img = img.rotate(-1.2, resample=Image.BICUBIC, expand=False, fillcolor=255)
    rnd = random.Random(7)
    px = img.load()
    for _ in range(img.width * img.height // 400):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.choice((0, 90, 160))
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    img.save(png)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    buf.seek(0)
    c = canvas.Canvas(str(fax_pdf), pagesize=(W, H))
    c.drawImage(ImageReader(buf), 0, 0, W, H)
    c.showPage()
    c.save()


def make_masters(folder: Path) -> None:
    with open(folder / "products.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["商品コード", "品名", "単位", "単価"])
        w.writerows([
            ["AB-1000", "制御ユニット 標準型", "台", 45000],
            ["AB-1000H", "制御ユニット 高耐久型", "台", 62000],
            ["XY-200-A", "接続ケーブル 1m", "本", 900],
            ["XY-200-B", "接続ケーブル 2m", "本", 1200],
            ["CD-55K", "電源アダプタ", "個", 3500],
            ["CD-55KW", "電源アダプタ 屋外型", "個", 5200],
        ])
    with open(folder / "customers.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["顧客コード", "会社名", "部署", "担当者", "都道府県", "市区町村", "住所", "郵便番号", "TEL"])
        w.writerows([
            ["C001", "株式会社テスト商事", "大阪支店", "山田 太郎", "大阪府", "大阪市北区", "梅田1-2-3", "530-0001", "06-1234-5678"],
            ["C002", "株式会社テスト商事", "大阪支店", "鈴木 一郎", "大阪府", "大阪市北区", "梅田1-2-3", "530-0001", "06-1234-5678"],
            ["C003", "株式会社テスト商事", "東京本社", "", "東京都", "港区", "芝9-9-9", "100-0005", "03-1111-2222"],
            ["C010", "株式会社ダミー物流", "東京センター", "", "東京都", "江東区", "豊洲4-5-6", "135-0061", "03-9876-5432"],
            ["C011", "株式会社ダミー物流", "大阪センター", "", "大阪府", "大阪市住之江区", "南港1-1-1", "559-0034", "06-5555-0000"],
            ["C020", "架空電機株式会社", "購買部", "佐藤 花子", "愛知県", "名古屋市中区", "栄2-2-2", "460-0008", "052-000-1111"],
        ])


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    out.mkdir(parents=True, exist_ok=True)
    make_pdf(out / "sample_order.pdf")
    make_scan(out / "sample_order.pdf", out / "sample_order_scan.png", out / "sample_order_fax.pdf")
    make_masters(out)
    print(f"サンプルを作成しました: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
