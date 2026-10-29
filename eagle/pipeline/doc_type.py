"""注文書以外の書類(現品票・納品書・請求書 等)の判定。

本物の注文書の本文にも『納品書を同梱願います』のように語が出るため、本文のどこかに
語があるだけでは対象外にしない。表題(ファイル名 / テキスト層の上部・最大級の文字 /
VLM が読んだ表題)で判定し、注文系の語を同時に含むときは注文書として扱う(取りこぼしより安全)。
"""
from __future__ import annotations

import os
import re
import unicodedata

from .loader import Page


def _n(s) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


def is_non_order_title(title: str, skip_words: list[str], order_words: list[str]) -> bool:
    t = _n(title)
    if not t or not any(_n(w) and _n(w) in t for w in skip_words):
        return False
    tu = t.upper()
    # 『PURCHASE ORDER NO.』のような項目名は表題でないため、英字の注文語は ORDERFORM のみ見る
    has_order = any(_n(w) in t for w in order_words if w and not str(w).isascii()) or "ORDERFORM" in tu
    return not has_order


def detect_non_order(filename: str, pages: list[Page], skip_words: list[str],
                     order_words: list[str]) -> tuple[bool, str]:
    """ファイル名とテキスト層の表題から判定する。テキスト層の無いページがあれば VLM の表題読取に委ねる。"""
    stem = os.path.splitext(os.path.basename(filename))[0]
    if is_non_order_title(stem, skip_words, order_words):
        return True, f"ファイル名『{filename}』"
    if not pages or any(not p.has_text_layer for p in pages):
        return False, ""
    hits = []
    for p in pages:
        hit = next((t for t in p.title_spans if is_non_order_title(t, skip_words, order_words)), "")
        if not hit:
            return False, ""
        hits.append(hit)
    return True, f"表題『{hits[0]}』"
