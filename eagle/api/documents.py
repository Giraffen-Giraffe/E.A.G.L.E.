"""書類(注文書)の API: 取込・一覧・詳細・修正・確定・再処理・出力。"""
from __future__ import annotations

import io
import urllib.parse
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import Text, cast, func, or_

from .. import learning
from ..db import Document, Job, LineItem, session_scope, utcnow, log_event
from ..pipeline.normalize import parse_num
from ..settings import get_settings
from ..storage import delete_doc_files, original_path, page_image_path
from ..services import export as exporter
from ..services.external_db import auto_push
from ..services.ingest import UnsupportedFile, ingest_bytes
from ..services.jobs import worker
from .deps import editor, viewer

router = APIRouter(prefix="/api")

LINE_EDITABLE = ("line_no", "page", "order_number", "product_code", "raw_code", "product_name", "quantity", "unit",
                 "unit_price", "amount", "due_date", "notes")
NUM_LINE = {"quantity", "unit_price", "amount"}


def _iso(v):
    return v.isoformat() if v else None


def _summary(d: Document, line_count: int = 0, amount: float | None = None) -> dict:
    h = d.header or {}
    return {
        "id": d.id, "filename": d.filename, "status": d.status,
        "status_label": exporter.STATUS_LABELS.get(d.status, d.status), "status_detail": d.status_detail[:300],
        "order_number": h.get("order_number", ""), "order_date": h.get("order_date", ""),
        "buyer_company": h.get("buyer_company", ""), "ship_to_company": h.get("ship_to_company", ""),
        "buyer_code": d.buyer_code, "total": h.get("total", ""), "line_count": line_count, "amount": amount,
        "template": d.template.name if d.template else "", "template_id": d.template_id,
        "warnings": len([w for w in (d.warnings or []) if str(w).startswith("要確認")]),
        "source": d.source, "created_at": _iso(d.created_at), "processed_at": _iso(d.processed_at),
        "confirmed_at": _iso(d.confirmed_at), "synced_at": _iso(d.synced_at), "page_count": d.page_count,
    }


def _filtered(s, status: str = "", q: str = "", template_id: int | None = None, date_from: str = "",
              date_to: str = "", ids: str = ""):
    query = s.query(Document)
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        return query.filter(Document.id.in_(id_list))
    if status:
        query = query.filter(Document.status.in_(status.split(",")))
    if template_id:
        query = query.filter(Document.template_id == template_id)
    if date_from:
        query = query.filter(Document.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Document.created_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    if q:
        like = f"%{q}%"
        # JSON 列は DB ごとに検索方法が違うため、文字列化して部分一致で探す
        sub = s.query(LineItem.document_id).filter(or_(LineItem.product_code.ilike(like),
                                                       LineItem.product_name.ilike(like)))
        query = query.filter(or_(Document.filename.ilike(like), cast(Document.header, Text).ilike(like),
                                 Document.id.in_(sub)))
    return query


@router.get("/documents")
def list_documents(status: str = "", q: str = "", template_id: int | None = None, date_from: str = "",
                   date_to: str = "", page: int = 1, page_size: int = Query(50, le=500), sort: str = "-id",
                   user: dict = Depends(viewer)):
    with session_scope() as s:
        query = _filtered(s, status, q, template_id, date_from, date_to)
        total = query.count()
        col = getattr(Document, sort.lstrip("-"), Document.id)
        query = query.order_by(col.desc() if sort.startswith("-") else col.asc())
        docs = query.offset((max(1, page) - 1) * page_size).limit(page_size).all()
        ids = [d.id for d in docs]
        agg = dict()
        if ids:
            for doc_id, cnt, amt in (s.query(LineItem.document_id, func.count(LineItem.id), func.sum(LineItem.amount))
                                     .filter(LineItem.document_id.in_(ids)).group_by(LineItem.document_id)):
                agg[doc_id] = (cnt, amt)
        counts = dict(s.query(Document.status, func.count(Document.id)).group_by(Document.status).all())
        return {"items": [_summary(d, *agg.get(d.id, (0, None))) for d in docs], "total": total,
                "page": page, "page_size": page_size, "counts": counts}


def _line_dict(li: LineItem) -> dict:
    return {"id": li.id, **{k: getattr(li, k) for k in LINE_EDITABLE}, "master_matched": li.master_matched,
            "match_method": li.match_method, "extra": li.extra or {}, "extracted": li.extracted or {}}


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, user: dict = Depends(viewer)):
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "書類が見つかりません")
        raw = d.raw_extraction or {}
        job = s.query(Job).filter(Job.document_id == doc_id).order_by(Job.id.desc()).first()
        return {
            **_summary(d, len(d.lines)),
            "header": d.header or {}, "extracted_header": d.extracted_header or {}, "warnings": d.warnings or [],
            "lines": [_line_dict(li) for li in d.lines], "doc_title": d.doc_title, "model_name": d.model_name,
            "processing_sec": d.processing_sec, "file_type": d.file_type, "confirmed_by": d.confirmed_by,
            "pages": raw.get("pages", []), "content_bbox": raw.get("content_bbox", {}),
            "buyer_match": raw.get("buyer_match"), "ship_to_match": raw.get("ship_to_match"),
            "template_match": raw.get("template_match", ""), "roi": raw.get("roi", []),
            "timings": raw.get("timings", {}), "vlm_calls": raw.get("vlm_calls", 0),
            "companies": (raw.get("header") or {}).get("companies", []), "template_locked": d.template_locked,
            "job": {"id": job.id, "status": job.status, "progress": job.progress, "message": job.message} if job else None,
        }


