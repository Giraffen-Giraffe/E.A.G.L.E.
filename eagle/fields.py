"""抽出項目の定義(標準項目 + ユーザー定義の追加項目)。"""
from __future__ import annotations

from dataclasses import dataclass

from .db import FieldDef, session_scope


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"          # text / number / date
    level: str = "header"       # header / line
    description: str = ""
    builtin: bool = True


HEADER_FIELDS: list[Field] = [
    Field("document_title", "書類名"),
    Field("order_number", "注文番号", description="発注元が採番した注文番号(発注番号/注文No/手配番号)"),
    Field("order_date", "注文日", "date"),
    Field("delivery_date", "希望納期", "date"),
    Field("buyer_company", "発注元 会社名"),
    Field("buyer_department", "発注元 部署・支店"),
    Field("buyer_contact", "発注担当者"),
    Field("buyer_address", "発注元 住所"),
    Field("buyer_tel", "発注元 TEL"),
    Field("buyer_fax", "発注元 FAX"),
    Field("ship_to_company", "納品先 会社名"),
    Field("ship_to_department", "納品先 部署"),
    Field("ship_to_contact", "納品先 担当者"),
    Field("ship_to_address", "納品先 住所"),
    Field("ship_to_tel", "納品先 TEL"),
    Field("end_user", "エンドユーザー"),
    Field("customer_order_number", "客先注番"),
    Field("subtotal", "小計", "number"),
    Field("tax", "消費税", "number"),
    Field("total", "合計", "number"),
    Field("payment_terms", "支払条件"),
    Field("notes", "備考"),
]

LINE_FIELDS: list[Field] = [
    Field("line_no", "行", "number", "line"),
    Field("order_number", "注文番号", level="line"),
    Field("product_code", "商品コード", level="line"),
    Field("product_name", "品名", level="line"),
    Field("quantity", "数量", "number", "line"),
    Field("unit", "単位", level="line"),
    Field("unit_price", "単価", "number", "line"),
    Field("amount", "金額", "number", "line"),
    Field("due_date", "納期", "date", "line"),
    Field("notes", "備考", level="line"),
]

# ROI で使える複合ターゲット(ブロック単位で読む)
ROI_BLOCK_TARGETS = {
    "@buyer": "発注元ブロック(会社名・部署・担当者・住所・TEL)",
    "@ship_to": "納品先ブロック(会社名・部署・担当者・住所・TEL)",
    "@line_items": "明細表の範囲",
}

PARTY_KEYS = ("company", "department", "contact", "address", "tel", "fax")

HEADER_KEYS = [f.key for f in HEADER_FIELDS]
DATE_HEADER_KEYS = [f.key for f in HEADER_FIELDS if f.kind == "date"]
NUMBER_HEADER_KEYS = [f.key for f in HEADER_FIELDS if f.kind == "number"]


def custom_fields(level: str | None = None, enabled_only: bool = True) -> list[Field]:
    with session_scope() as s:
        q = s.query(FieldDef)
        if enabled_only:
            q = q.filter(FieldDef.enabled.is_(True))
        if level:
            q = q.filter(FieldDef.level == level)
        rows = q.order_by(FieldDef.sort_order, FieldDef.id).all()
        return [Field(r.key, r.label, r.kind, r.level, r.description, False) for r in rows]


def header_field_map(include_custom: bool = True) -> dict[str, Field]:
    out = {f.key: f for f in HEADER_FIELDS}
    if include_custom:
        for f in custom_fields("header"):
            out[f.key] = f
    return out


def all_fields_payload() -> dict:
    def _d(f: Field) -> dict:
        return {"key": f.key, "label": f.label, "kind": f.kind, "level": f.level,
                "description": f.description, "builtin": f.builtin}
    return {
        "header": [_d(f) for f in HEADER_FIELDS] + [_d(f) for f in custom_fields("header")],
        "line": [_d(f) for f in LINE_FIELDS] + [_d(f) for f in custom_fields("line")],
        "roi_blocks": [{"key": k, "label": v} for k, v in ROI_BLOCK_TARGETS.items()],
    }
