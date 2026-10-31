"""图像 IO / hash / 裁剪 / 硬链接归档。

注意点：
- 中文路径 + macOS：用 ``np.fromfile``/``imdecode`` 绕过 cv2 对非 ASCII 路径的处理差异。
- 归档用 ``os.link``（硬链接），跨盘/不支持时降级 copy；绝不用 symlink（§11.3）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import cv2
import numpy as np

from .config import HEIC_EXTS, IMAGE_EXTS


class ImageError(RuntimeError):
    pass


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def imread(path: Path) -> np.ndarray:
    """读图为 BGR ndarray；失败抛 ImageError（单张失败不让整批挂掉）。"""
    p = Path(path)
    if p.suffix.lower() in HEIC_EXTS:
        raise ImageError("HEIC 需要先转成 JPG（iPhone 相册导出时选「兼容性最佳」）")
    try:
        buf = np.fromfile(str(p), dtype=np.uint8)
    except OSError as e:
        raise ImageError(f"读文件失败: {e}") from e
    if buf.size == 0:
        raise ImageError("空文件")
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ImageError("不是可解码的图片（可能已损坏）")
    return img


def imwrite(path: Path, img: np.ndarray, quality: int = 92) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ImageError(f"写图失败: {path}")
    buf.tofile(str(path))
    return path


def iter_images(root: Path) -> list[Path]:
    root = Path(root)
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in {"_review"})
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() in IMAGE_EXTS:
                out.append(Path(dirpath) / name)
    return out


def list_batches(photos_root: Path) -> list[Path]:
    root = Path(photos_root)
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")])


def crop_box(img: np.ndarray, bbox, pad: float = 0.28) -> np.ndarray:
    """按 bbox 裁一张带点上下文的正方形脸图（给 UI 看，不用于识别）。"""
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    side = max(x2 - x1, y2 - y1) * (1.0 + pad * 2)
    side = max(side, 24.0)
    x1 = int(round(cx - side / 2))
    y1 = int(round(cy - side / 2))
    x2 = int(round(cx + side / 2))
    y2 = int(round(cy + side / 2))
    x1c, y1c = max(0, x1), max(0, y1)
    x2c, y2c = min(w, x2), min(h, y2)
    if x2c <= x1c or y2c <= y1c:
        raise ImageError("脸框越界")
    patch = img[y1c:y2c, x1c:x2c]
    # 越界部分用边缘像素补齐，保证输出是正方形
    top, left = y1c - y1, x1c - x1
    bottom, right = y2 - y2c, x2 - x2c
    if any((top, left, bottom, right)):
        patch = cv2.copyMakeBorder(patch, top, bottom, left, right, cv2.BORDER_REPLICATE)
    return patch


def resize_max(img: np.ndarray, max_side: int = 1400) -> np.ndarray:
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return img
    s = max_side / m
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


def jpeg_bytes(img: np.ndarray, quality: int = 88) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ImageError("编码 JPEG 失败")
    return buf.tobytes()


def link_or_copy(src: Path, dst: Path) -> str:
    """硬链接优先，失败降级为复制。返回 'link' / 'copy'。"""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            if os.path.samefile(src, dst):
                return "link"
        except OSError:
            pass
        dst.unlink()
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def safe_photo_name(name: str, max_len: int = 120) -> str:
    name = "".join("_" if c in '\\/:*?"<>|\x00' else c for c in name).strip(". ")
    if not name:
        name = "photo"
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 5:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name
