"""値の正規化(数値・日付・注文番号)。純関数のみ。"""
from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date


def nfkc(s) -> str:
    return unicodedata.normalize("NFKC", "" if s is None else str(s))


def squash(s) -> str:
    """空白を除いて大文字化(比較用)。"""
    return re.sub(r"\s+", "", nfkc(s)).upper()


def clean_str(s) -> str:
    if s is None:
        return ""
    t = str(s).strip()
    # 壊れた JSON の名残(端の記号)を落とす
    return t.strip(" \t{}[]\"'`")


# ── 数値 ────────────────────────────────────────────────────────────────
def parse_num(s) -> float | None:
    """'¥1,234-' '1,234円' '△500' '(1,000)' 等を数値に。読めなければ None。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = nfkc(s).strip()
    if not t:
        return None
    neg = t.startswith(("△", "▲", "-", "−")) or (t.startswith("(") and t.endswith(")"))
    t = re.sub(r"[^\d.]", "", t.replace(",", ""))
    if t in ("", "."):
        return None
    if t.count(".") > 1:
        head, _, tail = t.rpartition(".")
        t = head.replace(".", "") + "." + tail
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


# ── 日付 ────────────────────────────────────────────────────────────────
_ERA = {"令和": 2018, "R": 2018, "平成": 1988, "H": 1988, "昭和": 1925, "S": 1925}


def _valid(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def normalize_date(s, ref_iso: str = "") -> str:
    """日付文字列を ISO(YYYY-MM-DD) へ。年が無い『月/日』は ref_iso(注文日)を基準に補完する
    (納期月 < 注文月 なら翌年)。日付として読めないもの(最短・別途相談 等)は ''。"""
    t = nfkc(s).strip()
    if not t:
        return ""
    t = t.replace("元年", "1年")
    # 和暦
    m = re.search(r"(令和|平成|昭和|[RHS])\s*(\d{1,2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})", t)
    if m:
        return _valid(_ERA[m.group(1)] + int(m.group(2)), int(m.group(3)), int(m.group(4)))
    m = re.search(r"(\d{4})\s*[./\-年]\s*(\d{1,2})\s*[./\-月]\s*(\d{1,2})", t)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", t)
    if m:
        v = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if v:
            return v
    m = re.search(r"(?<!\d)(\d{2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})(?!\d)", t)
    if m:
        return _valid(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?<!\d)(\d{1,2})\s*[./\-月]\s*(\d{1,2})(?!\d)", t)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        rm = re.match(r"(\d{4})-(\d{2})-(\d{2})", ref_iso or "")
        if rm:
            ry, rmo = int(rm.group(1)), int(rm.group(2))
            return _valid(ry + (1 if mo < rmo else 0), mo, d)
        today = date.today()
        return _valid(today.year + (1 if mo < today.month - 6 else 0), mo, d)
    return ""


# ── 注文番号 ─────────────────────────────────────────────────────────────
def is_junk_ordernum(s) -> bool:
    """FAX送信時刻・日付・ページ番号・明細の行番号など、注文番号でない値。"""
    t = nfkc(s).strip()
    if not t:
        return True
    if ";" in t or re.search(r"\d{1,2}:\d{2}", t):            # 送信時刻
        return True
    if re.fullmatch(r"\d{2,4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", t):  # 純粋な日付
        return True
    if re.fullmatch(r"\d{1,3}\s*/\s*\d{1,3}", t):              # ページ番号 1/2
        return True
    if re.fullmatch(r"0*\d{1,3}", t):                           # 行番号 001 / 2
        return True
    if re.fullmatch(r"\(?0\d{1,4}\)?[-\s]?\d{1,4}[-\s]\d{3,4}", t):  # 電話番号
        return True
    return False


def compact_ordernum(s) -> str:
    return re.sub(r"\s+", "", nfkc(s)).strip()


_ORDNO_TOKEN = r"(?<![A-Z0-9])[A-Z0-9][A-Z0-9\-/]{3,}(?![A-Z0-9])"
_ORDNO_LABEL = (r"(?:注文番号|注文NO\.?|注文No\.?|ご注文番号|御注文番号|発注番号|発注NO\.?|発注No\.?|手配番号|"
                r"注文書番号|ORDER\s?NO\.?|PO\s?NO\.?|P/O\s?NO\.?)")


def order_tokens(text: str) -> list[str]:
    t = nfkc(text).upper()
    return [x for x in re.findall(_ORDNO_TOKEN, t) if len(re.sub(r"\D", "", x)) >= 4]


def verify_ordernum_with_text(order: str, doc_text: str) -> str:
    """テキスト層(電子PDF)で注文番号を検証する。
    ラベル直後の番号が最も信頼でき、採った値と似ていればそちらへ寄せる。本文に無い値は
    本文中の最も近い番号らしい語へ寄せる(VLM の桁落ち・別欄の番号との取り違えの救済)。"""
    o = compact_ordernum(order).upper()
    if not o or not doc_text:
        return order
    t = re.sub(r"\s+", " ", nfkc(doc_text).upper())
    labeled = [m for m in re.findall(_ORDNO_LABEL + r"[\s:：#]*(" + _ORDNO_TOKEN + ")", t)
               if len(re.sub(r"\D", "", m)) >= 4]
    if labeled and o not in labeled:
        lb = max(labeled, key=lambda c: difflib.SequenceMatcher(None, o, c).ratio())
        if difflib.SequenceMatcher(None, o, lb).ratio() >= 0.6:
            return lb
    if o in t.replace(" ", ""):
        return order
    best, best_r = order, 0.0
    for c in set(order_tokens(t)):
        if abs(len(c) - len(o)) > 3:
            continue
        r = difflib.SequenceMatcher(None, o, c).ratio()
        if r > best_r:
            best, best_r = c, r
    return best if best_r >= 0.8 else order


def apply_pattern(value: str, pattern: str, pool: str) -> str:
    """値が書式(正規表現)に合わなければ、pool(テキスト層や他欄の読み)から書式に合う値を探す。"""
    if not pattern:
        return value
    try:
        pat = re.compile(pattern)
    except re.error:
        return value
    v = nfkc(value).strip()
    if v and pat.fullmatch(v):
        return v
    m = pat.search(nfkc(pool))
    return m.group(0) if m else value


# ── 電話・郵便番号 ─────────────────────────────────────────────────────────
def normalize_tel(s) -> str:
    t = nfkc(s)
    t = re.sub(r"(?i)^(tel|fax|電話|ＴＥＬ)[\s.:：]*", "", t.strip())
    return re.sub(r"[^\d\-()+]", "", t)
