"""生成拷到 Windows 的传输整包（源码 + 离线模型 + Python 安装包 + bat）。

在 macOS 或 Windows 上都可以运行：

    python packaging/make_transfer_zip.py
    python packaging/make_transfer_zip.py --output D:\\photoorg-windows-build.zip

不会打包 data/、photos/、out/、.venv/ 等本机数据。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_ROOT = "photoorg-windows-build"
DEFAULT_OUTPUT = ROOT / "photoorg-windows-build.zip"
MODEL_DIR = ROOT / "windows-offline" / "buffalo_l"
PYTHON_INSTALLER = ROOT / "windows-offline" / "python-3.11.9-amd64.exe"
REQUIRED_MODELS = ("det_10g.onnx", "w600k_r50.onnx")

ROOT_FILES = (
    "pyproject.toml",
    "README.md",
    "Windows使用说明.md",
    "网页操作说明.md",
    "build-windows.bat",
    "install-python.bat",
    "short-path-build.bat",
    "make-transfer-zip.bat",
    "打包Windows版.bat",
    "安装Python-Windows.bat",
    "短路径打包.bat",
    "制作传输包.bat",
    "制作传输包.command",
    "首次安装并打开网页-Windows.bat",
    "打开网页-Windows.bat",
)


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _check_offline() -> None:
    missing = [p for p in (MODEL_DIR / name for name in REQUIRED_MODELS) if not p.is_file()]
    if not PYTHON_INSTALLER.is_file():
        missing.append(PYTHON_INSTALLER)
    if missing:
        _say("缺少离线文件：")
        for p in missing:
            _say(f"  {p}")
        _say()
        _say("请先把 windows-offline/ 准备好（Python 安装包 + buffalo_l 两个模型）。")
        raise SystemExit(2)


def _collect(output: Path) -> list[Path]:
    files: list[Path] = []

    for p in (ROOT / "photoorg").rglob("*"):
        if (
            p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix in {".py", ".html", ".css", ".js"}
        ):
            files.append(p)

    for p in (ROOT / "packaging").rglob("*"):
        if (
            p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix.lower() in {".py", ".spec", ".txt", ".ico", ".png"}
        ):
            files.append(p)

    tests = ROOT / "tests"
    if tests.exists():
        for p in tests.rglob("*.py"):
            if "__pycache__" not in p.parts:
                files.append(p)

    for name in ROOT_FILES:
        p = ROOT / name
        if p.is_file():
            files.append(p)

    for p in (ROOT / "windows-offline").rglob("*"):
        if p.is_file():
            files.append(p)

    # 不要把自己打进自己；也不要把本机数据/虚拟环境带进去。
    output = output.resolve()
    return sorted({p for p in files if p.resolve() != output})


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Windows 传输整包 zip")
    parser.add_argument(
        "--output",
        help=f"输出路径（默认 {DEFAULT_OUTPUT.name}）",
    )
    args = parser.parse_args(argv)

    output = Path(args.output).expanduser() if args.output else DEFAULT_OUTPUT
    if not output.is_absolute():
        output = ROOT / output

    _say("=" * 60)
    _say("  photoorg · 生成 Windows 传输包")
    _say("=" * 60)
    _say(f"当前目录：{ROOT}")
    _say(f"输出文件：{output}")
    _say()

    _check_offline()
    files = _collect(output)
    if not files:
        raise SystemExit("没有找到可打包的文件")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    _say(f"正在打包 {len(files)} 个文件（约 200 MB）...")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in files:
            zf.write(p, f"{ARCHIVE_ROOT}/{p.relative_to(ROOT).as_posix()}")

    digest = _sha256(output)
    sha_file = output.with_suffix(".sha256.txt")
    sha_file.write_text(f"{digest}  {output.name}\n", encoding="utf-8")

    _say()
    _say(f"完成：{output}")
    _say(f"大小：{output.stat().st_size / 1024 / 1024:.1f} MB")
    _say(f"SHA256：{digest}")
    _say(f"校验文件：{sha_file}")
    _say()
    _say("把 zip 拷到 Windows，解压后双击 安装Python-Windows.bat / 打包Windows版.bat。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
