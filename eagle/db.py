"""アプリ内データベース(SQLAlchemy)。

既定は data/eagle.db (SQLite)。サーバー版で複数台から共有したい場合は
storage.database_url に PostgreSQL / MySQL 等の URL を指定すれば同じスキーマで動く。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# ── 書類 ────────────────────────────────────────────────────────────────
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    stored_name: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload / watch / api
    source_path: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    file_type: Mapped[str] = mapped_column(String(16), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    # queued / processing / extracted / needs_review / confirmed / skipped / failed
    status: Mapped[str] = mapped_column(String(24), index=True, default="queued")
    status_detail: Mapped[str] = mapped_column(Text, default="")
    doc_title: Mapped[str] = mapped_column(String(256), default="")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"), nullable=True)
    template_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # header: 現在値(人が修正した後の値) / extracted_header: エンジンが出した値(学習の比較元)
    header: Mapped[dict] = mapped_column(JSON, default=dict)
    extracted_header: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_extraction: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    buyer_code: Mapped[str] = mapped_column(String(128), default="")
    ship_to_code: Mapped[str] = mapped_column(String(128), default="")
    model_name: Mapped[str] = mapped_column(String(256), default="")
    processing_sec: Mapped[float] = mapped_column(Float, default=0.0)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    confirmed_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    lines: Mapped[list["LineItem"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="LineItem.line_no")
    template: Mapped["Template | None"] = relationship()


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int] = mapped_column(Integer, default=1)
    order_number: Mapped[str] = mapped_column(String(128), default="")
    product_code: Mapped[str] = mapped_column(String(256), default="")
    raw_code: Mapped[str] = mapped_column(String(256), default="")
    product_name: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), default="")
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    due_date: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    master_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    match_method: Mapped[str] = mapped_column(String(64), default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)        # 追加項目(行レベル)
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)    # エンジン出力時点の値(学習用)

    document: Mapped[Document] = relationship(back_populates="lines")


# ── テンプレート(発行元ごとの様式定義 + ROI) ─────────────────────────────
class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    match_keywords: Mapped[list] = mapped_column(JSON, default=list)
    anchor: Mapped[str] = mapped_column(String(16), default="content")   # page / content(印字範囲基準)
    reference: Mapped[dict] = mapped_column(JSON, default=dict)       # ROI 登録時の基準画像情報
    sample_document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rois: Mapped[list] = mapped_column(JSON, default=list)
    prompt_hint: Mapped[str] = mapped_column(Text, default="")
    defaults: Mapped[dict] = mapped_column(JSON, default=dict)        # 空欄時に補う値
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)       # 常に上書きする値
    patterns: Mapped[dict] = mapped_column(JSON, default=dict)        # 項目ごとの書式(正規表現)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FieldDef(Base):
    """ユーザー定義の追加抽出項目。"""
    __tablename__ = "field_defs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    level: Mapped[str] = mapped_column(String(16), default="header")  # header / line
    kind: Mapped[str] = mapped_column(String(16), default="text")     # text / number / date
    description: Mapped[str] = mapped_column(Text, default="")        # VLM への読み取り指示
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


# ── マスタ(任意。ユーザーが自社データを取り込む) ───────────────────────────
class ProductMaster(Base):
    __tablename__ = "product_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(256), index=True)
    name: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class CustomerMaster(Base):
    __tablename__ = "customer_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), index=True)
    company: Mapped[str] = mapped_column(String(512))
    department: Mapped[str] = mapped_column(String(512), default="")
    person: Mapped[str] = mapped_column(String(256), default="")
    prefecture: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(128), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    zip: Mapped[str] = mapped_column(String(16), default="")
    tel: Mapped[str] = mapped_column(String(64), default="")
    fax: Mapped[str] = mapped_column(String(64), default="")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


# ── 学習(人の修正の記憶) ───────────────────────────────────────────────────
class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(Integer, default=0, index=True)  # 0 = 全体
    field: Mapped[str] = mapped_column(String(64), index=True)
    original: Mapped[str] = mapped_column(Text, default="")     # 正規化済みキー
    original_raw: Mapped[str] = mapped_column(Text, default="")
    corrected: Mapped[str] = mapped_column(Text, default="")
    count: Mapped[int] = mapped_column(Integer, default=1)
    applied: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ── 処理キュー ──────────────────────────────────────────────────────────
class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="queued")  # queued/running/done/failed/cancelled
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── 利用者(サーバー版) ─────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), default="editor")  # admin / editor / viewer
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SessionToken(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── 設定・外部DB連携・イベント ──────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class DbTarget(Base):
    """外部データベースへの書き出し先。"""
    __tablename__ = "db_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(Text)
    header_table: Mapped[str] = mapped_column(String(256), default="eagle_orders")
    line_table: Mapped[str] = mapped_column(String(256), default="eagle_order_lines")
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    create_tables: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual / confirm / extract
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    kind: Mapped[str] = mapped_column(String(32), default="")
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    message: Mapped[str] = mapped_column(Text, default="")


# ── エンジン/セッション ───────────────────────────────────────────────────
_engine = None
_Session: sessionmaker | None = None
_lock = threading.Lock()


def init_db(url: str):
    global _engine, _Session
    with _lock:
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
        Base.metadata.create_all(_engine)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def engine():
    return _engine


@contextmanager
def session_scope():
    if _Session is None:
        raise RuntimeError("database is not initialised")
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def log_event(message: str, kind: str = "", level: str = "info", document_id: int | None = None,
              username: str = "") -> None:
    try:
        with session_scope() as s:
            s.add(Event(message=message[:4000], kind=kind, level=level, document_id=document_id, username=username))
    except Exception:  # noqa: BLE001  ログ書込失敗で本処理を止めない
        pass


__all__ = [
    "Base", "Document", "LineItem", "Template", "FieldDef", "ProductMaster", "CustomerMaster", "Correction",
    "Job", "User", "SessionToken", "Setting", "DbTarget", "Event", "init_db", "engine", "session_scope",
    "log_event", "utcnow", "select",
]
