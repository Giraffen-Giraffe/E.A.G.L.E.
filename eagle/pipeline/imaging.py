"""画像処理(傾き補正・内容領域の検出・切り出し・VLM 向け縮小)。"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def pil_to_gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"))


def resize_long_side(img: Image.Image, max_long: int) -> Image.Image:
    if max_long and max(img.size) > max_long:
        r = max_long / max(img.size)
        return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)
    return img


def estimate_skew(img: Image.Image, max_angle: float = 5.0) -> float:
    """行の投影プロファイルが最も鋭くなる角度を探す(度)。文書画像向け。"""
    gray = pil_to_gray(img)
    h, w = gray.shape
    scale = 1000.0 / max(h, w)
    if scale < 1.0:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    _, binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if binv.mean() < 0.5:     # ほぼ白紙
        return 0.0
    hh, ww = binv.shape
    center = (ww / 2, hh / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rot = cv2.warpAffine(binv, m, (ww, hh), flags=cv2.INTER_NEAREST, borderValue=0)
        prof = rot.sum(axis=1, dtype=np.float64)
        return float(np.var(prof))

    coarse = np.arange(-max_angle, max_angle + 0.001, 0.5)
    best = max(coarse, key=score)
    fine = np.arange(best - 0.5, best + 0.501, 0.1)
    return float(max(fine, key=score))


def deskew(img: Image.Image, min_angle: float = 0.3, max_angle: float = 5.0) -> tuple[Image.Image, float]:
    angle = estimate_skew(img, max_angle)
    if abs(angle) < min_angle or abs(angle) > max_angle:
        return img, 0.0
    arr = np.asarray(img.convert("RGB"))
    h, w = arr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rot = cv2.warpAffine(arr, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                         borderValue=(255, 255, 255))
    return Image.fromarray(rot), angle


def enhance_contrast(img: Image.Image) -> Image.Image:
    """薄いFAX文字を持ち上げる軽いコントラスト強調(幾何は変えない)。"""
    gray = pil_to_gray(img)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    out = clahe.apply(gray)
    return Image.fromarray(out).convert("RGB")


def content_bbox(img: Image.Image, proj_thresh: float = 0.02, min_run: int = 8,
                 pad_ratio: tuple[float, float] = (0.010, 0.006)) -> tuple[float, float, float, float] | None:
    """紙面上の印字がある範囲(ページ相対 0..1)。スキャン位置のズレを吸収する ROI 基準に使う。"""
    gray = pil_to_gray(img)
    h, w = gray.shape
    scale = 1600.0 / max(h, w)
    if scale < 1.0:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    _, binv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, kernel, iterations=1)
    hh, ww = binv.shape
    col = binv.sum(axis=0) / 255.0
    row = binv.sum(axis=1) / 255.0
    if col.max() <= 0 or row.max() <= 0:
        return None

    def ends(values: np.ndarray, thr: float) -> tuple[int, int]:
        n = len(values)
        left, run = 0, 0
        for i in range(n):
            run = run + 1 if values[i] > thr else 0
            if run >= min_run:
                left = i - min_run + 1
                break
        right, run = n - 1, 0
        for i in range(n - 1, -1, -1):
            run = run + 1 if values[i] > thr else 0
            if run >= min_run:
                right = i + min_run - 1
                break
        return max(0, left), min(n - 1, right)

    x0, x1 = ends(col, col.max() * proj_thresh)
    y0, y1 = ends(row, row.max() * proj_thresh)
    px = int((x1 - x0 + 1) * pad_ratio[0])
    py = int((y1 - y0 + 1) * pad_ratio[1])
    x = max(0, x0 - px)
    y = max(0, y0 - py)
    bw = min(ww - x, (x1 - x0 + 1) + 2 * px)
    bh = min(hh - y, (y1 - y0 + 1) + 2 * py)
    if bw * bh < 0.05 * ww * hh:
        return None
    return (x / ww, y / hh, bw / ww, bh / hh)


def map_box(box: tuple[float, float, float, float], ref: tuple[float, float, float, float] | None,
            cur: tuple[float, float, float, float] | None) -> tuple[float, float, float, float]:
    """基準ページの内容領域 ref で登録した箱を、現在ページの内容領域 cur へ写す(どちらもページ相対)。"""
    if not ref or not cur or ref[2] <= 0 or ref[3] <= 0:
        return box
    x, y, w, h = box
    sx, sy = cur[2] / ref[2], cur[3] / ref[3]
    return (cur[0] + (x - ref[0]) * sx, cur[1] + (y - ref[1]) * sy, w * sx, h * sy)


def crop_rel(img: Image.Image, box: tuple[float, float, float, float], pad: float = 0.006) -> Image.Image | None:
    W, H = img.size
    x, y, w, h = box
    x0 = max(0, int((x - pad) * W))
    y0 = max(0, int((y - pad) * H))
    x1 = min(W, int((x + w + pad) * W))
    y1 = min(H, int((y + h + pad) * H))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return img.crop((x0, y0, x1, y1))


def upscale_small(img: Image.Image, min_long: int = 640, max_factor: float = 3.0) -> Image.Image:
    """小さな切り出しを拡大して VLM が読みやすいようにする。"""
    long_side = max(img.size)
    if long_side >= min_long:
        return img
    f = min(max_factor, min_long / max(1, long_side))
    return img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
