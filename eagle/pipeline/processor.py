"""1書類の処理(読込 → VLM 抽出 → ROI 焦点読取 → 後処理 → 照合 → 学習補正 → 保存)。"""
from __future__ import annotations

import copy
import logging
import time
from typing import Callable

from .. import learning, masters
from ..db import Document, LineItem, Template, session_scope, utcnow, log_event
from ..fields import HEADER_KEYS, PARTY_KEYS, custom_fields
from ..settings import get_settings
from ..storage import original_path, save_pages
from ..vlm import VlmClient, VlmUnavailable, image_to_b64, make_client
from . import prompts
from .customers import DocEvidence, company_core, resolve
from .doc_type import detect_non_order, is_non_order_title
from .imaging import content_bbox, deskew, enhance_contrast, resize_long_side
from .loader import Page, load_document
from .normalize import (apply_pattern, clean_str, compact_ordernum, is_junk_ordernum, normalize_date,
                        normalize_tel, parse_num, squash, verify_ordernum_with_text)
from .postprocess import CleanContext, clean_line_items
from .roi import RoiResult, run_rois
from .templates import match_template

LOG = logging.getLogger("eagle.processor")

ProgressFn = Callable[[float, str], None]

SCALAR_HEADER = ["document_title", "order_number", "order_date", "delivery_date", "buyer_contact", "end_user",
                 "customer_order_number", "subtotal", "tax", "total", "payment_terms", "notes"]


class Skipped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def template_dict(t: Template) -> dict:
    return {"id": t.id, "name": t.name, "enabled": t.enabled, "priority": t.priority,
            "match_keywords": t.match_keywords or [], "anchor": t.anchor, "reference": t.reference or {},
            "rois": t.rois or [], "prompt_hint": t.prompt_hint or "", "defaults": t.defaults or {},
            "overrides": t.overrides or {}, "patterns": t.patterns or {}, "sample_document_id": t.sample_document_id}


class OwnCompany:
    def __init__(self, names: list[str]):
        self.keys = [squash(n) for n in names if str(n).strip()]
        self.cores = [company_core(n) for n in names if company_core(n)]

    def __call__(self, name: str) -> bool:
        n = squash(name)
        if not n:
            return False
        return any(k in n or (len(n) >= 4 and n in k) for k in self.keys) or \
            any(c and c in company_core(name) for c in self.cores)


def _merge_header(acc: dict, new: dict) -> None:
    if not isinstance(new, dict):
        return
    for k in SCALAR_HEADER:
        v = clean_str(new.get(k))
        if v and not acc.get(k):
            acc[k] = v
    st = new.get("ship_to") if isinstance(new.get("ship_to"), dict) else {}
    if st and not any((acc.get("ship_to") or {}).values()) and any(clean_str(st.get(k)) for k in PARTY_KEYS):
        acc["ship_to"] = {k: clean_str(st.get(k)) for k in PARTY_KEYS}
    seen = {squash(c["name"]) for c in acc.setdefault("companies", [])}
    for co in new.get("companies") or []:
        if isinstance(co, str):
            co = {"name": co}
        if not isinstance(co, dict):
            continue
        nm = clean_str(co.get("name")).strip(":,、。")
        if len(squash(nm)) < 2 or squash(nm) in seen:
            continue
        seen.add(squash(nm))
        acc["companies"].append({"name": nm, **{k: clean_str(co.get(k)) for k in PARTY_KEYS if k != "company"},
                                 "role": str(co.get("role") or "other")})
    cust = new.get("custom") if isinstance(new.get("custom"), dict) else {}
    accc = acc.setdefault("custom", {})
    for k, v in cust.items():
        if clean_str(v) and not accc.get(k):
            accc[k] = clean_str(v)


def _page_images(page: Page, max_long: int) -> list[str]:
    return [image_to_b64(resize_long_side(page.image, max_long))] if page.image is not None else []


def _prompt_for(page: Page, prompt: str) -> str:
    return prompts.with_text(prompt, page.text) if page.text_mode else prompt


