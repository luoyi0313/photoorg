"""把 CI 产物改成 ASCII 文件名，避免 GitHub Release 丢掉中文前缀。

本地构建仍然生成「照片归档工具-Windows-x64.zip」；只有 GitHub Actions
在上传前调用本脚本，生成 photoorg-Windows-x64.zip / .sha256.txt。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SRC_ZIP = DIST / "照片归档工具-Windows-x64.zip"
DST_ZIP = DIST / "photoorg-Windows-x64.zip"
SRC_SHA = DIST / "照片归档工具-Windows-x64.sha256.txt"
DST_SHA = DIST / "photoorg-Windows-x64.sha256.txt"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not SRC_ZIP.is_file():
        raise SystemExit(f"没有找到构建产物：{SRC_ZIP}")
    if DST_ZIP.exists():
        DST_ZIP.unlink()
    SRC_ZIP.replace(DST_ZIP)
    DST_SHA.write_text(
        f"{_sha256(DST_ZIP)}  {DST_ZIP.name}\n", encoding="utf-8"
    )
    if SRC_SHA.exists():
        SRC_SHA.unlink()
    print(f"release asset: {DST_ZIP.name}")
    print(f"sha256 file : {DST_SHA.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
