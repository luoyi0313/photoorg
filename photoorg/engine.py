"""检测 + 质量门控 + 特征提取。

- 检测：SCRFD（InsightFace ``det_10g``），大合照自动升分辨率并分块检测 + NMS 合并（§6）。
- 对齐/提特征：**直接用模型包自带的 ``ArcFaceONNX.get``**，保证预处理与训练一致（§6 第一个大坑）。
- 质量门控：检测分 / 瞳距 / 脸框宽 / Laplacian 方差 / 亮度 / yaw-pitch。
  不达标 → ``usable=False`` + ``reasons``，只写明细，不参与自动判定。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import Settings, DEFAULT_QUALITY


# --------------------------------------------------------------------- 数据结构


@dataclass
class FaceObs:
    face_idx: int
    bbox: list[float]          # x1, y1, x2, y2
    det_score: float
    embedding: np.ndarray      # (512,) L2 已归一化
    quality: float
    usable: bool
    yaw: float
    pitch: float
    iod: float
    face_w: float
    blur: float
    brightness: float
    reasons: list[str] = field(default_factory=list)

    @property
    def area(self) -> float:
        return max(0.0, self.bbox[2] - self.bbox[0]) * max(0.0, self.bbox[3] - self.bbox[1])

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("embedding")
        d["reasons"] = ",".join(self.reasons)
        return d


# --------------------------------------------------------------------- 质量度量


def estimate_pose(kps: np.ndarray) -> tuple[float, float]:
    """用 5 点关键点粗估 yaw / pitch（度）。

    正值 yaw = 脸朝画面右侧偏。这是启发式量，只用来做门控和诊断，不追求标定精度。
    """
    if kps is None or len(kps) < 5:
        return 0.0, 0.0
    le, re, nose = kps[0], kps[1], kps[2]
    dl = float(np.linalg.norm(nose - le))
    dr = float(np.linalg.norm(nose - re))
    denom = dl + dr
    yaw = 90.0 * (dr - dl) / denom if denom > 1e-6 else 0.0

    eye_mid = (le + re) / 2.0
    mouth_mid = (kps[3] + kps[4]) / 2.0
    vert = float(np.linalg.norm(mouth_mid - eye_mid))
    if vert > 1e-6:
        t = (float(nose[1]) - float(eye_mid[1])) / vert
        pitch = (t - 0.42) * 180.0
    else:
        pitch = 0.0
    return float(np.clip(yaw, -90, 90)), float(np.clip(pitch, -90, 90))


def measure_quality(
    img: np.ndarray,
    bbox,
    kps: np.ndarray | None,
    det_score: float,
    cfg: dict[str, float] | None = None,
) -> dict:
    cfg = {**DEFAULT_QUALITY, **(cfg or {})}
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    xi1, yi1 = max(0, int(x1)), max(0, int(y1))
    xi2, yi2 = min(w, int(x2)), min(h, int(y2))
    face_w = max(0.0, xu := (xi2 - xi1))
    face_h = max(0.0, yi2 - yi1)

    yaw, pitch = estimate_pose(kps)
    iod = 0.0
    if kps is not None and len(kps) >= 2:
        iod = float(np.linalg.norm(np.asarray(kps[0]) - np.asarray(kps[1])))

    blur, brightness = 0.0, 0.0
    if face_w >= 8 and face_h >= 8:
        crop = img[yi1:yi2, xi1:xi2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())

    q_size = np.clip((iod - 20.0) / 50.0, 0.0, 1.0) if iod > 0 else np.clip((face_w - 30.0) / 90.0, 0.0, 1.0)
    q_det = np.clip((det_score - 0.40) / 0.50, 0.0, 1.0)
    q_blur = np.clip(blur / 90.0, 0.0, 1.0)
    q_bright = 1.0 - np.clip(abs(brightness - 135.0) / 110.0, 0.0, 1.0)
    q_pose = 1.0 - np.clip((abs(yaw) - 25.0) / 45.0, 0.0, 1.0)

    quality = float(
        0.30 * q_size + 0.20 * q_det + 0.20 * q_blur + 0.10 * q_bright + 0.20 * q_pose
    )

    reasons: list[str] = []
    if det_score < cfg["min_det"]:
        reasons.append("低检测分")
    if iod and iod < cfg["min_iod"]:
        reasons.append("瞳距过小")
    elif not iod and face_w < cfg["min_face_w"]:
        reasons.append("脸框过小")
    if blur < cfg["min_blur"]:
        reasons.append("模糊")
    if brightness < cfg["min_bright"]:
        reasons.append("偏暗")
    elif brightness > cfg["max_bright"]:
        reasons.append("过曝")
    if abs(yaw) > cfg["max_abs_yaw"]:
        reasons.append("侧脸角度大")
    if quality < cfg["min_quality"]:
        reasons.append("综合质量低")

    return {
        "quality": quality,
        "usable": not reasons,
        "reasons": reasons,
        "yaw": yaw,
        "pitch": pitch,
        "iod": iod,
        "face_w": face_w,
        "blur": blur,
        "brightness": brightness,
    }


# --------------------------------------------------------------------- NMS / 分块


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return float(inter / ua) if ua > 0 else 0.0


def nms(boxes: np.ndarray, thresh: float = 0.45) -> list[int]:
    if len(boxes) == 0:
        return []
    order = np.argsort(-boxes[:, 4])
    keep: list[int] = []
    while len(order):
        i = int(order[0])
        keep.append(i)
        if len(order) == 1:
            break
        rest = order[1:]
        ious = np.array([_iou(boxes[i], boxes[j]) for j in rest])
        order = rest[ious <= thresh]
    return keep


def _grid(size: int, tile: int, overlap: float) -> list[tuple[int, int]]:
    if size <= tile:
        return [(0, size)]
    step = max(1, int(tile * (1.0 - overlap)))
    starts = []
    s = 0
    while s + tile < size:
        starts.append(s)
        s += step
    starts.append(max(0, size - tile))
    return [(s, s + tile) for s in starts]


# --------------------------------------------------------------------- 引擎


def _model_fingerprint(paths: list[Path]) -> str:
    h = hashlib.sha1()
    for p in paths:
        if not p.exists():
            continue
        h.update(p.name.encode())
        h.update(str(p.stat().st_size).encode())
        with p.open("rb") as f:
            h.update(f.read(1 << 20))
    return h.hexdigest()[:12]


class FaceEngine:
    """进程内单例即可；模型加载约 1~2 秒。"""

    def __init__(self, settings: Settings | None = None, model_root: Path | None = None):
        from insightface.app import FaceAnalysis  # 延迟导入，方便无模型的单测

        self.settings = settings or Settings()
        e = self.settings.engine
        self.det_size = int(e.get("det_size", 640))
        self.det_size_group = int(e.get("det_size_group", 1280))
        self.group_pixels = int(e.get("group_pixels", 2_600_000))
        self.tile_enabled = bool(e.get("tile", True))
        self.det_thresh = float(e.get("det_thresh", 0.5))
        name = str(e.get("model_name", "buffalo_l"))

        root = str(model_root) if model_root else "~/.insightface"
        self.app = FaceAnalysis(
            name=name, root=root, allowed_modules=["detection", "recognition"]
        )
        self.app.prepare(ctx_id=-1, det_thresh=self.det_thresh, det_size=(self.det_size,) * 2)
        self.det = self.app.det_model
        self.rec = self.app.models["recognition"]
        mdir = Path(self.app.model_dir)
        self.model_ver = f"{name}-{_model_fingerprint([mdir / 'det_10g.onnx', mdir / 'w600k_r50.onnx'])}"
        self.calls = 0

    # -- 检测 -----------------------------------------------------------
    def _detect_raw(self, img: np.ndarray, size: tuple[int, int]):
        from insightface.app.common import Face

        bboxes, kpss = self.det.detect(img, input_size=size, max_num=0)
        # InsightFace 用 None 表示「没有检测结果」，np.asarray(None) 会变成
        # shape=(1,)，随后 reshape(-1, 5) 得到一个容易被吞掉的异常。
        if bboxes is None:
            return np.empty((0, 5), dtype=np.float32), []
        bboxes = np.asarray(bboxes, dtype=np.float32).reshape(-1, 5)
        if kpss is None:
            kpss = [None] * len(bboxes)
        elif isinstance(kpss, np.ndarray):
            kpss = list(kpss.reshape(-1, 5, 2))
        return bboxes, kpss

    def detect(self, img: np.ndarray) -> list[tuple[np.ndarray, np.ndarray | None]]:
        h, w = img.shape[:2]
        group = h * w >= self.group_pixels
        normal_size = (self.det_size, self.det_size)
        group_size = (self.det_size_group, self.det_size_group)
        # 部分 ONNX 检测权重在某些 CPU EP 上用 1280 动态输入会退化成空结果。
        # 因此先尝试配置的大图尺度，空结果时必须回退到已经验证的默认尺度。
        size = group_size if group else normal_size

        if not (self.tile_enabled and group):
            bboxes, kpss = self._detect_raw(img, size)
            if len(bboxes) == 0 and size != normal_size:
                bboxes, kpss = self._detect_raw(img, normal_size)
            return [(bboxes[i], kpss[i]) for i in range(len(bboxes))]

        # 分块检测：大合照后排的小脸靠这一步捞回来。先跑一次整图，
        # 再以分块结果补小脸；不能只跑分块，否则主体刚好落在分块边界时会漏掉。
        full_boxes, full_kps = self._detect_raw(img, size)
        if len(full_boxes) == 0:
            full_boxes, full_kps = self._detect_raw(img, normal_size)
        tile = self.det_size_group
        xs = _grid(w, tile, 0.25)
        ys = _grid(h, tile, 0.25)
        if len(xs) * len(ys) > 9:  # 再大就不切了，切太碎反而假阳性多
            bboxes, kpss = self._detect_raw(img, size)
            return [(bboxes[i], kpss[i]) for i in range(len(bboxes))]

        acc_boxes: list[np.ndarray] = [b.copy() for b in full_boxes]
        acc_kps: list[np.ndarray | None] = list(full_kps)
        for y0, y1 in ys:
            for x0, x1 in xs:
                sub = img[y0:y1, x0:x1]
                if sub.size == 0:
                    continue
                bboxes, kpss = self._detect_raw(sub, size)
                if len(bboxes) == 0:
                    bboxes, kpss = self._detect_raw(sub, normal_size)
                for i in range(len(bboxes)):
                    b = bboxes[i].copy()
                    b[0] += x0
                    b[1] += y0
                    b[2] += x0
                    b[3] += y0
                    k = kpss[i]
                    if k is not None:
                        k = np.asarray(k, dtype=np.float32).copy()
                        k[:, 0] += x0
                        k[:, 1] += y0
                    acc_boxes.append(b)
                    acc_kps.append(k)

        if not acc_boxes:
            return []
        boxes = np.stack(acc_boxes)
        keep = nms(boxes, 0.45)
        return [(boxes[i], acc_kps[i]) for i in keep]

    # -- 全流程 ---------------------------------------------------------
    def analyze(self, img: np.ndarray, min_face_px: int = 8) -> list[FaceObs]:
        from insightface.app.common import Face

        h, w = img.shape[:2]
        raw = self.detect(img)
        obs: list[FaceObs] = []
        # 按面积从大到小重排，face_idx 稳定（同一张图同一批模型下顺序一致）
        raw.sort(key=lambda t: -((t[0][2] - t[0][0]) * (t[0][3] - t[0][1])))
        for i, (bbox, kps) in enumerate(raw):
            x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
            x1, y1 = max(0.0, x1), max(0.0, y1)
            x2, y2 = min(float(w), x2), min(float(h), y2)
            if x2 - x1 < min_face_px or y2 - y1 < min_face_px:
                continue
            face = Face(bbox=np.array([x1, y1, x2, y2], dtype=np.float32),
                        kps=kps, det_score=float(bbox[4]))
            emb = self.rec.get(img, face)
            emb = np.asarray(emb, dtype=np.float32).reshape(-1)
            n = float(np.linalg.norm(emb))
            if n > 0:
                emb = emb / n
            q = measure_quality(img, [x1, y1, x2, y2], kps, float(bbox[4]), self.settings.quality)
            obs.append(
                FaceObs(
                    face_idx=len(obs),
                    bbox=[x1, y1, x2, y2],
                    det_score=float(bbox[4]),
                    embedding=emb,
                    quality=q["quality"],
                    usable=q["usable"],
                    yaw=q["yaw"],
                    pitch=q["pitch"],
                    iod=q["iod"],
                    face_w=q["face_w"],
                    blur=q["blur"],
                    brightness=q["brightness"],
                    reasons=q["reasons"],
                )
            )
        return obs
