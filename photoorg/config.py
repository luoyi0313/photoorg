"""全局配置与路径。

设计原则（§9）：person 与 class 解耦；推理产物（faces.db）与人工修正（overrides.db）
物理分离；模型权重全局共享。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 默认参数

DEFAULT_THRESHOLDS: dict[str, float] = {
    # 三区间（§7.1）。默认值只是起点，必须用 calibrate 在本班数据上重算。
    "t_high": 0.55,
    "t_low": 0.42,
    "margin": 0.06,
}

DEFAULT_QUALITY: dict[str, float] = {
    # 质量门控（§6）。不达标的脸「不参与自动判定」，只写明细表并标原因。
    "min_iod": 40.0,        # 瞳距像素下限（自动判定用）
    "min_face_w": 60.0,     # 脸框宽下限
    "min_det": 0.55,        # 检测置信度下限
    "min_blur": 12.0,       # Laplacian 方差下限
    "min_bright": 35.0,     # 平均亮度下限
    "max_bright": 235.0,    # 平均亮度上限
    "max_abs_yaw": 50.0,    # 侧脸角度上限
    "min_quality": 0.22,    # 综合质量分下限
}

DEFAULT_TEMPLATE_GATE: dict[str, float] = {
    # 底库入库门控（§3.2「脏模板的伤害大于没有模板」）
    "min_iod": 48.0,
    "min_quality": 0.35,
    "min_det": 0.60,
    "min_blur": 25.0,
    "max_abs_yaw": 45.0,
}

DEFAULT_BATCH_GATE: dict[str, float] = {
    # 批次级质量门（§7.3）：超阈值 → 整批 INCOMPLETE，不出「未出现」结论
    "max_fail_ratio": 0.20,
    "max_unusable_ratio": 0.20,
}

DEFAULT_ENROLL: dict[str, float] = {
    "max_templates": 20,
    "dedup_sim": 0.95,
}

DEFAULT_ENGINE: dict[str, object] = {
    "model_name": "buffalo_l",
    "det_size": 640,
    "det_size_group": 1280,
    "group_pixels": 2_600_000,   # 超过该像素数视为大合照，自动升分辨率 + 分块
    "tile": True,
    "det_thresh": 0.5,
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".heic"}
HEIC_EXTS = {".heic"}


# ---------------------------------------------------------------- 路径

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    """exe 所在目录；源码模式下是当前目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def default_workspace_root() -> Path:
    """默认工作区。

    - 绿色版 exe：数据放在 exe 旁边，整个文件夹拷走就是全部数据；
    - 源码模式：沿用原来的「当前目录」；
    - 高级场景可用 PHOTOORG_ROOT 覆盖。
    """
    env = os.environ.get("PHOTOORG_ROOT")
    if env:
        return Path(env).expanduser()
    return executable_dir()


def default_model_root() -> Path:
    """InsightFace 模型根目录（下面结构为 models/<模型名>/）。

    exe 绿色版优先用同目录的 ``insightface/``，这样把模型一起打包即可离线使用；
    源码模式仍用 ``~/.insightface``，与原有行为一致。
    PHOTOORG_MODEL_ROOT 可覆盖。
    """
    env = os.environ.get("PHOTOORG_MODEL_ROOT")
    if env:
        return Path(env).expanduser()
    if is_frozen():
        return executable_dir() / "insightface"
    return Path.home() / ".insightface"


def safe_name(name: str) -> str:
    """把用户输入的人名/班级名变成可安全用作目录名的字符串。"""
    name = (name or "").strip()
    name = _UNSAFE.sub("_", name)
    name = name.strip(". ")
    if not name or name in {".", ".."}:
        raise ValueError(f"非法名称: {name!r}")
    return name[:80]


@dataclass
class AppPaths:
    """一个工作区（默认就是项目根目录）下的全部路径。"""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()

    # --- 根级
    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def photos(self) -> Path:
        return Path(os.environ.get("PHOTOORG_PHOTOS", self.root / "photos"))

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def persons_db(self) -> Path:
        return self.data / "persons.db"

    @property
    def classes_dir(self) -> Path:
        return self.data / "classes"

    @property
    def models_dir(self) -> Path:
        return self.data / "models"

    @property
    def cache_dir(self) -> Path:
        return self.data / "cache"

    @property
    def global_config(self) -> Path:
        return self.data / "config.json"

    def class_dir(self, class_name: str) -> Path:
        return self.classes_dir / safe_name(class_name)

    def class_paths(self, class_name: str) -> "ClassPaths":
        return ClassPaths(self, safe_name(class_name))

    def ensure(self) -> None:
        for p in (self.data, self.classes_dir, self.models_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)


@dataclass
class ClassPaths:
    app: AppPaths
    name: str

    @property
    def dir(self) -> Path:
        return self.app.class_dir(self.name)

    @property
    def faces_db(self) -> Path:
        return self.dir / "faces.db"

    @property
    def overrides_db(self) -> Path:
        return self.dir / "overrides.db"

    @property
    def batch_state(self) -> Path:
        return self.dir / "batch_state.json"

    @property
    def thresholds(self) -> Path:
        return self.dir / "thresholds.json"

    @property
    def members(self) -> Path:
        return self.dir / "members.json"

    @property
    def gallery(self) -> Path:
        return self.dir / "gallery"

    @property
    def gallery_dir(self) -> Path:
        """兼容别名"""
        return self.gallery

    @property
    def reports(self) -> Path:
        return self.dir / "reports"

    def person_gallery(self, person_name: str) -> Path:
        return self.gallery / safe_name(person_name)

    def ensure(self) -> None:
        for p in (self.dir, self.gallery, self.reports):
            p.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    """把一个班级的阈值/质量门聚合起来，避免到处读 json。"""

    t_high: float = DEFAULT_THRESHOLDS["t_high"]
    t_low: float = DEFAULT_THRESHOLDS["t_low"]
    margin: float = DEFAULT_THRESHOLDS["margin"]
    quality: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_QUALITY))
    template_gate: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TEMPLATE_GATE))
    batch_gate: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BATCH_GATE))
    enroll: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ENROLL))
    engine: dict[str, object] = field(default_factory=lambda: dict(DEFAULT_ENGINE))

    def as_dict(self) -> dict:
        return {
            "t_high": self.t_high,
            "t_low": self.t_low,
            "margin": self.margin,
            "quality": self.quality,
            "template_gate": self.template_gate,
            "batch_gate": self.batch_gate,
            "enroll": self.enroll,
            "engine": self.engine,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "Settings":
        d = d or {}
        s = cls()
        s.t_high = float(d.get("t_high", s.t_high))
        s.t_low = float(d.get("t_low", s.t_low))
        s.margin = float(d.get("margin", s.margin))
        for key in ("quality", "template_gate", "batch_gate", "enroll", "engine"):
            merged = getattr(s, key)
            merged.update(d.get(key) or {})
            setattr(s, key, merged)
        return s
