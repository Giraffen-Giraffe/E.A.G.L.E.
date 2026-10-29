"""入力ファイル → ページ列。

対応形式:
  - PDF(電子PDF / スキャンPDF / FAX PDF)… 画像化 + テキスト層(あれば)
  - 画像(PNG / JPEG / TIFF(複数ページ) / BMP / WebP)
  - メール(.eml)… 添付の PDF・画像をページとして読み、本文もテキストページとして添える
  - テキスト系(.txt / .csv / .xlsx)… テキストページとして VLM に読ませる
"""
from __future__ import annotations

import email
import email.policy
import html
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from .pdf import group_lines, load_pdf

Image.MAX_IMAGE_PIXELS = 300_000_000

PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
TEXT_EXT = {".txt", ".csv", ".tsv"}
XLSX_EXT = {".xlsx", ".xlsm"}
EML_EXT = {".eml"}
SUPPORTED_EXT = PDF_EXT | IMAGE_EXT | TEXT_EXT | XLSX_EXT | EML_EXT


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class Page:
    index: int                          # 1 始まり
    image: Image.Image | None = None    # 描画画像(RGB)。テキストページは None
    text: str = ""                      # テキスト層 / テキスト文書の本文
    words: list[Word] = field(default_factory=list)   # ページ相対座標の単語(テキスト層)
    title_spans: list[str] = field(default_factory=list)
    source: str = "pdf"                 # pdf / image / text
    deskew_angle: float = 0.0

    @property
    def has_text_layer(self) -> bool:
        return self.source == "pdf" and len(self.text.strip()) >= 20

    @property
    def text_mode(self) -> bool:
        return self.image is None

    def text_in_box(self, box: tuple[float, float, float, float]) -> str:
        """テキスト層から箱(ページ相対)内の文字列を読む。単語の中心が箱内にあるものを行順に連結。"""
        x, y, w, h = box
        hits = [wd for wd in self.words
                if x <= (wd.x0 + wd.x1) / 2 <= x + w and y <= (wd.y0 + wd.y1) / 2 <= y + h]
        if not hits:
            return ""
        return "\n".join(" ".join(w_.text for w_ in ln) for ln in group_lines(hits)).strip()


def detect_type(path: Path, head: bytes | None = None) -> str:
    ext = path.suffix.lower()
    if head is None:
        with open(path, "rb") as f:
            head = f.read(8)
    if head.startswith(b"%PDF"):
        return "pdf"
    if ext in IMAGE_EXT or head[:4] in (b"\x89PNG", b"II*\x00", b"MM\x00*") or head[:3] == b"\xff\xd8\xff":
        return "image"
    if ext in XLSX_EXT:
        return "xlsx"
    if ext in EML_EXT:
        return "eml"
    if ext in TEXT_EXT:
        return "text"
    if ext in PDF_EXT:
        return "pdf"
    return "unknown"


def load_pdf_bytes(data: bytes, dpi: int, max_pages: int, start_index: int = 1) -> list[Page]:
    return [Page(index=start_index + i, image=p.image, text=p.text, words=[Word(*w) for w in p.words],
                 title_spans=p.title_spans, source="pdf")
            for i, p in enumerate(load_pdf(data, dpi, max_pages))]


def load_image_bytes(data: bytes, max_pages: int, start_index: int = 1) -> list[Page]:
    pages: list[Page] = []
    with Image.open(io.BytesIO(data)) as im:
        for i, frame in enumerate(ImageSequence.Iterator(im)):
            if i >= max_pages:
                break
            fr = ImageOps.exif_transpose(frame.copy()) if i == 0 else frame.copy()
            pages.append(Page(index=start_index + i, image=fr.convert("RGB"), source="image"))
    return pages


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp932", "utf-16", "euc-jp", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _xlsx_text(data: bytes) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"[シート: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if any(v.strip() for v in vals):
                parts.append("\t".join(vals).rstrip())
    wb.close()
    return "\n".join(parts)


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>", "\n", s)
    s = re.sub(r"(?i)</td>|</th>", "\t", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def _load_eml(data: bytes, dpi: int, max_pages: int) -> list[Page]:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    pages: list[Page] = []
    body_txt = ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        fname = part.get_filename() or ""
        payload = part.get_payload(decode=True) or b""
        if ctype == "application/pdf" or fname.lower().endswith(".pdf"):
            pages += load_pdf_bytes(payload, dpi, max_pages - len(pages), len(pages) + 1)
        elif ctype.startswith("image/") and len(payload) > 2048:
            pages += load_image_bytes(payload, max_pages - len(pages), len(pages) + 1)
        elif ctype == "text/plain" and not fname and not body_txt:
            body_txt = part.get_content()
        elif ctype == "text/html" and not fname and not body_txt:
            body_txt = _html_to_text(part.get_content())
    header = f"件名: {msg.get('subject', '')}\n差出人: {msg.get('from', '')}\n日付: {msg.get('date', '')}\n"
    body = (header + "\n" + (body_txt or "")).strip()
    # 添付が無い、または本文に注文内容らしい文章がある場合は本文もページとして読む
    if not pages or len(re.sub(r"\s", "", body_txt or "")) >= 40:
        pages.append(Page(index=len(pages) + 1, text=body, source="text"))
    return pages


def load_document(path: Path, dpi: int = 300, max_pages: int = 30) -> tuple[str, list[Page]]:
    data = path.read_bytes()
    kind = detect_type(path, data[:8])
    if kind == "pdf":
        return kind, load_pdf_bytes(data, dpi, max_pages)
    if kind == "image":
        return kind, load_image_bytes(data, max_pages)
    if kind == "xlsx":
        return kind, [Page(index=1, text=_xlsx_text(data), source="text")]
    if kind == "eml":
        return kind, _load_eml(data, dpi, max_pages)
    if kind == "text":
        return kind, [Page(index=1, text=_decode_text(data), source="text")]
    raise ValueError(f"未対応のファイル形式です: {path.name}")
