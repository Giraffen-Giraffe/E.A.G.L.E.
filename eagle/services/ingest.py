"""書類の取込(ドラッグ&ドロップ / 監視フォルダ / API 共通)。"""
from __future__ import annotations

from pathlib import Path

from ..db import Document, session_scope, log_event
from ..pipeline.loader import SUPPORTED_EXT
from ..settings import get_settings
from ..storage import new_uid, safe_name, sha256_bytes, store_original
from .jobs import worker


class UnsupportedFile(ValueError):
    pass


def ingest_bytes(filename: str, data: bytes, source: str = "upload", source_path: str = "",
                 username: str = "", template_id: int | None = None, enqueue: bool = True) -> dict:
    name = safe_name(filename)
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise UnsupportedFile(f"未対応の形式です: {name}(対応: {', '.join(sorted(SUPPORTED_EXT))})")
    if not data:
        raise UnsupportedFile(f"空のファイルです: {name}")
    digest = sha256_bytes(data)
    if get_settings()["extraction"].get("skip_duplicates"):
        with session_scope() as s:
            dup = s.query(Document).filter(Document.sha256 == digest).first()
            if dup is not None:
                return {"id": dup.id, "filename": dup.filename, "duplicate": True, "status": dup.status}
    uid = new_uid()
    stored = store_original(uid, name, data)
    with session_scope() as s:
        doc = Document(uid=uid, filename=name, stored_name=stored, source=source, source_path=source_path,
                       sha256=digest, file_type=ext.lstrip("."), created_by=username, status="queued",
                       template_id=template_id, template_locked=bool(template_id))
        s.add(doc)
        s.flush()
        doc_id = doc.id
    if enqueue:
        worker.enqueue(doc_id)
    log_event(f"取込: {name}({source})", "ingest", document_id=doc_id, username=username)
    return {"id": doc_id, "filename": name, "duplicate": False, "status": "queued"}


def ingest_path(path: Path, source: str = "watch") -> dict:
    return ingest_bytes(path.name, path.read_bytes(), source=source, source_path=str(path))
