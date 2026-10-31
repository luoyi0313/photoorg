"""小工具：时间、原子写 JSON、批次名解析、人话错误。"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def now_human() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data) -> None:
    """原子写，避免进程被杀时留下半个文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


_BATCH_RE = re.compile(r"^\s*(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})\s*[-_ ]+\s*(.+?)\s*$")


def parse_batch_name(name: str) -> tuple[str, str]:
    """目录名 = 时间 + 活动名称。解析失败时退化为「无日期 + 原名」。

    >>> parse_batch_name("20261029-秋游")
    ('2026-10-29', '秋游')
    """
    m = _BATCH_RE.match(name)
    if not m:
        return "", name
    y, mo, d, activity = m.groups()
    try:
        datetime(int(y), int(mo), int(d))
    except ValueError:
        return "", name
    return f"{y}-{mo}-{d}", activity


def unique_batch_id(base: str, taken: set[str]) -> str:
    """目录名重名时追加序号后缀（§1.1）。"""
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def fmt_score(x: float) -> str:
    return f"{x:.2f}"


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def pct(part: float, total: float) -> str:
    if not total:
        return "0%"
    return f"{part / total * 100:.0f}%"
