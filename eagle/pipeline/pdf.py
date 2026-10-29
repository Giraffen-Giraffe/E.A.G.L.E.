"""PDF の描画と文字情報(テキスト層)の取得。

PDFium(Chrome の PDF エンジン)を pypdfium2(Apache-2.0 / BSD-3-Clause)経由で使う。
座標はすべて「表示される向き」のページ相対(左上原点 0..1)に揃える。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

# PDFium はスレッドセーフではないため、同時に1つだけ扱う
_LOCK = threading.RLock()

TITLE_TOP_RATIO = 0.15     # 表題とみなす範囲: ページ上端からの割合
TITLE_SIZE_RATIO = 0.9     # または最大級の文字サイズ(最大に対する割合)


@dataclass
class PdfPageData:
    image: Image.Image
    text: str
    words: list[tuple[float, float, float, float, str]] = field(default_factory=list)
    title_spans: list[str] = field(default_factory=list)


def _rotate(u: float, v: float, rot: int) -> tuple[float, float]:
    """回転前のページ相対座標(左上原点)を、表示の向き(時計回り rot 度)へ写す。"""
    if rot == 90:
        return 1 - v, u
    if rot == 180:
        return 1 - u, 1 - v
    if rot == 270:
        return v, 1 - u
    return u, v


@dataclass
class _Run:
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    text: str = ""

    def add(self, box: tuple[float, float, float, float], ch: str, size: float) -> None:
        self.x0, self.y0 = min(self.x0, box[0]), min(self.y0, box[1])
        self.x1, self.y1 = max(self.x1, box[2]), max(self.y1, box[3])
        self.size = max(self.size, size)
        self.text += ch


def _rotate_box(run: _Run, rot: int) -> _Run:
    pts = [_rotate(run.x0, run.y0, rot), _rotate(run.x1, run.y1, rot)]
    return _Run(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts),
                run.size, run.text)


def _extract_text(page, textpage) -> tuple[str, list[_Run], list[_Run]]:
    """(全文, 単語, 行)。単語は空白・改行・大きな字間で区切る(表の欄の境界に空白が無い PDF が多い)。
    まとめる処理は回転前の向き(文字の並ぶ向き)で行い、最後に表示の向きへ写す。"""
    l, b, r, t = page.get_cropbox()
    w, h = (r - l) or 1.0, (t - b) or 1.0
    rot = page.get_rotation() % 360
    n = textpage.count_chars()
    words: list[_Run] = []
    cur: _Run | None = None
    last_x1 = 0.0
    for i in range(n):
        cp = pdfium_c.FPDFText_GetUnicode(textpage, i)
        ch = chr(cp) if cp else ""
        if not ch or ch.isspace() or ord(ch) in (0, 0xFFFE, 0xFFFF):
            cur = None
            continue
        cl, cb, cr, ct = textpage.get_charbox(i)
        size = float(pdfium_c.FPDFText_GetFontSize(textpage, i)) or (ct - cb)
        box = ((cl - l) / w, (t - ct) / h, (cr - l) / w, (t - cb) / h)
        if cur is not None:
            same_line = abs((box[1] + box[3]) / 2 - (cur.y0 + cur.y1) / 2) < max(cur.y1 - cur.y0, box[3] - box[1]) * 0.6
            if not same_line or box[0] - last_x1 > (size / w) * 0.8:
                cur = None
        if cur is None:
            cur = _Run(*box, size=size)
            words.append(cur)
        cur.add(box, ch, size)
        last_x1 = box[2]
    # 単語を行にまとめる(表題の判定用)
    lines: list[_Run] = []
    for group in group_lines(words):
        first = group[0]
        ln = _Run(first.x0, first.y0, first.x1, first.y1, first.size, first.text)
        for wd in group[1:]:
            ln.add((wd.x0, wd.y0, wd.x1, wd.y1), " " + wd.text, wd.size)
        lines.append(ln)
    if rot:
        words = [_rotate_box(x, rot) for x in words]
        lines = [_rotate_box(x, rot) for x in lines]
    return textpage.get_text_range(), words, lines


def group_lines(items: list) -> list[list]:
    """x0/y0/x1/y1 を持つ要素を行ごとにまとめ、行内は左から並べる。
    文字の大きさが違うと上端の位置がずれるため、縦の中心が近いものを同じ行とみなす。"""
    lines: list[dict] = []
    for it in sorted(items, key=lambda x: (x.y0 + x.y1) / 2):
        cy, hgt = (it.y0 + it.y1) / 2, it.y1 - it.y0
        if lines and abs(cy - lines[-1]["cy"]) < max(hgt, lines[-1]["h"]) * 0.6:
            lines[-1]["items"].append(it)
        else:
            lines.append({"cy": cy, "h": hgt, "items": [it]})
    return [sorted(ln["items"], key=lambda x: x.x0) for ln in lines]


def _title_spans(lines: list[_Run]) -> list[str]:
    if not lines:
        return []
    mx = max(ln.size for ln in lines)
    return [ln.text.strip() for ln in lines if ln.text.strip()
            and (ln.y0 < TITLE_TOP_RATIO or ln.size >= mx * TITLE_SIZE_RATIO)]


def load_pdf(data: bytes, dpi: int = 300, max_pages: int = 30) -> list[PdfPageData]:
    out: list[PdfPageData] = []
    with _LOCK:
        doc = pdfium.PdfDocument(data)
        try:
            for i in range(min(len(doc), max_pages)):
                page = doc[i]
                try:
                    bitmap = page.render(scale=dpi / 72.0, may_draw_forms=True)
                    img = bitmap.to_pil().convert("RGB")
                    bitmap.close()
                    tp = page.get_textpage()
                    try:
                        text, words, lines = _extract_text(page, tp)
                    finally:
                        tp.close()
                    out.append(PdfPageData(
                        image=img, text=text,
                        words=[(wd.x0, wd.y0, wd.x1, wd.y1, wd.text) for wd in words],
                        title_spans=_title_spans(lines)))
                finally:
                    page.close()
        finally:
            doc.close()
    return out