def _apply_edits(d: Document, body: dict) -> None:
    if isinstance(body.get("header"), dict):
        h = dict(d.header or {})
        for k, v in body["header"].items():
            if isinstance(k, str) and not k.startswith("_"):
                h[k] = v if not isinstance(v, str) else v.strip()
        d.header = h
        d.buyer_code, d.ship_to_code = str(h.get("buyer_code") or ""), str(h.get("ship_to_code") or "")
    if isinstance(body.get("lines"), list):
        existing = {li.id: li for li in d.lines}
        keep = []
        for n, row in enumerate(body["lines"], start=1):
            li = existing.get(row.get("id")) if row.get("id") else None
            if li is None:
                li = LineItem(document_id=d.id, extracted={})
                d.lines.append(li)
            for k in LINE_EDITABLE:
                if k in row:
                    v = row[k]
                    if k in NUM_LINE:
                        v = parse_num(v)
                    elif k in ("line_no", "page"):
                        v = int(parse_num(v) or 0)
                    else:
                        v = "" if v is None else str(v).strip()
                    setattr(li, k, v)
            li.line_no = n
            if isinstance(row.get("extra"), dict):
                li.extra = {k: v for k, v in row["extra"].items()}
            keep.append(li)
        for li in list(d.lines):
            if li not in keep:
                d.lines.remove(li)


@router.put("/documents/{doc_id}")
def update_document(doc_id: int, body: dict = Body(...), user: dict = Depends(editor)):
    confirm = bool(body.get("confirm"))
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "書類が見つかりません")
        if d.status in ("queued", "processing"):
            raise HTTPException(409, "処理中の書類は編集できません")
        _apply_edits(d, body)
        if "template_id" in body:
            d.template_id = body.get("template_id") or None
        if confirm:
            d.status, d.confirmed_at, d.confirmed_by = "confirmed", utcnow(), user["username"]
        if d.status == "confirmed" and get_settings()["learning"].get("enabled", True):
            s.flush()
            learning.record_corrections(s, d)
    log_event(("確定" if confirm else "修正") + f": 書類{doc_id}", "edit", document_id=doc_id, username=user["username"])
    if confirm:
        auto_push(doc_id, "confirm")
    return get_document(doc_id, user)


@router.post("/documents/{doc_id}/confirm")
def confirm_document(doc_id: int, user: dict = Depends(editor)):
    return update_document(doc_id, {"confirm": True}, user)


