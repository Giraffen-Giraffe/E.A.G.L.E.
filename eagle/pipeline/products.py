"""商品マスタ照合(型番の確定)。

照合の順序(上ほど厳密。あいまい一致は最後):
  1. 完全一致
  2. 正規化一致     … 全半角・大文字小文字・区切り記号(- . / 空白 等)の差を吸収
  3. 接頭辞除去     … 型番の前に付く管理用の接頭辞(設定)を外して一致
  4. 誤読耐性一致   … O↔0 / I↔1 / L↔1 を同一視(マスタ内で衝突しない場合だけ使う)
  5. 略記展開       … 注文書上の略記 → 正式表記(設定)
  6. 最長包含       … 型番欄に品名等が連結されている時、含まれる正規コードのうち最長のもの
  7. あいまい一致   … 文字列類似度(高い閾値・長さ近接・短い型番は対象外)
  8. 単価による再確定 … 標準単価が行の単価と合わず、それを含む長い型番の単価が一致するならそちら
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

_SEP_RE = re.compile(r"[\s\-‐‑‒–—―ー－・･.．_/／]+")


def code_key(s) -> str:
    """型番比較キー: NFKC + 大文字化 + 区切り記号除去(日本語は保持)。"""
    return _SEP_RE.sub("", unicodedata.normalize("NFKC", str(s or "")).upper())


def ocr_key(s) -> str:
    return code_key(s).replace("O", "0").replace("I", "1").replace("L", "1")


@dataclass
class Product:
    code: str
    name: str = ""
    unit: str = ""
    price: float | None = None


@dataclass
class Match:
    product: Product | None
    method: str            # exact / normalized / prefix / ocr / abbrev / contained / fuzzy / price / none


class ProductIndex:
    def __init__(self, products: list[Product], internal_prefixes: list[str] | None = None,
                 abbreviations: dict[str, str] | None = None, fuzzy_ratio: float = 0.86):
        self.products = [p for p in products if str(p.code).strip()]
        self.by_code: dict[str, Product] = {}
        self.by_key: dict[str, Product] = {}
        for p in self.products:
            self.by_code.setdefault(p.code, p)
            self.by_key.setdefault(code_key(p.code), p)
        # 誤読耐性キーは、マスタ内で別品目同士が同じキーになる(衝突する)ものを除外して使う
        ok: dict[str, list[Product]] = {}
        for p in self.products:
            ok.setdefault(ocr_key(p.code), []).append(p)
        self.by_ocr = {k: v[0] for k, v in ok.items() if len({code_key(x.code) for x in v}) == 1}
        self.prefixes = sorted({code_key(x) for x in (internal_prefixes or []) if code_key(x)}, key=len, reverse=True)
        self.abbrev = {ocr_key(k): ocr_key(v) for k, v in (abbreviations or {}).items() if code_key(k) and code_key(v)}
        self.fuzzy_ratio = fuzzy_ratio

    def __len__(self) -> int:
        return len(self.products)

    def price_map(self) -> dict[str, float]:
        return {code_key(p.code): float(p.price or 0) for p in self.products}

    def prompt_text(self, limit: int) -> str:
        rows = []
        for p in self.products[:limit]:
            price = "" if p.price is None else (str(int(p.price)) if float(p.price).is_integer() else str(p.price))
            rows.append(f"{p.code}\t{(p.name or '')[:30]}\t{p.unit}\t{price}")
        return "\n".join(rows)

    def _strip_prefix(self, k: str) -> str | None:
        for pre in self.prefixes:
            if k.startswith(pre) and len(k) > len(pre):
                return k[len(pre):]
        return None

    def lookup(self, code: str) -> Match:
        code = str(code or "").strip()
        if not code or not self.products:
            return Match(None, "none")
        if code in self.by_code:
            return Match(self.by_code[code], "exact")
        k = code_key(code)
        if k in self.by_key:
            return Match(self.by_key[k], "normalized")
        rest = self._strip_prefix(k)
        if rest and rest in self.by_key:
            return Match(self.by_key[rest], "prefix")
        ok = ocr_key(code)
        if ok in self.by_ocr:
            return Match(self.by_ocr[ok], "ocr")
        rest_o = self._strip_prefix(ok)
        if rest_o and rest_o in self.by_ocr:
            return Match(self.by_ocr[rest_o], "ocr")
        for ab, full in self.abbrev.items():
            if ok.startswith(ab) and not ok.startswith(full):
                cand = full + ok[len(ab):]
                if cand in self.by_ocr:
                    return Match(self.by_ocr[cand], "abbrev")
        contained = [mk for mk in self.by_ocr if len(mk) >= 6 and mk in ok]
        if contained:
            return Match(self.by_ocr[max(contained, key=len)], "contained")
        if len(k) >= 5:
            best, best_r = None, 0.0
            for p in self.products:
                mk = code_key(p.code)
                if not mk or abs(len(mk) - len(k)) > 2:
                    continue
                r = difflib.SequenceMatcher(None, k, mk).ratio()
                if r > best_r:
                    best, best_r = p, r
            if best is not None and best_r >= self.fuzzy_ratio:
                return Match(best, "fuzzy")
        return Match(None, "none")

    def price_fix(self, code: str, unit_price: float | None) -> str | None:
        """読んだ型番の標準単価が行の単価と合わず、その型番を含む長い正規コードの標準単価が
        一致するならそちらを返す(短い型番が長い型番の一部であるときの取り違え救済)。"""
        if unit_price is None:
            return None
        ck = code_key(code)
        p0 = self.by_key.get(ck)
        if not ck or p0 is None:
            return None
        if p0.price and abs(float(p0.price) - unit_price) < 1:
            return None
        longer = [p for k, p in self.by_key.items()
                  if len(k) > len(ck) and ck in k and p.price and abs(float(p.price) - unit_price) < 1]
        return longer[0].code if len(longer) == 1 else None