def _party_text(d: dict) -> str:
    return " ".join(str(d.get(k) or "") for k in ("company", "name", "department", "contact", "address", "tel")
                    if str(d.get(k) or "").strip()).strip()


def process_document(doc_id: int, options: dict | None = None, progress: ProgressFn | None = None) -> dict:
    options = options or {}
    progress = progress or (lambda p, m: None)
    t0 = time.time()
    st = get_settings()
    ex_cfg, rcfg, mcfg = st["extraction"], st["render"], st["matching"]
    timings: dict[str, float] = {}

    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise ValueError(f"document {doc_id} not found")
        uid, stored, filename = doc.uid, doc.stored_name, doc.filename
        forced_tpl = options.get("template_id") or (doc.template_id if doc.template_locked else None)
        doc.status, doc.status_detail = "processing", ""
        templates = [template_dict(t) for t in s.query(Template).filter(Template.enabled.is_(True)).all()]
        all_templates = {t.id: template_dict(t) for t in s.query(Template).all()}
        hist = learning.customer_history(s)

    # 1) 読込
    progress(0.02, "書類を読み込み中")
    kind, pages = load_document(original_path(uid, stored), int(rcfg.get("dpi") or 300),
                                int(options.get("max_pages") or 30))
    if not pages:
        raise ValueError("ページがありません")
    for p in pages:
        if p.image is None or p.has_text_layer:
            continue
        if str(rcfg.get("deskew") or "auto") != "off":
            p.image, p.deskew_angle = deskew(p.image)
        if str(rcfg.get("enhance") or "off") == "contrast":
            p.image = enhance_contrast(p.image)
    page_meta = save_pages(uid, pages)
    cbbox = {str(p.index): list(content_bbox(p.image) or ()) for p in pages if p.image is not None}
    timings["load"] = time.time() - t0
    doc_text = "\n".join(p.text for p in pages if p.has_text_layer) if ex_cfg.get("use_text_layer", True) else ""
    raw: dict = {"file_type": kind, "pages": page_meta, "content_bbox": cbbox}

    skip_words, order_words = ex_cfg.get("skip_title_words") or [], ex_cfg.get("order_title_words") or []
    if not options.get("force"):
        skip, why = detect_non_order(filename, pages, skip_words, order_words)
        if skip:
            return _finish_skipped(doc_id, why, raw, len(pages), t0)

    own = OwnCompany(ex_cfg.get("own_company_names") or [])
    custom_h, custom_l = custom_fields("header"), custom_fields("line")
    max_long = int(rcfg.get("max_long_side") or 2240)

    # 2) テンプレート(指定 / 本文・ファイル名で事前判定)
    tpl: dict | None = None
    match_info = ""
    if forced_tpl and int(forced_tpl) in all_templates:
        tpl, match_info = all_templates[int(forced_tpl)], "手動指定"
    else:
        m = match_template(templates, [], doc_text, filename, "", ex_cfg.get("own_company_names"))
        if m:
            tpl, match_info = m.template, f"{m.where}『{m.keyword}』"
    hint = tpl["prompt_hint"] if tpl else ""

    client: VlmClient = make_client(st["vlm"])
    model_name = client.model_name()

    # 3) 書類全体(表題・会社・注文番号・日付)
    header_raw: dict = {"companies": [], "custom": {}}
    header_pages = max(1, int(ex_cfg.get("header_pages") or 1))
    for n, page in enumerate(pages[:header_pages], start=1):
        progress(0.05 + 0.25 * (n - 1) / header_pages, f"書類全体を読取中({page.index}ページ目)")
        res = client.chat_json(prompts.SYSTEM,
                               _prompt_for(page, prompts.header_prompt(ex_cfg.get("own_company_names") or [], custom_h,
                                                                       hint, page.text_mode, page.index)),
                               _page_images(page, max_long), prompts.header_schema(custom_h),
                               want=lambda d: bool(d.get("companies")))
        raw.setdefault("header_calls", []).append(res)
        _merge_header(header_raw, res)
        if n == 1 and not options.get("force") and is_non_order_title(header_raw.get("document_title", ""),
                                                                     skip_words, order_words):
            raw["header"] = header_raw
            return _finish_skipped(doc_id, f"表題『{header_raw.get('document_title')}』", raw, len(pages), t0,
                                   model_name)
        if any(c.get("role") == "orderer" and not own(c["name"]) for c in header_raw["companies"]):
            break
    timings["header"] = time.time() - t0 - sum(timings.values())

    company_names = [c["name"] for c in header_raw["companies"]]
    if tpl is None:
        m = match_template(templates, company_names, doc_text, filename, header_raw.get("document_title", ""),
                           ex_cfg.get("own_company_names"))
        if m:
            tpl, match_info = m.template, f"{m.where}『{m.keyword}』"
    raw["template_match"] = match_info

    # 発注元が1社も取れない(受注側=自社しか読めない)時は、発注元だけを狙って読み直す
    if ex_cfg.get("orderer_fallback", True) and not any(not own(n) for n in company_names):
        page = pages[0]
        od = client.chat_json(prompts.SYSTEM, _prompt_for(page, prompts.orderer_prompt(
            ex_cfg.get("own_company_names") or [], page.text_mode)), _page_images(page, max_long),
            prompts.ORDERER_SCHEMA, want=lambda d: bool(clean_str(d.get("company"))))
        raw["orderer_fallback"] = od
        nm = clean_str(od.get("company"))
        if nm and not own(nm):
            header_raw["companies"].append({"name": nm, **{k: clean_str(od.get(k)) for k in PARTY_KEYS if k != "company"},
                                            "role": "orderer"})
    raw["header"] = header_raw

    # 4) ROI 焦点読取
    roi_results: list[RoiResult] = []
    pidx = masters.product_index(ex_cfg, float(mcfg.get("product_fuzzy_ratio") or 0.86))
    master_text = ""
    if ex_cfg.get("include_master_in_prompt", True) and len(pidx) and len(pidx) <= int(
            ex_cfg.get("max_master_items_in_prompt") or 300):
        master_text = pidx.prompt_text(int(ex_cfg.get("max_master_items_in_prompt") or 300))
    if tpl and tpl.get("rois"):
        progress(0.32, f"ROI を焦点読取中({tpl['name']})")
        roi_results = run_rois(tpl, pages, client, custom_l, master_text,
                               progress=lambda f: progress(0.32 + 0.15 * f, "ROI を焦点読取中"))
        raw["roi"] = [r.as_dict() for r in roi_results]
    timings["roi"] = time.time() - t0 - sum(timings.values())

    # 5) 明細
    region_pages = {r.page for r in roi_results if r.field == "@line_items" and (r.value or {}).get("line_items")}
    items: list[dict] = []
    top_orders: list[str] = []
    for r in roi_results:
        if r.field == "@line_items" and r.page in region_pages:
            for li in r.value.get("line_items") or []:
                if isinstance(li, dict):
                    items.append({**li, "page": r.page})
            top_orders += [str(o) for o in r.value.get("order_numbers") or []]
    target_pages = [p for p in pages if p.index not in region_pages]
    for n, page in enumerate(target_pages, start=1):
        progress(0.48 + 0.4 * (n - 1) / max(1, len(target_pages)), f"明細を読取中({page.index}/{len(pages)}ページ)")
        res = client.chat_json(prompts.SYSTEM,
                               _prompt_for(page, prompts.items_prompt(master_text, custom_l, hint, page.text_mode,
                                                                      page.index)),
                               _page_images(page, max_long), prompts.items_schema(custom_l),
                               # 1ページ目で明細が空なら空返しとみなして再試行(2ページ目以降は明細無しもあり得る)
                               want=(lambda d: bool(d.get("line_items"))) if page.index == pages[0].index else None)
        raw.setdefault("item_calls", []).append({"page": page.index, **res})
        for li in res.get("line_items") or []:
            if isinstance(li, dict):
                items.append({**li, "page": page.index})
        top_orders += [str(o) for o in res.get("order_numbers") or []]
    timings["items"] = time.time() - t0 - sum(timings.values())

    # 6) 後処理
    progress(0.9, "後処理・照合中")
    with session_scope() as s:
        fixes, defaults = learning.header_rules(s, tpl["id"] if tpl else None,
                                                int(st["learning"].get("min_count_for_default") or 2)) \
            if st["learning"].get("enabled", True) else ({}, {})
        code_fixes = learning.code_rules(s) if st["learning"].get("enabled", True) else {}
    ctx = CleanContext(
        summary_keywords=ex_cfg.get("summary_keywords") or [], shipping_keywords=ex_cfg.get("shipping_keywords") or [],
        shipping_code=str(ex_cfg.get("shipping_code") or "送料"),
        internal_prefixes=ex_cfg.get("internal_code_prefixes") or [], products=pidx, doc_text=doc_text,
        learned_codes=code_fixes, custom_keys=[f.key for f in custom_l],
    )
    lines, warnings = clean_line_items(items, ctx)

    header, ship_cands = _build_header(header_raw, roi_results, own, warnings)
    for f in custom_h:
        header[f.key] = clean_str((header_raw.get("custom") or {}).get(f.key))
    for r in roi_results:
        if r.field in header and not r.field.startswith("@") and r.value:
            if r.merge == "override" or not header.get(r.field):
                header[r.field] = r.value

    _fix_order_numbers(header, lines, top_orders, doc_text, tpl, items, warnings)
    _normalize_header_values(header, lines, custom_h, custom_l)
    _check_totals(header, lines, warnings, ctx.shipping_code)

    # 7) 顧客照合
    buyer_label = ship_label = ""
    header.setdefault("buyer_code", "")
    header.setdefault("ship_to_code", "")
    cidx = masters.customer_index()
    if ship_cands and not header.get("ship_to_company"):
        _infer_ship_to(header, ship_cands, cidx, warnings)
    if cidx.records:
        vlm_blob = " ".join(f"{c['name']} {c.get('department', '')} {c.get('contact', '')} {c.get('address', '')} "
                            f"{c.get('tel', '')}" for c in header_raw["companies"])
        secondary = (doc_text + "\n" + vlm_blob) if doc_text else vlm_blob
        own_names = ex_cfg.get("own_company_names") or []
        buyer_text = " ".join(str(header.get(k) or "") for k in
                              ("buyer_company", "buyer_department", "buyer_contact", "buyer_address", "buyer_tel"))
        roi_b = next((r for r in roi_results if r.field == "@buyer" and isinstance(r.value, dict)), None)
        if roi_b and roi_b.value.get("_text"):
            buyer_text += " " + roi_b.value["_text"]
        rb = resolve(DocEvidence(buyer_text, secondary), cidx, dict(hist["buyer"]), role="buyer",
                     exclude_cores=own_names, min_score=float(mcfg.get("buyer_min_score") or 3.0),
                     contact=str(header.get("buyer_contact") or ""))
        buyer_core = ""
        if rb:
            header["buyer_code"], buyer_label, buyer_core = rb.code, rb.rec.label(), rb.rec.core
            raw["buyer_match"] = {"code": rb.code, "label": rb.rec.label(), "score": round(rb.score, 2),
                                  "why": rb.why, "runner_up": rb.runner_up}
        else:
            buyer_core = company_core(header.get("buyer_company", ""))
            if header.get("buyer_company"):
                warnings.append("要確認[発注元が顧客マスタで特定できません]")
        texts = [" ".join(str(header.get(k) or "") for k in
                          ("ship_to_company", "ship_to_department", "ship_to_contact", "ship_to_address", "ship_to_tel")),
                 str(header.get("end_user") or "")]
        best = None
        for t in texts:
            if not t.strip() or own(t):
                continue
            r = resolve(DocEvidence(t, secondary), cidx, dict(hist["ship_to"]), role="ship_to",
                        exclude_codes={rb.code} if rb else set(), exclude_cores=own_names, buyer_core=buyer_core,
                        min_score=float(mcfg.get("customer_min_score") or 3.9),
                        hist_margin=float(mcfg.get("hist_margin") or 2.0),
                        pref_hist=dict(hist["pair"].get(rb.code, {})) if rb else None)
            if r is not None and (best is None or r.score > best.score):
                best = r
        if best:
            header["ship_to_code"], ship_label = best.code, best.rec.label()
            raw["ship_to_match"] = {"code": best.code, "label": best.rec.label(), "score": round(best.score, 2),
                                    "why": best.why, "runner_up": best.runner_up}

    # 8) テンプレートの既定値・固定値、学習補正
    if tpl:
        for k, v in (tpl.get("defaults") or {}).items():
            if v not in (None, "") and not header.get(k):
                header[k] = v
        for k, v in (tpl.get("overrides") or {}).items():
            if v not in (None, ""):
                header[k] = v
    warnings += learning.apply_header_rules(header, fixes, defaults)

    status = "extracted"
    if not lines:
        warnings.append("要確認[明細が読み取れませんでした]")
    if not header.get("buyer_company"):
        warnings.append("要確認[発注元が読み取れませんでした]")
    if any(w.startswith("要確認") for w in warnings):
        status = "needs_review"
    timings["post"] = time.time() - t0 - sum(timings.values())
    raw["timings"] = {k: round(v, 2) for k, v in timings.items()}
    raw["vlm_calls"] = client.calls
    raw["buyer_label"], raw["ship_to_label"] = buyer_label, ship_label

    with session_scope() as s:
        doc = s.get(Document, doc_id)
        doc.status, doc.status_detail = status, ""
        doc.file_type, doc.page_count = kind, len(pages)
        doc.doc_title = str(header.get("document_title") or "")[:250]
        doc.template_id = tpl["id"] if tpl else None
        doc.header = header
        doc.extracted_header = copy.deepcopy(header)
        doc.raw_extraction = raw
        doc.warnings = warnings
        doc.buyer_code, doc.ship_to_code = str(header.get("buyer_code") or ""), str(header.get("ship_to_code") or "")
        doc.model_name = model_name
        doc.processing_sec = round(time.time() - t0, 1)
        doc.processed_at = utcnow()
        doc.confirmed_at, doc.confirmed_by = None, ""
        doc.lines.clear()
        s.flush()
        for ln in lines:
            snap = {k: ln[k] for k in ("product_code", "quantity", "unit_price", "amount", "order_number")}
            doc.lines.append(LineItem(**ln, extracted=snap))
    log_event(f"抽出完了 {filename}: 明細{len(lines)}行 / {status} / {round(time.time() - t0, 1)}秒",
              "process", document_id=doc_id)
    return {"status": status, "lines": len(lines), "warnings": warnings}


