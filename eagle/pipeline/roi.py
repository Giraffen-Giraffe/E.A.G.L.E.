"""ROI(読取領域)による焦点読取。

全ページを一度に読むと、小さい文字・かすれ・似た欄の取り違え(発注元と届け先の逆転、
担当者の取りこぼし)が起きやすい。テンプレートに登録した領域だけを切り出して読み直すことで
精度を上げる。ROI はユーザーがあとから画面で追加できる。

ROI の定義(テンプレートの rois 配列の各要素):
  id, label        … 識別子と表示名
  field            … 書き込み先。標準項目/追加項目のキー、または @buyer / @ship_to / @line_items
  page             … 対象ページ(1始まり)。@line_items は 0 で全ページ
  x, y, w, h       … ページ相対の座標(0..1)。登録時のページ基準
  mode             … auto(テキスト層があればそれ、無ければVLM) / text / vlm
  merge            … override(読めたら上書き) / fallback(全体読取で空の時だけ)
  instruction      … VLM への補足指示(例: この欄には発注担当者の氏名だけが書かれている)
  regex            … 値の書式。合わない読みは捨てる
  pad              … 切り出しの余白(ページ相対)
テンプレートの anchor が content の場合は、紙面の印字範囲を基準に位置を補正する(スキャン位置のズレ対策)。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..fields import PARTY_KEYS, header_field_map
from ..vlm import VlmClient, image_to_b64
from . import prompts
from .imaging import content_bbox, crop_rel, map_box, upscale_small
from .loader import Page
from .normalize import clean_str, nfkc

LOG = logging.getLogger("eagle.roi")

PARTY_LABELS = {"@buyer": "発注元(注文を出した側)", "@ship_to": "納品先(届け先)"}


@dataclass
class RoiResult:
    roi_id: str
    label: str
    field: str
    page: int
    value: object           # 単一項目: str / ブロック: dict / 明細: dict(order_numbers, line_items)
    source: str             # text / vlm / none
    box: tuple[float, float, float, float]
    merge: str = "override"
    note: str = ""

    def as_dict(self) -> dict:
        return {"roi_id": self.roi_id, "label": self.label, "field": self.field, "page": self.page,
                "value": self.value, "source": self.source, "box": list(self.box), "merge": self.merge,
                "note": self.note}


def roi_box_for_page(roi: dict, template: dict, page: Page, cache: dict[int, tuple | None]) -> tuple:
    box = (float(roi.get("x", 0)), float(roi.get("y", 0)), float(roi.get("w", 0)), float(roi.get("h", 0)))
    if str(template.get("anchor") or "page") != "content" or page.image is None:
        return box
    ref_all = (template.get("reference") or {}).get("content_bbox") or {}
    ref = ref_all.get(str(roi.get("page") or 1)) or ref_all.get("1")
    if page.index not in cache:
        cache[page.index] = content_bbox(page.image)
    cur = cache[page.index]
    return map_box(box, tuple(ref) if ref else None, cur)


def _validate(value: str, regex: str) -> tuple[str, str]:
    if not regex or not value:
        return value, ""
    try:
        pat = re.compile(regex)
    except re.error:
        return value, "書式(正規表現)が不正のため検証を省略"
    v = nfkc(value).strip()
    if pat.fullmatch(v):
        return v, ""
    m = pat.search(v)
    if m:
        return m.group(0), "書式に合う部分を採用"
    return "", f"書式に合わない読み『{value}』を破棄"


def read_roi(roi: dict, template: dict, page: Page, client: VlmClient | None, cache: dict,
             custom_line=None, master_text: str = "") -> RoiResult:
    field = str(roi.get("field") or "")
    label = str(roi.get("label") or field)
    box = roi_box_for_page(roi, template, page, cache)
    mode = str(roi.get("mode") or "auto")
    merge = str(roi.get("merge") or "override")
    instruction = str(roi.get("instruction") or "")
    rid = str(roi.get("id") or label)
    pad = float(roi.get("pad") if roi.get("pad") not in (None, "") else 0.006)
    res = RoiResult(rid, label, field, page.index, "" if not field.startswith("@") else {}, "none", box, merge)

    # テキスト層(電子PDF)から読む: 単一項目のみ。正確で速い
    if field and not field.startswith("@") and mode in ("auto", "text") and page.has_text_layer:
        txt = clean_str(page.text_in_box(box))
        if txt:
            val, note = _validate(txt, str(roi.get("regex") or ""))
            res.value, res.source, res.note = val, "text", note
            if val or mode == "text":
                return res
    if mode == "text" or client is None or page.image is None:
        return res

    crop = crop_rel(page.image, box, pad)
    if crop is None:
        res.note = "領域が小さすぎるか範囲外です"
        return res
    img = image_to_b64(upscale_small(crop))
    if field == "@line_items":
        data = client.chat_json(prompts.SYSTEM, prompts.items_prompt(master_text, custom_line or [],
                                                                     template.get("prompt_hint", ""),
                                                                     page_no=page.index, region=True),
                                [img], prompts.items_schema(custom_line or []),
                                want=lambda d: bool(d.get("line_items")))
        res.value = {"order_numbers": data.get("order_numbers") or [], "line_items": data.get("line_items") or []}
        res.source = "vlm"
        return res
    if field in PARTY_LABELS:
        data = client.chat_json(prompts.SYSTEM, prompts.roi_party_prompt(PARTY_LABELS[field], instruction), [img],
                                prompts.PARTY_SCHEMA, want=lambda d: any(str(d.get(k) or "").strip() for k in PARTY_KEYS))
        value = {k: clean_str(data.get(k)) for k in PARTY_KEYS}
        for k in ("address", "tel", "fax"):   # 『4 - 5 - 6』のような区切りの空白を詰める
            value[k] = re.sub(r"\s*([-‐－ー])\s*(?=\d)", "-", value[k])
        if page.has_text_layer:
            value["_text"] = page.text_in_box(box)
        res.value, res.source = value, "vlm"
        return res
    fmap = header_field_map()
    f = fmap.get(field)
    lbl = f.label if f else label
    inst = instruction or (f.description if f else "")
    data = client.chat_json(prompts.SYSTEM, prompts.roi_field_prompt(lbl, inst), [img], prompts.VALUE_SCHEMA,
                            want=lambda d: bool(str(d.get("value") or "").strip()))
    val, note = _validate(clean_str(data.get("value")), str(roi.get("regex") or ""))
    res.value, res.source, res.note = val, "vlm", note
    return res


def run_rois(template: dict, pages: list[Page], client: VlmClient | None, custom_line=None,
             master_text: str = "", progress=None) -> list[RoiResult]:
    results: list[RoiResult] = []
    rois = [r for r in (template.get("rois") or []) if r.get("enabled", True) and r.get("field")]
    cache: dict[int, tuple | None] = {}
    by_index = {p.index: p for p in pages}
    for n, roi in enumerate(rois, start=1):
        pno = int(roi.get("page") or (0 if roi.get("field") == "@line_items" else 1))
        targets = [p for p in pages if p.image is not None] if pno == 0 else [by_index[pno]] if pno in by_index else []
        for page in targets:
            try:
                results.append(read_roi(roi, template, page, client, cache, custom_line, master_text))
            except Exception as exc:  # noqa: BLE001  ROI の失敗は全体読取の結果へフォールバック
                if exc.__class__.__name__ == "VlmUnavailable":
                    raise
                LOG.warning("ROI『%s』の読取に失敗: %s", roi.get("label"), exc)
        if progress:
            progress(n / max(1, len(rois)))
    return results