@router.post("/documents/{doc_id}/unconfirm")
def unconfirm_document(doc_id: int, user: dict = Depends(editor)):
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "書類が見つかりません")
        if d.status == "confirmed":
            d.status, d.confirmed_at, d.confirmed_by = "extracted", None, ""
    return get_document(doc_id, user)


@router.post("/documents/{doc_id}/reprocess")
def reprocess_document(doc_id: int, body: dict = Body(default={}), user: dict = Depends(editor)):
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "書類が見つかりません")
        tid = body.get("template_id")
        if "template_id" in body:
            d.template_id = tid or None
            d.template_locked = bool(tid)
    worker.enqueue(doc_id, {"force": bool(body.get("force")), **({"template_id": tid} if body.get("template_id") else {})},
                   priority=1)
    log_event(f"再処理を依頼: 書類{doc_id}", "process", document_id=doc_id, username=user["username"])
    return {"ok": True}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, user: dict = Depends(editor)):
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "書類が見つかりません")
        if d.status == "processing":
            raise HTTPException(409, "処理中の書類は削除できません")
        uid = d.uid
        s.query(Job).filter(Job.document_id == doc_id).delete()
        s.delete(d)
    delete_doc_files(uid)
    log_event(f"削除: 書類{doc_id}", "delete", username=user["username"])
    return {"ok": True}


@router.post("/documents/bulk")
def bulk_action(body: dict = Body(...), user: dict = Depends(editor)):
    ids = [int(x) for x in body.get("ids") or []]
    action = body.get("action")
    done = 0
    for i in ids:
        try:
            if action == "confirm":
                confirm_document(i, user)
            elif action == "delete":
                delete_document(i, user)
            elif action == "reprocess":
                reprocess_document(i, {}, user)
            else:
                raise HTTPException(400, "不明な操作です")
            done += 1
        except HTTPException:
            continue
    return {"done": done}


@router.get("/documents/{doc_id}/pages/{index}")
def page_image(doc_id: int, index: int, user: dict = Depends(viewer)):
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404)
        uid = d.uid
    p = page_image_path(uid, index)
    if not p.exists():
        raise HTTPException(404, "ページ画像がありません")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})


@router.get("/documents/{doc_id}/original")
def original_file(doc_id: int, user: dict = Depends(viewer)):
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404)
        uid, stored, name = d.uid, d.stored_name, d.filename
    return FileResponse(original_path(uid, stored), filename=name)


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...), template_id: int | None = Form(None),
                 user: dict = Depends(editor)):
    results = []
    for f in files:
        data = await f.read()
        try:
            results.append(ingest_bytes(f.filename or "upload", data, "upload", username=user["username"],
                                        template_id=template_id))
        except UnsupportedFile as exc:
            results.append({"filename": f.filename, "error": str(exc)})
    return {"results": results}


@router.get("/export")
def export(format: str = "xlsx", layout: str = "flat", ids: str = "", status: str = "", q: str = "",
           template_id: int | None = None, date_from: str = "", date_to: str = "", user: dict = Depends(viewer)):
    with session_scope() as s:
        docs = _filtered(s, status, q, template_id, date_from, date_to, ids).order_by(Document.id).all()
        for d in docs:          # セッションを閉じる前に関連を読み込む
            _ = d.lines, d.template
        if format == "csv":
            enc = get_settings()["export"].get("csv_encoding") or "utf-8-sig"
            data, media, ext = exporter.to_csv(docs, layout, enc), "text/csv", "csv"
        else:
            data = exporter.to_xlsx(docs)
            media, ext = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    name = f"EAGLE_注文書_{datetime.now():%Y%m%d_%H%M}.{ext}"
    log_event(f"出力: {len(docs)}件 ({ext})", "export", username=user["username"])
    return StreamingResponse(io.BytesIO(data), media_type=media, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})


@router.get("/jobs")
def jobs(user: dict = Depends(viewer)):
    return worker.status()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: dict = Depends(editor)):
    return {"ok": worker.cancel(job_id)}


@router.get("/fields/active")
def active_fields(user: dict = Depends(viewer)):
    from ..fields import all_fields_payload
    return all_fields_payload()

