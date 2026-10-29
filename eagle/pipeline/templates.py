"""テンプレート(発行元ごとの様式定義)の判定。

様式そのもの(座標・注文番号の書式)ではなく、紙面に現れる社名・語でテンプレートを選ぶ。
  - 社名(VLM が列挙した会社名)に一致 … 最も強い根拠
  - テキスト層(電子PDF)に一致        … 次点
  - ファイル名に一致                   … 弱い根拠
長い識別語は 1 文字の誤読(編集距離1)まで許容する。短い語は別語との衝突を避けるため完全一致のみ。
キーワードを "re:" で始めると正規表現として扱う。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or ""))).upper()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def keyword_hit(text_norm: str, keyword: str, fuzzy: bool = False) -> bool:
    kw = str(keyword or "").strip()
    if not kw or not text_norm:
        return False
    if kw.lower().startswith("re:"):
        try:
            return re.search(kw[3:], text_norm, re.I) is not None
        except re.error:
            return False
    k = _normalize(kw)
    if k in text_norm:
        return True
    if fuzzy and len(k) >= 8:
        L = len(k)
        return any(levenshtein(text_norm[i:i + L], k) <= 1 for i in range(0, len(text_norm) - L + 1))
    return False


@dataclass
class TemplateMatch:
    template: dict
    keyword: str
    where: str
    score: float


def match_template(templates: list[dict], company_names: list[str], text: str = "", filename: str = "",
                   title: str = "", exclude_names: list[str] | None = None) -> TemplateMatch | None:
    excl = [_normalize(x) for x in (exclude_names or []) if str(x).strip()]
    names = [_normalize(n) for n in company_names if str(n).strip()]
    names = [n for n in names if not any(e and e in n for e in excl)]
    text_n = _normalize(text)
    file_n = _normalize(filename) + "|" + _normalize(title)
    best: TemplateMatch | None = None
    for t in templates:
        if not t.get("enabled", True):
            continue
        for kw in t.get("match_keywords") or []:
            score, where = 0.0, ""
            if any(keyword_hit(n, kw) for n in names):
                score, where = 3.0, "社名"
            elif any(keyword_hit(n, kw, fuzzy=True) for n in names):
                score, where = 2.5, "社名(誤読許容)"
            elif keyword_hit(text_n, kw):
                score, where = 2.0, "本文"
            elif keyword_hit(file_n, kw):
                score, where = 1.0, "ファイル名/表題"
            if not score:
                continue
            score += 0.01 * float(t.get("priority") or 0) + 0.001 * len(_normalize(kw))
            if best is None or score > best.score:
                best = TemplateMatch(t, str(kw), where, score)
    return best
