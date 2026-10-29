"""外部データベースへの書き出し(既存の業務システムの DB・共有 DB との連携)。

SQLAlchemy の接続 URL で接続先を指定する(PostgreSQL / MySQL / SQL Server / SQLite / Access 等)。
  postgresql+psycopg://user:pass@host:5432/db
  mysql+pymysql://user:pass@host:3306/db
  mssql+pyodbc://user:pass@DSN名
  sqlite:///C:/path/to/orders.db
  access+pyodbc:///?odbc_connect=DRIVER%3D%7BMicrosoft+Access+Driver+%28*.mdb%2C+*.accdb%29%7D%3BDBQ%3DC%3A%5Cpath%5Corders.accdb
列の対応(mapping)を変えれば既存テーブルにもそのまま書き込める。同じ書類を再送すると、
書類ID列で既存行を消してから書き直す(何度送っても重複しない)。
"""
from __future__ import annotations

from sqlalchemy import Column, Float, MetaData, String, Table, Text, create_engine, delete, inspect, text
from sqlalchemy.engine import make_url

from ..db import DbTarget, Document, session_scope, utcnow, log_event
from ..fields import HEADER_KEYS, LINE_FIELDS, custom_fields

DOC_KEYS = ["id", "uid", "filename", "status", "template", "created_at", "processed_at", "confirmed_at",
            "confirmed_by"]
NUMERIC = {"subtotal", "tax", "total", "quantity", "unit_price", "amount", "line_no", "page", "id", "document_id"}
LONG_TEXT = {"notes", "product_name", "buyer_address", "ship_to_address", "payment_terms"}


def default_mapping() -> dict:
    header = {k: k for k in DOC_KEYS + HEADER_KEYS + ["buyer_code", "ship_to_code"]}
    header["id"] = "eagle_id"
    for f in custom_fields("header"):
        header[f.key] = f.key
    line = {"document_id": "eagle_id"}
    line.update({f.key: f.key for f in LINE_FIELDS})
    for f in custom_fields("line"):
        line["extra." + f.key] = f.key
    return {"header": header, "line": line}


def masked_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001
        return "(不正な URL)"


def test_connection(url: str) -> dict:
    eng = create_engine(url, future=True, pool_pre_ping=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
            tables = inspect(c).get_table_names()
        return {"ok": True, "tables": tables[:200]}
    finally:
        eng.dispose()


def _col_type(src: str):
    key = src.split(".")[-1]
    if key in NUMERIC:
        return Float()
    if key in LONG_TEXT:
        return Text()
    return String(512)


def _table(md: MetaData, conn, name: str, mapping: dict[str, str], create: bool) -> Table:
    if inspect(conn).has_table(name):
        return Table(name, md, autoload_with=conn)
    if not create:
        raise RuntimeError(f"テーブル {name} がありません(『テーブルを自動作成』を有効にするか、先に作成してください)")
    cols = [Column(dst, _col_type(src)) for src, dst in mapping.items() if dst]
    t = Table(name, md, *cols)
    t.create(conn)
    return t


def _doc_value(d: Document, key: str):
    if key in DOC_KEYS:
        if key == "template":
            return d.template.name if d.template else ""
        v = getattr(d, key, "")
        return v.isoformat(sep=" ", timespec="seconds") if hasattr(v, "isoformat") else v
    return (d.header or {}).get(key, "")


def _line_value(d: Document, li, key: str):
    if key == "document_id":
        return d.id
    if key.startswith("extra."):
        return (li.extra or {}).get(key[6:], "")
    return getattr(li, key, "")


def _coerce(v, col):
    if v in (None, ""):
        return None
    try:
        if col.type.python_type in (int, float):
            return col.type.python_type(v)
    except (NotImplementedError, ValueError, TypeError):
        pass
    return v if isinstance(v, (int, float)) else str(v)


def push_documents(target_id: int, doc_ids: list[int]) -> dict:
    with session_scope() as s:
        tgt = s.get(DbTarget, target_id)
        if tgt is None:
            raise ValueError("連携先が見つかりません")
        url, htab, ltab, create = tgt.url, tgt.header_table, tgt.line_table, tgt.create_tables
        mapping = tgt.mapping or default_mapping()
        docs = s.query(Document).filter(Document.id.in_(doc_ids)).all()
        payload = []
        for d in docs:
            payload.append((d.id, {src: _doc_value(d, src) for src in mapping.get("header", {})},
                            [{src: _line_value(d, li, src) for src in mapping.get("line", {})} for li in d.lines]))
    hmap, lmap = mapping.get("header", {}), mapping.get("line", {})
    eng = create_engine(url, future=True, pool_pre_ping=True)
    try:
        with eng.begin() as conn:
            md = MetaData()
            ht = _table(md, conn, htab, hmap, create) if htab and hmap else None
            lt = _table(md, conn, ltab, lmap, create) if ltab and lmap else None
            hkey, lkey = hmap.get("id"), lmap.get("document_id")
            for doc_id, hv, lines in payload:
                if ht is not None:
                    if hkey and hkey in ht.c:
                        conn.execute(delete(ht).where(ht.c[hkey] == _coerce(doc_id, ht.c[hkey])))
                    conn.execute(ht.insert().values({dst: _coerce(hv.get(src), ht.c[dst])
                                                     for src, dst in hmap.items() if dst and dst in ht.c}))
                if lt is not None:
                    if lkey and lkey in lt.c:
                        conn.execute(delete(lt).where(lt.c[lkey] == _coerce(doc_id, lt.c[lkey])))
                    rows = [{dst: _coerce(lv.get(src), lt.c[dst]) for src, dst in lmap.items() if dst and dst in lt.c}
                            for lv in lines]
                    if rows:
                        conn.execute(lt.insert(), rows)
        err = ""
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:2000]
    finally:
        eng.dispose()
    with session_scope() as s:
        tgt = s.get(DbTarget, target_id)
        tgt.last_push_at, tgt.last_error = utcnow(), err
        if not err:
            for d in s.query(Document).filter(Document.id.in_(doc_ids)):
                d.synced_at = utcnow()
    log_event(f"外部DB『{masked_url(url)}』へ {len(payload)}件 書き出し" + (f" 失敗: {err}" if err else ""),
              "external_db", "error" if err else "info")
    if err:
        raise RuntimeError(err)
    return {"pushed": len(payload)}


def auto_push(doc_id: int, trigger: str) -> None:
    """抽出完了/確定時に、該当トリガーの連携先へ自動で書き出す。"""
    with session_scope() as s:
        ids = [t.id for t in s.query(DbTarget).filter(DbTarget.enabled.is_(True), DbTarget.trigger == trigger)]
    for tid in ids:
        try:
            push_documents(tid, [doc_id])
        except Exception:  # noqa: BLE001  エラーは連携先の last_error とイベントに残る
            pass