def _finish_skipped(doc_id: int, reason: str, raw: dict, page_count: int, t0: float, model: str = "") -> dict:
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        doc.status, doc.status_detail = "skipped", f"注文書以外の書類と判定: {reason}"
        doc.raw_extraction, doc.page_count = raw, page_count
        doc.processing_sec, doc.processed_at, doc.model_name = round(time.time() - t0, 1), utcnow(), model
    log_event(f"注文書以外としてスキップ: {reason}", "process", document_id=doc_id)
    return {"status": "skipped", "reason": reason}


def _infer_ship_to(header: dict, cands: list[dict], cidx, warnings: list[str]) -> None:
    """届け先の明示が読めなかった時、紙面の他の会社(住所付き)から納品先を推定する。
    VLM の役割ラベルは不安定で、届け先を other と付けることがあるため。
    顧客マスタがあれば「マスタに一致する会社」を選ぶ(マスタが誰が顧客かの正解を持つ)。
    マスタが無ければ候補が1社だけの時に限る。どちらも要確認を付ける。"""
    pick = None
    if cidx.records:
        best = None
        for c in cands:
            text = " ".join(str(c.get(k) or "") for k in ("name", "department", "contact", "address", "tel"))
            r = resolve(DocEvidence(text, text), cidx, {}, role="ship_to", min_score=3.9)
            if r is not None and (best is None or r.score > best[0]):
                best = (r.score, c)
        pick = best[1] if best else None
        why = "顧客マスタに一致する会社"
    elif len(cands) == 1:
        pick, why = cands[0], "届け先の明示なし・候補が1社"
    if pick is None:
        return
    header["ship_to_company"] = pick["name"]
    header["ship_to_department"] = pick.get("department", "")
    header["ship_to_contact"] = pick.get("contact", "")
    header["ship_to_address"] = pick.get("address", "")
    header["ship_to_tel"] = normalize_tel(pick.get("tel", ""))
    warnings.append(f"要確認[納品先を推定しました({why}): {pick['name']}]")


