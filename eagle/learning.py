"""学習(人の修正を記憶して次回から自動で直す)。

  - 値の置換   : 『エンジンがこう読んだ値』→『人が直した値』。同じ読みが出たら置き換える
  - 既定値     : 空欄を同じ値で埋める修正がテンプレート内で一定回数たまったら、既定値として補う
  - 型番の置換 : 明細の型番の読み → 正しい型番(マスタ照合より前に適用)
  - 照合実績   : 確定した書類の顧客コードを数え、顧客照合で同点付近の優先度に使う
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import func

from .db import Correction, Document, LineItem, utcnow
from .pipeline.normalize import squash
from .pipeline.products import code_key

LINE_CODE_FIELD = "line.product_code"
# 学習の対象外(書類ごとに必ず違う値)
NON_LEARNABLE = {"order_number", "order_date", "delivery_date", "customer_order_number", "subtotal", "tax",
                 "total", "document_title", "notes"}


def _key(field: str, value) -> str:
    v = "" if value is None else str(value)
    return code_key(v) if field == LINE_CODE_FIELD else squash(v)


def _upsert(s, template_id: int, field: str, original, corrected) -> None:
    orig_key = _key(field, original)
    row = (s.query(Correction)
           .filter(Correction.template_id == template_id, Correction.field == field, Correction.original == orig_key)
           .first())
    if row is None:
        s.add(Correction(template_id=template_id, field=field, original=orig_key,
                         original_raw="" if original is None else str(original), corrected=str(corrected)))
    elif row.corrected == str(corrected):
        row.count += 1
        row.updated_at = utcnow()
    else:
        # 直し方が変わった場合は新しい直し方で数え直す
        row.corrected, row.count, row.updated_at = str(corrected), 1, utcnow()


def record_corrections(s, doc: Document) -> int:
    """書類の現在値とエンジン出力を比べ、修正を学習に記録する(同じ修正は書類ごとに1回だけ数える)。"""
    learned = dict((doc.raw_extraction or {}).get("_learned") or {})
    tid = int(doc.template_id or 0)
    n = 0
    ext = doc.extracted_header or {}
    for field, new in (doc.header or {}).items():
        if field in NON_LEARNABLE or field.startswith("_"):
            continue
        old = ext.get(field)
        if _key(field, old) == _key(field, new) or new in (None, ""):
            continue
        tag = f"h:{field}"
        if learned.get(tag) == str(new):
            continue
        _upsert(s, tid, field, old if old is not None else "", new)
        learned[tag] = str(new)
        n += 1
    for li in doc.lines:
        ex = li.extracted or {}
        if not ex:
            continue
        old_code = ex.get("product_code") or ""
        if li.product_code and _key(LINE_CODE_FIELD, old_code) != _key(LINE_CODE_FIELD, li.product_code):
            tag = f"l:{_key(LINE_CODE_FIELD, li.raw_code or old_code)}"
            if learned.get(tag) != li.product_code:
                # 型番は様式に依らないので全体(0)に記録する
                _upsert(s, 0, LINE_CODE_FIELD, li.raw_code or old_code, li.product_code)
                if old_code and li.raw_code and code_key(old_code) != code_key(li.raw_code):
                    _upsert(s, 0, LINE_CODE_FIELD, old_code, li.product_code)
                learned[tag] = li.product_code
                n += 1
    raw = dict(doc.raw_extraction or {})
    raw["_learned"] = learned
    doc.raw_extraction = raw
    return n


def header_rules(s, template_id: int | None, min_count_default: int = 2) -> tuple[dict, dict]:
    """(置換 {(field, key): corrected}, 既定値 {field: value})。テンプレート固有 → 全体 の順で優先。"""
    tids = [int(template_id or 0)] + ([0] if template_id else [])
    fixes: dict[tuple[str, str], str] = {}
    defaults: dict[str, str] = {}
    rows = (s.query(Correction).filter(Correction.enabled.is_(True), Correction.template_id.in_(tids),
                                       Correction.field != LINE_CODE_FIELD).all())
    rows.sort(key=lambda r: 0 if r.template_id == (template_id or 0) else 1)
    for r in rows:
        if r.original == "":
            if template_id and r.template_id == template_id and r.count >= min_count_default:
                defaults.setdefault(r.field, r.corrected)
            continue
        fixes.setdefault((r.field, r.original), r.corrected)
    return fixes, defaults


def code_rules(s) -> dict[str, str]:
    rows = s.query(Correction).filter(Correction.enabled.is_(True), Correction.field == LINE_CODE_FIELD).all()
    return {r.original: r.corrected for r in rows if r.original}


def apply_header_rules(header: dict, fixes: dict, defaults: dict) -> list[str]:
    notes = []
    for field, value in list(header.items()):
        if field in NON_LEARNABLE or value in (None, ""):
            continue
        fixed = fixes.get((field, squash(value)))
        if fixed is not None and fixed != value:
            header[field] = fixed
            notes.append(f"学習補正[{field}]: '{value}' → '{fixed}'")
    for field, value in defaults.items():
        if header.get(field) in (None, ""):
            header[field] = value
            notes.append(f"学習既定値[{field}]: '{value}'")
    return notes


def customer_history(s) -> dict:
    """確定済み書類の顧客コード利用実績。"""
    hist: dict = {"buyer": Counter(), "ship_to": Counter(), "pair": defaultdict(Counter)}
    rows = s.query(Document.buyer_code, Document.ship_to_code).filter(Document.status == "confirmed").all()
    for b, e in rows:
        if b:
            hist["buyer"][b] += 1
        if e:
            hist["ship_to"][e] += 1
        if b and e:
            hist["pair"][b][e] += 1
    return hist


def learning_stats(s, limit: int = 500) -> dict:
    """確定書類の項目別の修正率(どの項目にROIを足すと効くかの目安)。"""
    docs = (s.query(Document).filter(Document.status == "confirmed")
            .order_by(Document.confirmed_at.desc()).limit(limit).all())
    per_field: dict[str, list[int]] = defaultdict(lambda: [0, 0])     # field -> [修正数, 件数]
    per_tpl: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    line_total = line_fixed = 0
    for d in docs:
        ext, cur = d.extracted_header or {}, d.header or {}
        tname = d.template.name if d.template else "(テンプレートなし)"
        changed_doc = 0
        for f in set(ext) | set(cur):
            if f.startswith("_"):
                continue
            per_field[f][1] += 1
            if squash(ext.get(f)) != squash(cur.get(f)):
                per_field[f][0] += 1
                changed_doc = 1
        for li in d.lines:
            line_total += 1
            ex = li.extracted or {}
            if ex and (code_key(ex.get("product_code")) != code_key(li.product_code)
                       or ex.get("quantity") != li.quantity or ex.get("unit_price") != li.unit_price):
                line_fixed += 1
                changed_doc = 1
        per_tpl[tname][1] += 1
        per_tpl[tname][0] += changed_doc
    fields = sorted(({"field": f, "corrected": c, "total": t, "rate": round(c / t, 3) if t else 0}
                     for f, (c, t) in per_field.items() if t), key=lambda x: -x["rate"])
    templates = [{"template": k, "corrected_docs": c, "docs": t, "accuracy": round(1 - c / t, 3) if t else None}
                 for k, (c, t) in per_tpl.items()]
    total_rules = s.query(func.count(Correction.id)).scalar() or 0
    return {"confirmed_docs": len(docs), "fields": fields, "templates": templates,
            "lines": {"total": line_total, "corrected": line_fixed,
                      "accuracy": round(1 - line_fixed / line_total, 3) if line_total else None},
            "rules": total_rules}
