"""書類ファイルの保存場所(data/files/<uid>/)。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

from PIL import Image

from .config import boot
from .pipeline.loader import Page, Word


def new_uid() -> str:
    return uuid.uuid4().hex


def doc_dir(uid: str) -> Path:
    d = boot().files_dir / uid
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", Path(name).name).strip() or "document"
    return base[:180]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_original(uid: str, filename: str, data: bytes) -> str:
    name = "original" + Path(safe_name(filename)).suffix.lower()
    (doc_dir(uid) / name).write_bytes(data)
    return name


def original_path(uid: str, stored_name: str) -> Path:
    return doc_dir(uid) / stored_name


def page_image_path(uid: str, index: int) -> Path:
    return doc_dir(uid) / f"page_{index:03d}.jpg"


def save_pages(uid: str, pages: list[Page]) -> list[dict]:
    """閲覧・ROI 編集用にページ画像と単語座標を保存する。"""
    d = doc_dir(uid)
    for old in d.glob("page_*.jpg"):
        old.unlink()
    meta = []
    words = {}
    for p in pages:
        if p.image is not None:
            p.image.save(page_image_path(uid, p.index), "JPEG", quality=88, optimize=True)
            meta.append({"index": p.index, "width": p.image.width, "height": p.image.height, "kind": "image",
                         "text_layer": p.has_text_layer, "deskew": round(p.deskew_angle, 2)})
        else:
            meta.append({"index": p.index, "width": 0, "height": 0, "kind": "text", "text_layer": False})
        if p.words or p.text:
            words[str(p.index)] = {"text": p.text, "source": p.source,
                                   "words": [[round(w.x0, 5), round(w.y0, 5), round(w.x1, 5), round(w.y1, 5), w.text]
                                             for w in p.words]}
    (d / "pages.json").write_text(json.dumps({"pages": meta, "text": words}, ensure_ascii=False), encoding="utf-8")
    return meta


def load_saved_pages(uid: str) -> list[Page]:
    """保存済みのページ画像から Page を復元する(ROI の試し読み用。再描画しない)。"""
    d = doc_dir(uid)
    info_path = d / "pages.json"
    if not info_path.exists():
        return []
    info = json.loads(info_path.read_text(encoding="utf-8"))
    texts = info.get("text", {})
    pages = []
    for m in info.get("pages", []):
        idx = int(m["index"])
        t = texts.get(str(idx), {})
        img = None
        ip = page_image_path(uid, idx)
        if m.get("kind") == "image" and ip.exists():
            with Image.open(ip) as im:
                img = im.convert("RGB")
        pages.append(Page(index=idx, image=img, text=t.get("text", ""),
                          words=[Word(*w) for w in t.get("words", [])], source=t.get("source", "pdf")))
    return pages


def delete_doc_files(uid: str) -> None:
    d = boot().files_dir / uid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