def _build_header(hr: dict, rois: list[RoiResult], own: OwnCompany, warnings: list[str]) -> tuple[dict, list[dict]]:
    """発注元・納品先を役割から確定し、標準項目の辞書と納品先の推定候補を返す。"""
    h: dict = {k: "" for k in HEADER_KEYS}
    for k in SCALAR_HEADER:
        h[k] = clean_str(hr.get(k))
    companies = hr.get("companies") or []
    ship = hr.get("ship_to") or {}
    ship_n = squash(ship.get("company"))
    eu_n = squash(hr.get("end_user"))

    roi_buyer = next((r.value for r in rois if r.field == "@buyer" and isinstance(r.value, dict)
                      and any(r.value.get(k) for k in PARTY_KEYS)), None)
    roi_ship = next((r.value for r in rois if r.field == "@ship_to" and isinstance(r.value, dict)
                     and any(r.value.get(k) for k in PARTY_KEYS)), None)

    # 発注元: ROI → role=orderer → 自社・届け先・最終利用者以外の先頭
    cand = next((c for c in companies if c.get("role") == "orderer" and not own(c["name"])), None)
    if cand is None:
        cand = next((c for c in companies if not own(c["name"]) and c.get("role") not in ("recipient", "ship_to", "end_user")
                     and squash(c["name"]) not in (ship_n, eu_n)), None)
    if cand is None:
        cand = next((c for c in companies if not own(c["name"]) and c.get("role") != "recipient"), None)
    buyer = {"company": cand["name"] if cand else "", **{k: (cand or {}).get(k, "") for k in PARTY_KEYS if k != "company"}}
    if roi_buyer:
        if roi_buyer.get("company") and own(roi_buyer["company"]):
            warnings.append("要確認[発注元ROIに自社名が読まれました。ROIの位置を確認してください]")
        else:
            for k in PARTY_KEYS:
                if roi_buyer.get(k):
                    buyer[k] = roi_buyer[k]
    h["buyer_company"] = buyer["company"]
    h["buyer_department"] = buyer.get("department", "")
    h["buyer_contact"] = (roi_buyer or {}).get("contact") or h.get("buyer_contact") or buyer.get("contact", "")
    h["buyer_address"] = buyer.get("address", "")
    h["buyer_tel"] = normalize_tel(buyer.get("tel", ""))
    h["buyer_fax"] = normalize_tel(buyer.get("fax", ""))

    # 納品先: ROI → 明示の届け先 → role=ship_to の会社
    if not any(ship.get(k) for k in PARTY_KEYS):
        sc = next((c for c in companies if c.get("role") == "ship_to" and not own(c["name"])), None)
        if sc:
            ship = {"company": sc["name"], **{k: sc.get(k, "") for k in PARTY_KEYS if k != "company"}}
    if roi_ship:
        ship = {k: roi_ship.get(k) or "" for k in PARTY_KEYS}
    h["ship_to_company"] = ship.get("company", "")
    h["ship_to_department"] = ship.get("department", "")
    h["ship_to_contact"] = ship.get("contact", "")
    h["ship_to_address"] = ship.get("address", "")
    h["ship_to_tel"] = normalize_tel(ship.get("tel", ""))
    if not h["end_user"]:
        eu = next((c for c in companies if c.get("role") == "end_user" and not own(c["name"])), None)
        h["end_user"] = eu["name"] if eu else ""
    if h["ship_to_company"] and own(h["ship_to_company"]):
        warnings.append("要確認[納品先が自社になっています]")
    buyer_n = squash(h["buyer_company"])
    cands = [c for c in companies if c.get("role") == "other" and not own(c["name"])
             and squash(c["name"]) not in (buyer_n, eu_n) and (c.get("address") or c.get("tel"))]
    return h, cands


