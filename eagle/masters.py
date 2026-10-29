"""商品マスタ・顧客マスタの取込と照合用索引。

マスタは任意。取り込むと型番の確定・顧客コードの照合が有効になる。
CSV(UTF-8 / Shift_JIS)と Excel(.xlsx)に対応し、列名は代表的な表記を自動で判別する。
"""
from __future__ import annotations

import csv
import io
import threading
import unicodedata

from .db import CustomerMaster, ProductMaster, session_scope
from .pipeline.customers import CustRec, CustomerIndex, build_index
from .pipeline.normalize import parse_num
from .pipeline.products import Product, ProductIndex

PRODUCT_COLUMNS = {
    "code": ["code", "item_code", "product_code", "商品コード", "品番", "型番", "型式", "製品コード", "品目コード", "コード"],
    "name": ["name", "item_name", "product_name", "商品名", "品名", "製品名", "品目名", "名称"],
    "unit": ["unit", "単位"],
    "price": ["price", "price_standard", "unit_price", "単価", "標準単価", "定価", "販売単価", "価格"],
}
CUSTOMER_COLUMNS = {
    "code": ["code", "id", "customer_id", "customer_code", "顧客id", "顧客コード", "得意先コード", "得意先id", "取引先コード", "コード"],
    "company": ["company", "company_name", "name", "会社名", "得意先名", "顧客名", "取引先名", "社名", "名称"],
    "department": ["department", "dept", "branch", "部署", "部署名", "支店", "支店名", "営業所", "部門"],
    "person": ["person", "contact", "担当者", "担当者名", "担当"],
    "prefecture": ["prefecture", "都道府県"],
    "city": ["city", "市区町村"],
    "address": ["address", "住所", "番地", "所在地"],
    "zip": ["zip", "postal_code", "〒", "郵便番号"],
    "tel": ["tel", "phone", "電話", "電話番号"],
    "fax": ["fax", "fax番号"],
    "hidden": ["hidden", "非表示", "無効"],
}


def _norm_col(s) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip().lower().replace(" ", "").replace("_", "")


def _detect(header: list[str], spec: dict[str, list[str]]) -> dict[str, int]:
    cols = [_norm_col(h) for h in header]
    out: dict[str, int] = {}
    for key, names in spec.items():
        for n in names:
            nn = _norm_col(n)
            if nn in cols and cols.index(nn) not in out.values():
                out[key] = cols.index(nn)
                break
    return out


def read_table(data: bytes, filename: str) -> list[list[str]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [["" if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        return [r for r in rows if any(r)]
    text = None
    for enc in ("utf-8-sig", "cp932", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
    dialect = csv.excel_tab if text.count("\t") > text.count(",") else csv.excel
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on", "○", "✓", "はい", "-1")


def import_products(data: bytes, filename: str, replace: bool = True) -> dict:
    rows = read_table(data, filename)
    if not rows:
        raise ValueError("ファイルが空です")
    cols = _detect(rows[0], PRODUCT_COLUMNS)
    if "code" not in cols:
        raise ValueError("商品コードの列が見つかりません(列名の例: 商品コード / 品番 / 型番 / code)")
    items = []
    seen = set()
    for r in rows[1:]:
        def g(k):
            i = cols.get(k)
            return r[i] if i is not None and i < len(r) else ""
        code = g("code").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        items.append(ProductMaster(code=code, name=g("name"), unit=g("unit"), price=parse_num(g("price"))))
    with session_scope() as s:
        if replace:
            s.query(ProductMaster).delete()
        s.add_all(items)
    invalidate()
    return {"imported": len(items), "columns": {k: rows[0][i] for k, i in cols.items()}}


def import_customers(data: bytes, filename: str, replace: bool = True) -> dict:
    rows = read_table(data, filename)
    if not rows:
        raise ValueError("ファイルが空です")
    cols = _detect(rows[0], CUSTOMER_COLUMNS)
    if "company" not in cols:
        raise ValueError("会社名の列が見つかりません(列名の例: 会社名 / 得意先名 / company)")
    items = []
    for n, r in enumerate(rows[1:], start=1):
        def g(k):
            i = cols.get(k)
            return r[i] if i is not None and i < len(r) else ""
        if not g("company").strip():
            continue
        items.append(CustomerMaster(
            code=g("code").strip() or str(n), company=g("company"), department=g("department"), person=g("person"),
            prefecture=g("prefecture"), city=g("city"), address=g("address"), zip=g("zip"), tel=g("tel"),
            fax=g("fax"), hidden=_truthy(g("hidden")),
        ))
    with session_scope() as s:
        if replace:
            s.query(CustomerMaster).delete()
        s.add_all(items)
    invalidate()
    return {"imported": len(items), "columns": {k: rows[0][i] for k, i in cols.items()}}


# ── 索引キャッシュ ───────────────────────────────────────────────────────
_lock = threading.Lock()
_products: tuple[tuple, ProductIndex] | None = None
_customers: CustomerIndex | None = None


def invalidate() -> None:
    global _products, _customers
    with _lock:
        _products = None
        _customers = None


def product_index(extraction_settings: dict, fuzzy_ratio: float = 0.86) -> ProductIndex:
    global _products
    key = (tuple(extraction_settings.get("internal_code_prefixes") or []),
           tuple(sorted((extraction_settings.get("code_abbreviations") or {}).items())), fuzzy_ratio)
    with _lock:
        if _products is not None and _products[0] == key:
            return _products[1]
    with session_scope() as s:
        prods = [Product(r.code, r.name or "", r.unit or "", r.price) for r in s.query(ProductMaster).order_by(ProductMaster.id)]
    idx = ProductIndex(prods, list(key[0]), dict(key[1]), fuzzy_ratio)
    with _lock:
        _products = (key, idx)
    return idx


def customer_index() -> CustomerIndex:
    global _customers
    with _lock:
        if _customers is not None:
            return _customers
    with session_scope() as s:
        recs = [CustRec(code=r.code, company=r.company, dept=r.department or "", person=r.person or "",
                        pref=r.prefecture or "", city=r.city or "", addr=r.address or "", zip=r.zip or "",
                        tel=r.tel or "", hidden=bool(r.hidden), order=r.id).finalize()
                for r in s.query(CustomerMaster).order_by(CustomerMaster.id)]
    idx = build_index(recs)
    with _lock:
        _customers = idx
    return idx
