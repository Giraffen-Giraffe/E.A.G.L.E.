"""管理系 API: テンプレート/ROI・追加項目・マスタ・設定・VLM・監視・外部DB・利用者・学習・ログ。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, Response, UploadFile

from .. import APP_NAME, __version__, learning, masters
from ..config import boot
from ..db import (Correction, CustomerMaster, DbTarget, Document, Event, FieldDef, ProductMaster, Template, User,
                  session_scope, log_event)
from ..fields import HEADER_KEYS, all_fields_payload
from ..pipeline.customers import company_core
from ..pipeline.processor import template_dict
from ..pipeline.roi import run_rois
from ..settings import DEFAULTS, get_settings, update_settings
from ..storage import load_saved_pages
from ..services import auth
from ..services.external_db import default_mapping, masked_url, push_documents, test_connection
from ..services.jobs import worker
from ..services.watcher import watch_service
from ..vlm import VlmError, llama_manager, make_client, setup_runner
from ..vlm.setup import PRESETS, detect_accelerator
from .deps import SESSION_COOKIE, admin, current_user, editor, viewer

router = APIRouter(prefix="/api")


# ── 情報・認証 ────────────────────────────────────────────────────────────
@router.get("/info")
def info(request: Request):
    b = boot()
    st = get_settings()
    user = None
    try:
        user = current_user(request)
    except HTTPException:
        pass
    return {"app": APP_NAME, "version": __version__, "mode": b.mode, "auth_required": b.auth_required,
            "user": user, "vlm_backend": st["vlm"]["backend"], "needs_setup": b.auth_required and auth.user_count() == 0}


@router.post("/auth/login")
def login(request: Request, response: Response, body: dict = Body(...)):
    b = boot()
    if not b.auth_required:
        return {"user": None}
    try:
        token, user = auth.login(str(body.get("username") or ""), str(body.get("password") or ""),
                                 request.client.host if request.client else "", b.session_hours)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=b.session_hours * 3600,
                        secure=bool(b.ssl_certfile) or request.url.scheme == "https", path="/")
    log_event("ログイン", "auth", username=user["username"])
    return {"user": user}


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        auth.logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/auth/password")
def change_password(body: dict = Body(...), user: dict = Depends(current_user)):
    if not boot().auth_required:
        raise HTTPException(400, "デスクトップ版にはパスワードがありません")
    with session_scope() as s:
        u = s.get(User, user["id"])
        if u is None or not auth.verify_password(str(body.get("current") or ""), u.password_hash):
            raise HTTPException(400, "現在のパスワードが違います")
        try:
            auth.validate_password(str(body.get("new") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        u.password_hash = auth.hash_password(str(body["new"]))
    return {"ok": True}


# ── 利用者(サーバー版) ─────────────────────────────────────────────────────
def _user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role, "active": u.active,
            "last_login": u.last_login.isoformat() if u.last_login else None}


@router.get("/users")
def list_users(user: dict = Depends(admin)):
    with session_scope() as s:
        return [_user_dict(u) for u in s.query(User).order_by(User.id)]


@router.post("/users")
def add_user(body: dict = Body(...), user: dict = Depends(admin)):
    try:
        uid = auth.create_user(str(body.get("username") or ""), str(body.get("password") or ""),
                               str(body.get("role") or "editor"), str(body.get("display_name") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    log_event(f"利用者を追加: {body.get('username')}", "user", username=user["username"])
    return {"id": uid}


@router.put("/users/{user_id}")
def edit_user(user_id: int, body: dict = Body(...), user: dict = Depends(admin)):
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(404)
        if "role" in body:
            if body["role"] not in auth.ROLES:
                raise HTTPException(400, "権限が不正です")
            if u.id == user["id"] and body["role"] != "admin":
                raise HTTPException(400, "自分の管理者権限は外せません")
            u.role = body["role"]
        if "display_name" in body:
            u.display_name = str(body["display_name"])
        if "active" in body:
            if u.id == user["id"] and not body["active"]:
                raise HTTPException(400, "自分自身は無効にできません")
            u.active = bool(body["active"])
        if body.get("password"):
            try:
                auth.validate_password(str(body["password"]))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            u.password_hash = auth.hash_password(str(body["password"]))
    if body.get("password") or body.get("active") is False:
        auth.revoke_user_sessions(user_id)
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, user: dict = Depends(admin)):
    if user_id == user["id"]:
        raise HTTPException(400, "自分自身は削除できません")
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is not None:
            s.delete(u)
    return {"ok": True}


# ── テンプレート / ROI ─────────────────────────────────────────────────────
TEMPLATE_FIELDS = ("name", "description", "enabled", "priority", "match_keywords", "anchor", "reference",
                   "sample_document_id", "rois", "prompt_hint", "defaults", "overrides", "patterns")


def _clean_rois(rois) -> list[dict]:
    out = []
    for i, r in enumerate(rois or []):
        if not isinstance(r, dict):
            continue
        box = [float(r.get(k) or 0) for k in ("x", "y", "w", "h")]
        if box[2] <= 0 or box[3] <= 0:
            continue
        out.append({
            "id": str(r.get("id") or f"roi{i + 1}"), "label": str(r.get("label") or r.get("field") or ""),
            "field": str(r.get("field") or ""),
            "page": int(r.get("page") or (0 if r.get("field") == "@line_items" else 1)),
            "x": max(0.0, min(1.0, box[0])), "y": max(0.0, min(1.0, box[1])),
            "w": max(0.001, min(1.0, box[2])), "h": max(0.001, min(1.0, box[3])),
            "mode": r.get("mode") if r.get("mode") in ("auto", "text", "vlm") else "auto",
            "merge": r.get("merge") if r.get("merge") in ("override", "fallback") else "override",
            "instruction": str(r.get("instruction") or ""), "regex": str(r.get("regex") or ""),
            "pad": float(r.get("pad") if r.get("pad") not in (None, "") else 0.006),
            "enabled": bool(r.get("enabled", True)),
        })
    return out


def _apply_template(t: Template, body: dict) -> None:
    for k in TEMPLATE_FIELDS:
        if k not in body:
            continue
        v = body[k]
        if k == "rois":
            v = _clean_rois(v)
        elif k == "match_keywords":
            v = [str(x).strip() for x in (v or []) if str(x).strip()]
        elif k in ("defaults", "overrides", "patterns", "reference"):
            v = {str(a): b for a, b in (v or {}).items() if str(a)} if isinstance(v, dict) else {}
        elif k == "anchor":
            v = v if v in ("page", "content") else "page"
        elif k == "priority":
            v = int(v or 0)
        setattr(t, k, v)


@router.get("/templates")
def list_templates(user: dict = Depends(viewer)):
    from sqlalchemy import func
    with session_scope() as s:
        counts = dict(s.query(Document.template_id, func.count(Document.id)).group_by(Document.template_id).all())
        out = []
        for t in s.query(Template).order_by(Template.priority.desc(), Template.name):
            d = template_dict(t)
            d["description"] = t.description
            d["documents"] = counts.get(t.id, 0)
            d["updated_at"] = t.updated_at.isoformat() if t.updated_at else None
            out.append(d)
        return out


@router.get("/templates/{tid}")
def get_template(tid: int, user: dict = Depends(viewer)):
    with session_scope() as s:
        t = s.get(Template, tid)
        if t is None:
            raise HTTPException(404, "テンプレートが見つかりません")
        return {**template_dict(t), "description": t.description}


@router.post("/templates")
def create_template(body: dict = Body(...), user: dict = Depends(editor)):
    if not str(body.get("name") or "").strip():
        raise HTTPException(400, "名前を入力してください")
    with session_scope() as s:
        t = Template(name=str(body["name"]).strip())
        _apply_template(t, body)
        _sync_reference(s, t, None)
        s.add(t)
        s.flush()
        tid = t.id
    log_event(f"テンプレートを作成: {body['name']}", "template", username=user["username"])
    return get_template(tid, user)


@router.post("/templates/from-document/{doc_id}")
def template_from_document(doc_id: int, body: dict = Body(default={}), user: dict = Depends(editor)):
    """この書類を見本にテンプレートを作る(発注元の社名を判定キーワードに入れる)。"""
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "書類が見つかりません")
        h = d.header or {}
        company = str(h.get("buyer_company") or "")
        core = company_core(company)
        kw = re.sub(r"(株式会社|有限会社|合同会社|\(株\)|（株）|㈱)", "", company).strip() if company else ""
        name = str(body.get("name") or (kw or company) or d.filename)
        t = Template(name=name, match_keywords=[kw] if kw and len(core) >= 2 else [], sample_document_id=d.id,
                     reference={"content_bbox": (d.raw_extraction or {}).get("content_bbox", {})}, rois=[])
        s.add(t)
        s.flush()
        d.template_id = t.id
        tid = t.id
    return get_template(tid, user)


@router.put("/templates/{tid}")
def update_template(tid: int, body: dict = Body(...), user: dict = Depends(editor)):
    with session_scope() as s:
        t = s.get(Template, tid)
        if t is None:
            raise HTTPException(404, "テンプレートが見つかりません")
        before = t.sample_document_id
        _apply_template(t, body)
        _sync_reference(s, t, before)
    return get_template(tid, user)


def _sync_reference(s, t: Template, before: int | None) -> None:
    """ROI 位置補正の基準(見本書類の印字範囲)を、見本書類が変わった/未設定の時に取り直す。"""
    if not t.sample_document_id:
        return
    if t.sample_document_id == before and (t.reference or {}).get("content_bbox"):
        return
    d = s.get(Document, t.sample_document_id)
    if d is not None:
        t.reference = {"content_bbox": (d.raw_extraction or {}).get("content_bbox", {})}


@router.delete("/templates/{tid}")
def delete_template(tid: int, user: dict = Depends(editor)):
    with session_scope() as s:
        t = s.get(Template, tid)
        if t is not None:
            s.query(Document).filter(Document.template_id == tid).update({Document.template_id: None})
            s.delete(t)
    return {"ok": True}


@router.post("/templates/test")
def test_template(body: dict = Body(...), user: dict = Depends(editor)):
    """保存前の ROI を見本書類で試し読みする(VLM を使う。結果は保存しない)。"""
    doc_id = int(body.get("document_id") or 0)
    tpl = dict(body.get("template") or {})
    tpl["rois"] = _clean_rois(tpl.get("rois"))
    only = body.get("roi_ids")
    if only:
        tpl["rois"] = [r for r in tpl["rois"] if r["id"] in only]
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "見本の書類が見つかりません")
        uid = d.uid
        if not tpl.get("reference"):
            tpl["reference"] = {"content_bbox": (d.raw_extraction or {}).get("content_bbox", {})}
    pages = load_saved_pages(uid)
    if not pages:
        raise HTTPException(400, "この書類はまだ処理されていません")
    try:
        client = make_client(get_settings()["vlm"])
        results = run_rois(tpl, pages, client)
    except VlmError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"results": [r.as_dict() for r in results]}


# ── 追加項目 ─────────────────────────────────────────────────────────────
@router.get("/fields")
def get_fields(user: dict = Depends(viewer)):
    with session_scope() as s:
        custom = [{"id": f.id, "key": f.key, "label": f.label, "level": f.level, "kind": f.kind,
                   "description": f.description, "enabled": f.enabled, "sort_order": f.sort_order}
                  for f in s.query(FieldDef).order_by(FieldDef.sort_order, FieldDef.id)]
    return {**all_fields_payload(), "custom": custom}


def _field_body(f: FieldDef, body: dict) -> None:
    if "label" in body:
        f.label = str(body["label"]).strip() or f.key
    if "level" in body and body["level"] in ("header", "line"):
        f.level = body["level"]
    if "kind" in body and body["kind"] in ("text", "number", "date"):
        f.kind = body["kind"]
    for k in ("description",):
        if k in body:
            setattr(f, k, str(body[k] or ""))
    if "enabled" in body:
        f.enabled = bool(body["enabled"])
    if "sort_order" in body:
        f.sort_order = int(body["sort_order"] or 0)


@router.post("/fields")
def add_field(body: dict = Body(...), user: dict = Depends(admin)):
    key = str(body.get("key") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", key):
        raise HTTPException(400, "キーは英小文字で始まる英数字と _ で入力してください(例: project_name)")
    if key in HEADER_KEYS or key in ("buyer_code", "ship_to_code"):
        raise HTTPException(400, "標準項目と同じキーは使えません")
    with session_scope() as s:
        if s.query(FieldDef).filter(FieldDef.key == key).first():
            raise HTTPException(400, "同じキーが既にあります")
        f = FieldDef(key=key, label=str(body.get("label") or key))
        _field_body(f, body)
        s.add(f)
    return get_fields(user)


@router.put("/fields/{fid}")
def edit_field(fid: int, body: dict = Body(...), user: dict = Depends(admin)):
    with session_scope() as s:
        f = s.get(FieldDef, fid)
        if f is None:
            raise HTTPException(404)
        _field_body(f, body)
    return get_fields(user)


@router.delete("/fields/{fid}")
def delete_field(fid: int, user: dict = Depends(admin)):
    with session_scope() as s:
        f = s.get(FieldDef, fid)
        if f is not None:
            s.delete(f)
    return get_fields(user)


# ── マスタ ───────────────────────────────────────────────────────────────
@router.get("/masters/summary")
def masters_summary(user: dict = Depends(viewer)):
    with session_scope() as s:
        return {"products": s.query(ProductMaster).count(), "customers": s.query(CustomerMaster).count()}


@router.get("/masters/products")
def list_products(q: str = "", page: int = 1, user: dict = Depends(viewer)):
    with session_scope() as s:
        query = s.query(ProductMaster)
        if q:
            query = query.filter((ProductMaster.code.ilike(f"%{q}%")) | (ProductMaster.name.ilike(f"%{q}%")))
        total = query.count()
        rows = query.order_by(ProductMaster.id).offset((page - 1) * 100).limit(100).all()
        return {"total": total, "items": [{"id": r.id, "code": r.code, "name": r.name, "unit": r.unit,
                                           "price": r.price} for r in rows]}


@router.get("/masters/customers")
def list_customers(q: str = "", page: int = 1, user: dict = Depends(viewer)):
    with session_scope() as s:
        query = s.query(CustomerMaster)
        if q:
            like = f"%{q}%"
            query = query.filter(CustomerMaster.company.ilike(like) | CustomerMaster.code.ilike(like)
                                 | CustomerMaster.department.ilike(like) | CustomerMaster.person.ilike(like))
        total = query.count()
        rows = query.order_by(CustomerMaster.id).offset((page - 1) * 100).limit(100).all()
        return {"total": total, "items": [{"id": r.id, "code": r.code, "company": r.company,
                                           "department": r.department, "person": r.person,
                                           "address": f"{r.prefecture}{r.city}{r.address}", "zip": r.zip,
                                           "tel": r.tel, "hidden": r.hidden} for r in rows]}


@router.post("/masters/{kind}/import")
async def import_master(kind: str, file: UploadFile = File(...), replace: bool = Form(True),
                        user: dict = Depends(admin)):
    data = await file.read()
    try:
        if kind == "products":
            res = masters.import_products(data, file.filename or "", replace)
        elif kind == "customers":
            res = masters.import_customers(data, file.filename or "", replace)
        else:
            raise HTTPException(404)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    log_event(f"マスタ取込({kind}): {res['imported']}件", "master", username=user["username"])
    return res


@router.delete("/masters/{kind}")
def clear_master(kind: str, user: dict = Depends(admin)):
    model = {"products": ProductMaster, "customers": CustomerMaster}.get(kind)
    if model is None:
        raise HTTPException(404)
    with session_scope() as s:
        s.query(model).delete()
    masters.invalidate()
    return {"ok": True}


# ── 設定 ────────────────────────────────────────────────────────────────
@router.get("/settings")
def read_settings(user: dict = Depends(viewer)):
    st = get_settings()
    if user["role"] != "admin":
        st["vlm"]["openai"]["api_key"] = "********" if st["vlm"]["openai"].get("api_key") else ""
    return {"settings": st, "defaults": DEFAULTS}


@router.put("/settings")
def write_settings(body: dict = Body(...), user: dict = Depends(admin)):
    before = get_settings()
    st = update_settings(body)
    masters.invalidate()
    if "vlm" in body:
        llama_manager().configure(st["vlm"]["llama"])
        if st["vlm"]["llama"] != before["vlm"]["llama"] and llama_manager().status()["state"] in ("ready", "starting"):
            llama_manager().restart()
    if "watch" in body and watch_service.status()["running"]:
        watch_service.start()
    log_event("設定を変更: " + ", ".join(body.keys()), "settings", username=user["username"])
    return {"settings": st}


# ── VLM ─────────────────────────────────────────────────────────────────
@router.get("/vlm/status")
def vlm_status(user: dict = Depends(viewer)):
    st = get_settings()["vlm"]
    mgr = llama_manager()
    mgr.configure(st["llama"])
    return {"backend": st["backend"], "llama": mgr.status(), "setup": setup_runner().status(),
            "presets": {k: {"label": v["label"], "size_gb": v["size_gb"]} for k, v in PRESETS.items()},
            "accelerator": detect_accelerator()}


@router.post("/vlm/{action}")
def vlm_action(action: str, body: dict = Body(default={}), user: dict = Depends(admin)):
    mgr = llama_manager()
    mgr.configure(get_settings()["vlm"]["llama"])
    if action == "start":
        return mgr.start()
    if action == "stop":
        return mgr.stop()
    if action == "restart":
        return mgr.restart()
    if action == "test":
        try:
            return make_client(get_settings()["vlm"]).ping()
        except VlmError as exc:
            raise HTTPException(503, str(exc)) from exc
    if action == "setup":
        b = boot()
        preset = str(body.get("preset") or "standard")
        if preset not in PRESETS:
            raise HTTPException(400, "不明なプリセットです")

        def _done(model, mmproj):
            if model is not None:
                update_settings({"vlm": {"llama": {"model": model.name, "mmproj": mmproj.name}}})
        return setup_runner().start(bin_dir=b.bin_dir, models_dir=b.models_dir, preset=preset,
                                    variant=str(body.get("variant") or "auto"),
                                    skip_binary=bool(body.get("skip_binary")), skip_model=bool(body.get("skip_model")),
                                    on_done=_done)
    raise HTTPException(404)


# ── 監視 ────────────────────────────────────────────────────────────────
@router.get("/watch")
def watch_status(user: dict = Depends(viewer)):
    return {**watch_service.status(), "settings": get_settings()["watch"]}


@router.post("/watch/{action}")
def watch_action(action: str, user: dict = Depends(editor)):
    if action == "start":
        update_settings({"watch": {"enabled": True}})
        return watch_service.start()
    if action == "stop":
        update_settings({"watch": {"enabled": False}})
        return watch_service.stop()
    raise HTTPException(404)


# ── 外部DB連携 ───────────────────────────────────────────────────────────
def _target_dict(t: DbTarget) -> dict:
    return {"id": t.id, "name": t.name, "url": masked_url(t.url), "header_table": t.header_table,
            "line_table": t.line_table, "mapping": t.mapping or default_mapping(), "create_tables": t.create_tables,
            "trigger": t.trigger, "enabled": t.enabled, "last_error": t.last_error,
            "last_push_at": t.last_push_at.isoformat() if t.last_push_at else None}


@router.get("/integrations")
def list_targets(user: dict = Depends(admin)):
    with session_scope() as s:
        return {"targets": [_target_dict(t) for t in s.query(DbTarget).order_by(DbTarget.id)],
                "default_mapping": default_mapping()}


def _target_body(t: DbTarget, body: dict) -> None:
    for k in ("name", "header_table", "line_table"):
        if k in body:
            setattr(t, k, str(body[k] or "").strip())
    if body.get("url") and "***" not in str(body["url"]):
        t.url = str(body["url"]).strip()
    if "mapping" in body and isinstance(body["mapping"], dict):
        t.mapping = body["mapping"]
    for k in ("create_tables", "enabled"):
        if k in body:
            setattr(t, k, bool(body[k]))
    if body.get("trigger") in ("manual", "confirm", "extract"):
        t.trigger = body["trigger"]


@router.post("/integrations")
def add_target(body: dict = Body(...), user: dict = Depends(admin)):
    if not body.get("url"):
        raise HTTPException(400, "接続URLを入力してください")
    with session_scope() as s:
        t = DbTarget(name=str(body.get("name") or "外部DB"), url=str(body["url"]))
        _target_body(t, body)
        s.add(t)
        s.flush()
        return _target_dict(t)


@router.put("/integrations/{tid}")
def edit_target(tid: int, body: dict = Body(...), user: dict = Depends(admin)):
    with session_scope() as s:
        t = s.get(DbTarget, tid)
        if t is None:
            raise HTTPException(404)
        _target_body(t, body)
        return _target_dict(t)


@router.delete("/integrations/{tid}")
def delete_target(tid: int, user: dict = Depends(admin)):
    with session_scope() as s:
        t = s.get(DbTarget, tid)
        if t is not None:
            s.delete(t)
    return {"ok": True}


@router.post("/integrations/test")
def test_target(body: dict = Body(...), user: dict = Depends(admin)):
    url = str(body.get("url") or "")
    if (not url or "***" in url) and body.get("id"):
        with session_scope() as s:
            t = s.get(DbTarget, int(body["id"]))
            url = t.url if t else ""
    if not url:
        raise HTTPException(400, "接続URLを入力してください")
    try:
        return test_connection(url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:500]}


@router.post("/integrations/{tid}/push")
def push_target(tid: int, body: dict = Body(...), user: dict = Depends(editor)):
    ids = [int(x) for x in body.get("ids") or []]
    if not ids:
        raise HTTPException(400, "書類を選択してください")
    try:
        return push_documents(tid, ids)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"書き出しに失敗しました: {str(exc)[:400]}") from exc


# ── 学習 ────────────────────────────────────────────────────────────────
@router.get("/learning")
def learning_info(user: dict = Depends(viewer)):
    with session_scope() as s:
        tnames = {t.id: t.name for t in s.query(Template)}
        rules = [{"id": r.id, "template": tnames.get(r.template_id, "全体") if r.template_id else "全体",
                  "field": r.field, "original": r.original_raw, "corrected": r.corrected, "count": r.count,
                  "enabled": r.enabled, "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                 for r in s.query(Correction).order_by(Correction.count.desc(), Correction.id.desc()).limit(500)]
        return {"stats": learning.learning_stats(s), "rules": rules}


@router.put("/learning/{rid}")
def edit_rule(rid: int, body: dict = Body(...), user: dict = Depends(editor)):
    with session_scope() as s:
        r = s.get(Correction, rid)
        if r is None:
            raise HTTPException(404)
        if "enabled" in body:
            r.enabled = bool(body["enabled"])
        if "corrected" in body:
            r.corrected = str(body["corrected"])
    return {"ok": True}


@router.delete("/learning/{rid}")
def delete_rule(rid: int, user: dict = Depends(editor)):
    with session_scope() as s:
        r = s.get(Correction, rid)
        if r is not None:
            s.delete(r)
    return {"ok": True}


# ── ログ・状態 ───────────────────────────────────────────────────────────
@router.get("/events")
def events(limit: int = 200, level: str = "", user: dict = Depends(viewer)):
    with session_scope() as s:
        q = s.query(Event)
        if level:
            q = q.filter(Event.level == level)
        return [{"id": e.id, "ts": e.ts.isoformat(), "level": e.level, "kind": e.kind, "document_id": e.document_id,
                 "username": e.username, "message": e.message} for e in q.order_by(Event.id.desc()).limit(min(limit, 1000))]


@router.get("/dashboard")
def dashboard(user: dict = Depends(viewer)):
    from sqlalchemy import func
    with session_scope() as s:
        counts = dict(s.query(Document.status, func.count(Document.id)).group_by(Document.status).all())
        recent = s.query(Document).order_by(Document.id.desc()).limit(8).all()
        recent_list = [{"id": d.id, "filename": d.filename, "status": d.status,
                        "buyer_company": (d.header or {}).get("buyer_company", ""),
                        "created_at": d.created_at.isoformat()} for d in recent]
    return {"counts": counts, "recent": recent_list, "jobs": worker.status(), "watch": watch_service.status(),
            "vlm": {"backend": get_settings()["vlm"]["backend"], "llama": {
                k: v for k, v in llama_manager().status().items() if k in ("state", "error", "model")}}}
