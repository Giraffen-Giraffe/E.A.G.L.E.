"""xlsx / csv への出力。"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from ..db import Document
from ..fields import HEADER_FIELDS, LINE_FIELDS, custom_fields

STATUS_LABELS = {
    "queued": "待機中", "processing": "処理中", "extracted": "抽出済", "needs_review": "要確認",
    "confirmed": "確定", "skipped": "対象外", "failed": "失敗",
}

DOC_COLUMNS = [("id", "ID"), ("filename", "ファイル名"), ("status", "状態"), ("template", "テンプレート"),
               ("created_at", "取込日時"), ("confirmed_at", "確定日時")]
CODE_COLUMNS = [("buyer_code", "発注元コード"), ("ship_to_code", "納品先コード")]


def _fmt_dt(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if isinstance(v, datetime) else ""


def header_columns() -> list[tuple[str, str]]:
    cols = list(DOC_COLUMNS) + [(f.key, f.label) for f in HEADER_FIELDS] + CODE_COLUMNS
    cols += [(f.key, f.label) for f in custom_fields("header")]
    return cols


def line_columns() -> list[tuple[str, str]]:
    cols = [(f.key, f.label) for f in LINE_FIELDS]
    cols += [("extra." + f.key, f.label) for f in custom_fields("line")]
    return cols


def doc_values(d: Document) -> dict:
    h = d.header or {}
    v = {"id": d.id, "filename": d.filename, "status": STATUS_LABELS.get(d.status, d.status),
         "template": d.template.name if d.template else "", "created_at": _fmt_dt(d.created_at),
         "confirmed_at": _fmt_dt(d.confirmed_at)}
    for k, val in h.items():
        v.setdefault(k, val)
    return v


def line_values(li) -> dict:
    v = {f.key: getattr(li, f.key, "") for f in LINE_FIELDS}
    for k, val in (li.extra or {}).items():
        v["extra." + k] = val
    return v


def build_tables(docs: list[Document]) -> dict[str, tuple[list[str], list[list]]]:
    hc, lc = header_columns(), line_columns()
    orders = [[doc_values(d).get(k, "") for k, _ in hc] for d in docs]
    lines, flat = [], []
    for d in docs:
        dv = doc_values(d)
        if not d.lines:
            flat.append([dv.get(k, "") for k, _ in hc] + ["" for _ in lc])
        for li in d.lines:
            lv = line_values(li)
            lines.append([d.id, d.filename, (d.header or {}).get("order_number", "")] + [lv.get(k, "") for k, _ in lc])
            flat.append([dv.get(k, "") for k, _ in hc] + [lv.get(k, "") for k, _ in lc])
    return {
        "orders": ([lbl for _, lbl in hc], orders),
        "lines": (["書類ID", "ファイル名", "注文番号(書類)"] + [lbl for _, lbl in lc], lines),
        "flat": ([lbl for _, lbl in hc] + ["明細:" + lbl for _, lbl in lc], flat),
    }


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "○" if v else ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def to_xlsx(docs: list[Document]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    tables = build_tables(docs)
    wb = Workbook()
    sheets = [("注文書", "orders"), ("明細", "lines"), ("一覧(明細展開)", "flat")]
    fill = PatternFill("solid", fgColor="1F3A5F")
    for i, (title, key) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        cols, rows = tables[key]
        ws.append(cols)
        for r in rows:
            ws.append([_cell(v) for v in r])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = fill
            c.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        for idx, name in enumerate(cols, start=1):
            width = max([len(str(name))] + [len(str(r[idx - 1])) for r in rows[:200] if idx - 1 < len(r)])
            ws.column_dimensions[get_column_letter(idx)].width = min(48, max(8, width * 1.6 + 2))
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    c.number_format = "#,##0.##" if isinstance(c.value, float) else "#,##0"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_csv(docs: list[Document], layout: str = "flat", encoding: str = "utf-8-sig") -> bytes:
    cols, rows = build_tables(docs)[layout if layout in ("orders", "lines", "flat") else "flat"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return buf.getvalue().encode(encoding, errors="replace")
