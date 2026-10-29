"""VLM が返した明細の後処理。純関数(外部への副作用なし)。

  ① 税・合計などの集計行を除去          ② 「〜他」で終わる合計行を除去
  ③ 負値ガード(罫線→負号の誤読救済)    ④ 控えページ(同じ明細の再掲)を除去
  ⑤ テキスト層に送料の語が無い送料行(幻)を除去
  ⑥ 商品コードの確定(送料の正規化・注文番号の混入除去・マスタ照合・学習した置換)
  ⑦ 金額=単価×数量 の補完と整合チェック、重複候補・同一コード異金額・送料複数行の警告
警告は自動削除せず「要確認」として残す(誤って正しい行を消さないため)。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .normalize import compact_ordernum, is_junk_ordernum, nfkc, parse_num, squash
from .products import Match, ProductIndex, code_key

_SHIPPING_NOTE_RE = re.compile(r"送料\s*(?:を|は|も)?\s*(?:込|無料|別|含|サービス|ｻｰﾋﾞｽ|不要|当社負担|弊社負担)")


@dataclass
class CleanContext:
    summary_keywords: list[str] = field(default_factory=list)
    shipping_keywords: list[str] = field(default_factory=list)
    shipping_code: str = "送料"
    internal_prefixes: list[str] = field(default_factory=list)
    products: ProductIndex | None = None
    doc_text: str = ""
    learned_codes: dict[str, str] = field(default_factory=dict)   # code_key(読み) -> 正しいコード
    custom_keys: list[str] = field(default_factory=list)


def _blob(it: dict) -> str:
    return " ".join(str(it.get(k, "") or "") for k in ("corrected_code", "product_name", "raw_code"))


def _is_internal(code: str, prefixes: list[str]) -> bool:
    c = squash(code).lstrip("(")
    return any(c.startswith(squash(p)) for p in prefixes if p)


def _is_shipping(it: dict, ctx: CleanContext) -> bool:
    blob = _blob(it)
    if _SHIPPING_NOTE_RE.search(nfkc(blob)):
        return False   # 『送料込み』『送料無料』等は商品行の但し書き
    b = nfkc(blob)
    return any(nfkc(k) in b for k in ctx.shipping_keywords if k)


def resolve_code(it: dict, ctx: CleanContext) -> tuple[str, Match | None]:
    """この行に採用する商品コードと、マスタ照合結果を返す。"""
    code, match = _resolve_code(it, ctx)
    fixed = ctx.learned_codes.get(code_key(code)) if code else None
    if fixed and fixed != code:   # 確定したコード自体を人が別のコードに直していた(送料の行など)
        m = ctx.products.lookup(fixed) if ctx.products else None
        return (m.product.code if m and m.product else fixed), (m if m and m.product else Match(None, "learned"))
    return code, match


def _resolve_code(it: dict, ctx: CleanContext) -> tuple[str, Match | None]:
    if _is_shipping(it, ctx):
        return ctx.shipping_code, None
    corrected = str(it.get("corrected_code") or "").strip()
    product = str(it.get("product_name") or "").strip()
    raw = str(it.get("raw_code") or "").strip()
    # 行の注文番号がコード欄に混入することがある → 同じトークンを取り除く
    ono = squash(it.get("order_number"))
    if ono:
        def _drop(s: str) -> str:
            return " ".join(t for t in s.split() if squash(t) != ono).strip()
        corrected, raw = _drop(corrected), _drop(raw)

    # 学習済みの置換(人が過去に直した読み → 正しいコード)
    for cand in (raw, corrected):
        k = code_key(cand)
        if k and k in ctx.learned_codes:
            fixed = ctx.learned_codes[k]
            m = ctx.products.lookup(fixed) if ctx.products else None
            return (m.product.code if m and m.product else fixed), (m if m and m.product else Match(None, "learned"))

    chosen = ""
    pidx = ctx.products
    if pidx is not None and len(pidx):
        rk, ck, pk = code_key(raw), code_key(corrected), code_key(product)
        # VLM はマスタへ寄せる「補正」で長い型番を短い型番に縮めることがある。
        # 紙面どおりの raw がマスタに一致する、またはより長い正規コードを含むならそちらを採る。
        if rk and rk in pidx.by_key and rk != ck:
            chosen = pidx.by_key[rk].code
        else:
            contained = [k for k in pidx.by_key if len(k) >= 6 and (k in rk or k in pk)]
            if contained:
                best = max(contained, key=len)
                if best != ck and len(best) > len(ck):
                    chosen = pidx.by_key[best].code
    if not chosen:
        if corrected and not _is_internal(corrected, ctx.internal_prefixes):
            chosen = corrected
        else:
            for cand in (raw, product, corrected):
                if cand and not _is_internal(cand, ctx.internal_prefixes):
                    m = re.search(r"[A-Za-z0-9][A-Za-z0-9\-./_]{2,}", nfkc(cand))
                    chosen = m.group(0) if m else cand
                    break
            chosen = chosen or corrected or raw
    if pidx is None or not len(pidx):
        return chosen, None
    m = pidx.lookup(chosen)
    if m.product is None and raw and raw != chosen:
        m2 = pidx.lookup(raw)
        if m2.product is not None:
            m = m2
    if m.product is not None:
        fix = pidx.price_fix(m.product.code, parse_num(it.get("unit_price")))
        if fix:
            return fix, Match(pidx.by_key[code_key(fix)], "price")
        return m.product.code, m
    return chosen, m


def clean_line_items(items: list, ctx: CleanContext) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    rows: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            warnings.append(f"除去[形式不正の明細]: {str(it)[:40]}")
            continue
        rows.append({**it, "_q": parse_num(it.get("quantity", it.get("qty"))),
                     "_u": parse_num(it.get("unit_price")), "_a": parse_num(it.get("amount"))})

    # 空行(何も読めていない行)
    rows = [r for r in rows if any(str(r.get(k) or "").strip() for k in ("raw_code", "corrected_code", "product_name"))
            or r["_a"] is not None]

    # ① 集計行
    step: list[dict] = []
    for it in rows:
        text = _blob(it)
        if any(kw in text for kw in ctx.summary_keywords if kw) and not _is_shipping(it, ctx):
            warnings.append(f"除去[税/合計行]: {text.strip()[:40]} (金額={it.get('amount')})")
            continue
        step.append(it)

    # ② 「〜他」
    step2: list[dict] = []
    for it in step:
        name = str(it.get("product_name", "") or "") + str(it.get("raw_code", "") or "")
        if re.search(r"他\s*$", name) or " 他" in name or "他]" in name:
            warnings.append(f"除去[合計らしき行(〜他)]: {name.strip()[:40]}")
            continue
        step2.append(it)

    # ③ 負値
    for it in step2:
        neg = False
        for k in ("_q", "_u", "_a"):
            if it[k] is not None and it[k] < 0:
                it[k] = abs(it[k])
                neg = True
        if neg:
            warnings.append(f"要確認[負の値を絶対値化]: {it.get('product_name') or it.get('corrected_code')}")

    def _sig(it: dict) -> tuple:
        return (squash(it.get("order_number")), resolve_code(it, ctx)[0], it["_q"], it["_u"], it["_a"])

    # ④ 控えページ
    pages = sorted({int(it.get("page") or 0) for it in step2})
    if len(pages) > 1:
        seen: set[tuple] = set()
        kept: list[dict] = []
        for p in pages:
            cur = [it for it in step2 if int(it.get("page") or 0) == p]
            sigs = [_sig(it) for it in cur]
            if seen and sigs and all(s in seen for s in sigs):
                warnings.append(f"除去[控えページ]: {p}ページ目の明細{len(cur)}行は前ページと同一内容")
                continue
            kept.extend(cur)
            seen.update(sigs)
        step2 = kept

    # ⑤ 送料の幻
    if ctx.doc_text.strip():
        doc = nfkc(ctx.doc_text)
        if not any(nfkc(k) in doc for k in ctx.shipping_keywords if k):
            kept = []
            for it in step2:
                if _is_shipping(it, ctx):
                    warnings.append(f"除去[送料の行が本文に無い]: 金額={it.get('amount')}")
                    continue
                kept.append(it)
            step2 = kept

    # ⑥⑦ 確定と整合
    out: list[dict] = []
    seen_keys: set[tuple] = set()
    by_code: dict[str, list[float | None]] = defaultdict(list)
    shipping_rows = 0
    for i, it in enumerate(step2, start=1):
        code, match = resolve_code(it, ctx)
        q, u, a = it["_q"], it["_u"], it["_a"]
        if a is None and u is not None and q is not None:
            a = round(u * q, 2)
        if u is None and a is not None and q:
            u = round(a / q, 2)
        if u is not None and q and a is not None:
            if abs(u * q - a) > max(1.0, 0.01 * abs(a)):
                warnings.append(f"要確認[金額≠単価×数量]: {code} 単価{u:g}×数量{q:g}≠金額{a:g}")
        order_no = compact_ordernum(it.get("order_number"))
        if is_junk_ordernum(order_no):
            order_no = ""
        key = (order_no, code, q, a)
        if key in seen_keys:
            warnings.append(f"要確認[重複行の可能性]: {code} 金額={a}")
        seen_keys.add(key)
        by_code[code].append(a)
        is_ship = code == ctx.shipping_code or _is_shipping(it, ctx)
        shipping_rows += int(is_ship)
        prod = match.product if match is not None else None
        name = str(it.get("product_name") or "").strip()
        if prod is not None and not name:
            name = prod.name
        unit = str(it.get("unit") or "").strip() or (prod.unit if prod is not None else "")
        if ctx.products is not None and len(ctx.products) and prod is None and not is_ship and code:
            warnings.append(f"要確認[商品マスタに無いコード]: {code}")
        out.append({
            "line_no": i,
            "page": int(it.get("page") or 1),
            "order_number": order_no,
            "product_code": code,
            "raw_code": str(it.get("raw_code") or "").strip(),
            "product_name": name,
            "quantity": q,
            "unit": unit,
            "unit_price": u,
            "amount": a,
            "due_date": str(it.get("due_date") or "").strip(),
            "notes": str(it.get("notes") or "").strip(),
            "master_matched": prod is not None or is_ship,
            "match_method": "shipping" if is_ship else (match.method if match is not None else ""),
            "extra": {k: str(it.get(k) or "").strip() for k in ctx.custom_keys},
        })
    for code, amounts in by_code.items():
        vals = sorted({x for x in amounts if x is not None})
        if len(amounts) > 1 and len(vals) > 1 and code != ctx.shipping_code:
            warnings.append(f"要確認[同一コードで金額が異なる]: {code} {vals}")
    if shipping_rows > 1:
        warnings.append(f"要確認[送料が{shipping_rows}行]: 複数の注文をまとめた書類でなければ重複の可能性")
    return out, warnings