def _fix_order_numbers(header: dict, lines: list[dict], top_orders: list[str], doc_text: str, tpl: dict | None,
                       items: list[dict], warnings: list[str]) -> None:
    cust_no = squash(header.get("customer_order_number"))
    tops = []
    for o in [header.get("order_number", "")] + top_orders:
        o = compact_ordernum(o)
        if o and not is_junk_ordernum(o) and squash(o) != cust_no and o not in tops:
            tops.append(o)
    per_line = [ln["order_number"] if squash(ln["order_number"]) != cust_no else "" for ln in lines]
    for ln, o in zip(lines, per_line):
        ln["order_number"] = o
    case_order = tops[0] if tops else next((o for o in per_line if o), "")
    pattern = str(((tpl or {}).get("patterns") or {}).get("order_number") or "")
    if pattern:
        pool = " ".join([doc_text] + tops + per_line + [f"{li.get('raw_code', '')} {li.get('corrected_code', '')}"
                                                       for li in items])
        fixed = apply_pattern(case_order, pattern, pool)
        if fixed != case_order:
            warnings.append(f"注文番号を書式で補正: '{case_order}' → '{fixed}'")
            case_order = fixed
    if doc_text and case_order:
        fixed = verify_ordernum_with_text(case_order, doc_text)
        if fixed != case_order:
            warnings.append(f"注文番号を本文(テキスト層)で補正: '{case_order}' → '{fixed}'")
            case_order = fixed
        for ln in lines:
            if ln["order_number"]:
                ln["order_number"] = verify_ordernum_with_text(ln["order_number"], doc_text)
    distinct = {squash(o) for o in tops}
    if len(distinct) > 1:
        warnings.append(f"注文番号の候補が複数あります: {', '.join(tops[:5])}")
    header["order_number"] = case_order
    for k, pat in ((tpl or {}).get("patterns") or {}).items():
        if k != "order_number" and k in header and pat:
            header[k] = apply_pattern(str(header.get(k) or ""), str(pat), doc_text)


