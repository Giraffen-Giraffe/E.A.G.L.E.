"""実行時設定(画面から変更できる設定)。DB の settings テーブルに JSON で保存する。"""
from __future__ import annotations

import copy
import os
import threading

from .config import DEFAULT_LLAMA_PORT
from .db import Setting, session_scope

DEFAULTS: dict = {
    "vlm": {
        # llama_cpp = 同梱 llama.cpp(llama-server) / openai = OpenAI互換サーバー(LAN 内の vLLM 等) / ollama
        "backend": "llama_cpp",
        "llama": {
            "model": "",          # models/ 内の GGUF ファイル名。空なら自動選択
            "mmproj": "",         # 画像エンコーダ(mmproj-*.gguf)。空なら自動選択
            "port": DEFAULT_LLAMA_PORT,
            "ctx": 16384,
            "gpu_layers": 999,
            "threads": 0,
            "extra_args": "",
            "autostart": True,
        },
        "openai": {"base_url": "http://127.0.0.1:8000/v1", "api_key": "", "model": ""},
        "ollama": {"base_url": "http://127.0.0.1:11434", "model": "qwen3-vl:8b", "keep_alive": "30m"},
        "json_mode": "schema",    # schema(推奨) / object / none
        "max_tokens": 4096,
        "retry_empty": 2,         # 空返し時の再試行回数(温度を上げて再試行)
        "timeout_sec": 900,
    },
    "render": {
        "dpi": 300,               # PDF の描画解像度
        "max_long_side": 2240,    # VLM に送る画像の長辺上限(px)。大きいほど精細・遅い
        "enhance": "off",         # off / contrast(薄いFAX向け)
        "deskew": "auto",         # auto(テキスト層の無いページのみ) / off
    },
    "extraction": {
        "own_company_names": [],  # 自社名(受注側)。発注元候補から除外する
        "skip_title_words": ["現品票", "納品書", "請求書", "領収書"],
        "order_title_words": ["注文", "発注", "オーダー"],
        "header_pages": 2,        # 発注元等の読取に使う最大ページ数
        "shipping_keywords": ["送料", "運賃", "配送料", "荷造運賃", "ソウリョウ", "ｿｳﾘｮｳ"],
        "shipping_code": "送料",
        "summary_keywords": ["消費税", "合計", "小計", "総合計", "内税", "外税", "税額", "お買上", "ご請求", "総額"],
        "internal_code_prefixes": [],   # 型番の前に付く管理用の接頭辞(取り除いて照合)
        "code_abbreviations": {},       # 型番の略記 → 正式表記(例 {"AB": "ABCD"})
        "include_master_in_prompt": True,
        "max_master_items_in_prompt": 300,
        "use_text_layer": True,         # 電子PDFの文字情報で注文番号等を検証する
        "orderer_fallback": True,       # 発注元が取れない時に発注元だけを狙って再読取
        "skip_duplicates": False,       # 同一ファイル(SHA-256一致)の再取込をスキップ
    },
    "matching": {
        "product_fuzzy_ratio": 0.86,
        "customer_min_score": 3.9,
        "buyer_min_score": 3.0,
        "hist_margin": 2.0,
    },
    "watch": {
        "enabled": False,
        "folders": [],            # [{"path": "...", "recursive": false, "polling": false}]
        "extensions": [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".eml", ".txt", ".xlsx", ".csv"],
        "after": "move",          # move(_eagle_done へ移動) / keep / delete
    },
    "export": {
        "csv_encoding": "utf-8-sig",   # utf-8-sig / cp932
        "date_format": "%Y-%m-%d",
    },
    "learning": {
        "enabled": True,
        "min_count_for_default": 2,    # 空欄→同じ値 の修正がこの回数たまったら既定値として学習
    },
}

_cache: dict | None = None
_lock = threading.Lock()


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _env_defaults() -> dict:
    """環境変数による初期値(画面で保存した値が優先)。サーバーの一括設定用。
    EAGLE_VLM_BACKEND=llama_cpp|openai|ollama / EAGLE_VLM_URL / EAGLE_VLM_MODEL / EAGLE_VLM_API_KEY"""
    backend = os.environ.get("EAGLE_VLM_BACKEND", "").strip()
    url = os.environ.get("EAGLE_VLM_URL", "").strip()
    model = os.environ.get("EAGLE_VLM_MODEL", "").strip()
    key = os.environ.get("EAGLE_VLM_API_KEY", "").strip()
    vlm: dict = {}
    if backend:
        vlm["backend"] = backend
    target = backend if backend in ("openai", "ollama") else "openai"
    sub = {k: v for k, v in (("base_url", url), ("model", model), ("api_key", key if target == "openai" else "")) if v}
    if sub:
        vlm[target] = sub
    return {"vlm": vlm} if vlm else {}


def get_settings() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return copy.deepcopy(_cache)
    stored: dict = {}
    with session_scope() as s:
        for row in s.query(Setting).all():
            stored[row.key] = row.value
    merged = deep_merge(deep_merge(DEFAULTS, _env_defaults()), stored)
    with _lock:
        _cache = merged
    return copy.deepcopy(merged)


def update_settings(patch: dict) -> dict:
    """セクション単位で部分更新する。"""
    global _cache
    current = get_settings()
    with session_scope() as s:
        for section, value in (patch or {}).items():
            if section not in DEFAULTS or not isinstance(value, dict):
                continue
            merged = deep_merge(current.get(section, {}), value)
            row = s.get(Setting, section)
            if row is None:
                s.add(Setting(key=section, value=merged))
            else:
                row.value = merged
    with _lock:
        _cache = None
    return get_settings()


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None