def _normalize_header_values(header: dict, lines: list[dict], custom_h, custom_l) -> None:
    od = normalize_date(header.get("order_date"))
    if od:
        header["order_date"] = od
    ref = od or ""
    dd = normalize_date(header.get("delivery_date"), ref)
    if dd:
        header["delivery_date"] = dd
    for k in ("subtotal", "tax", "total"):
        v = parse_num(header.get(k))
        header[k] = v if v is not None else ""
    for ln in lines:
        iso = normalize_date(ln.get("due_date"), ref)
        if iso:
            ln["due_date"] = iso
    for f in custom_h:
        if f.kind == "date":
            v = normalize_date(header.get(f.key), ref)
            header[f.key] = v or header.get(f.key, "")
        elif f.kind == "number":
            v = parse_num(header.get(f.key))
            header[f.key] = v if v is not None else header.get(f.key, "")
    for ln in lines:
        for f in custom_l:
            val = ln["extra"].get(f.key, "")
            if f.kind == "date":
                ln["extra"][f.key] = normalize_date(val, ref) or val
            elif f.kind == "number":
                v = parse_num(val)
                ln["extra"][f.key] = v if v is not None else val


def _check_totals(header: dict, lines: list[dict], warnings: list[str], shipping_code: str) -> None:
    amounts = [ln["amount"] for ln in lines if isinstance(ln.get("amount"), (int, float))]
    if not amounts or len(amounts) != len(lines):
        return
    total = sum(amounts)
    sub = header.get("subtotal")
    tot = header.get("total")
    tax = header.get("tax")
    target = sub if isinstance(sub, (int, float)) else (tot - tax if isinstance(tot, (int, float)) and isinstance(
        tax, (int, float)) else None)
    if target is None:
        return
    # 送料を小計に含めない様式もあるため、送料を除いた合計とも比べる
    without_shipping = sum(ln["amount"] for ln in lines if ln.get("product_code") != shipping_code)
    if abs(total - target) > 1 and abs(without_shipping - target) > 1:
        warnings.append(f"要確認[明細の合計 {total:,.0f} が小計 {target:,.0f} と一致しません]")
