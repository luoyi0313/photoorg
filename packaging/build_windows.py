"""把项目打成 Windows 绿色版目录 + zip。

在 Windows 上由 ``打包Windows版.bat`` 调用；macOS 上也可以跑，
只是产物是 macOS 可执行文件（用于本机冒烟测试打包配置）。

产物结构::

    dist/照片归档工具/
      照片归档工具.exe
      _internal/                 PyInstaller 运行时
      insightface/models/buffalo_l/{det_10g.onnx,w600k_r50.onnx}
      使用说明.txt
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "照片归档工具"
MODEL_NAME = "buffalo_l"
REQUIRED_MODELS = ("det_10g.onnx", "w600k_r50.onnx")

QUICK_START = """\
班级照片归档与出勤统计（Windows 绿色版）
==================================================

一、怎么用
1. 把整个「照片归档工具」文件夹解压到桌面或 D 盘；
   不要只复制 exe，_internal 和 insightface 必须一起保留。
2. 双击「照片归档工具.exe」。
3. 浏览器会自动打开本机网页（通常 http://127.0.0.1:8765/ ，以窗口显示为准）。
   若没有自动打开，手动把窗口里的地址复制到 Edge / Chrome。
4. 使用期间保留黑色命令窗口；关闭窗口或按 Ctrl+C 即停止。
5. 再次双击 exe 会直接打开已经在运行的页面，不会重复启动。

二、数据在哪里
data\\      人员名单、人脸特征、阈值（最重要，请定期备份）
photos\\    上传的活动照片
out\\       生成的归档和报告
roster\\    底库参考照片（可选）
整个文件夹拷走 = 带走全部数据；换电脑时把整个文件夹复制过去即可。

三、注意
- 不要放在 C:\\Program Files 等需要管理员权限的目录。
- 浏览器建议用 Edge 或 Chrome，才能一次选择整个照片文件夹。
- 首次识别一张照片需要几秒；活动照片越多越慢，属正常。
- 本工具只监听 127.0.0.1，照片和特征不会上传网络。
- 人脸模型 buffalo_l 仅用于非商业研究/测试；正式对外使用前请确认许可。
"""


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _model_ready(model_dir: Path) -> bool:
    return all((model_dir / name).is_file() for name in REQUIRED_MODELS)


def _find_local_model() -> Path | None:
    candidates = [
        # 从 macOS/联网机器提前拷过来的离线模型包（打包时完全不用联网）
        ROOT / "windows-offline" / MODEL_NAME,
        ROOT / "vendor" / "insightface" / "models" / MODEL_NAME,
        Path.home() / ".insightface" / "models" / MODEL_NAME,
    ]
    for path in candidates:
        if _model_ready(path):
            return path
    return None


def _download_model() -> Path:
    target_root = ROOT / "vendor" / "insightface"
    _say(f"本地没有完整模型，开始下载 {MODEL_NAME}（约 280 MB，只需一次）……")
    try:
        from insightface.utils.storage import download

        download("models", MODEL_NAME, root=str(target_root))
    except Exception as exc:  # noqa: BLE001 - 给打包者看的人话错误
        raise SystemExit(
            f"模型下载失败：{exc}\n"
            "请联网重试，或手动把 buffalo_l 模型文件夹放到：\n"
            f"  {target_root / 'models' / MODEL_NAME}\n"
            "需要 det_10g.onnx 和 w600k_r50.onnx 两个文件；\n"
            "也可以下载后这样打包：\n"
            "  打包Windows版.bat --model-dir D:\\buffalo_l"
        ) from exc
    model_dir = target_root / "models" / MODEL_NAME
    if not _model_ready(model_dir):
        raise SystemExit(f"模型不完整：{model_dir}")
    return model_dir


def _copy_model(model_dir: Path, dist_dir: Path) -> None:
    target = dist_dir / "insightface" / "models" / MODEL_NAME
    target.mkdir(parents=True, exist_ok=True)
    total = 0
    for name in REQUIRED_MODELS:
        src = model_dir / name
        dst = target / name
        _say(f"  复制模型 {name} ({src.stat().st_size / 1024 / 1024:.0f} MB)")
        shutil.copy2(src, dst)
        total += src.stat().st_size
    _say(f"模型已放入：{target}（{total / 1024 / 1024:.0f} MB）")


def _copy_docs(dist_dir: Path) -> None:
    # utf-8-sig：老版本记事本也能正确显示中文。
    (dist_dir / "使用说明.txt").write_text(QUICK_START, encoding="utf-8-sig")
    for name in ("Windows使用说明.md", "网页操作说明.md", "README.md"):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, dist_dir / name)


def _make_zip(dist_dir: Path, tag: str) -> Path:
    zip_path = dist_dir.parent / f"{APP_NAME}-{tag}.zip"
    if zip_path.exists():
        zip_path.unlink()
    _say(f"正在压缩：{zip_path.name}")
    files = [p for p in dist_dir.rglob("*") if p.is_file()]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in files:
            zf.write(p, p.relative_to(dist_dir.parent))
    return zip_path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打 Windows 绿色版（含模型）")
    parser.add_argument("--skip-model", action="store_true", help="不复制模型（首次运行联网下载）")
    parser.add_argument("--model-dir", help="已下载的 buffalo_l 目录（内含两个 onnx 文件）")
    parser.add_argument("--offline", action="store_true", help="没有本地模型就报错，绝不联网下载")
    parser.add_argument("--skip-zip", action="store_true", help="只生成目录，不压缩")
    parser.add_argument("--clean", action="store_true", help="构建前清空 build/ 与 dist/")
    args = parser.parse_args(argv)

    started = time.time()
    os.chdir(ROOT)
    try:
        import PyInstaller  # noqa: F401
    except ModuleNotFoundError:
        raise SystemExit(
            "没有安装 PyInstaller。请先运行：\n"
            '  python -m pip install -e ".[build]"'
        )

    if args.clean:
        for path in (ROOT / "build", ROOT / "dist"):
            if path.resolve().parent != ROOT:
                raise SystemExit(f"拒绝清理可疑路径：{path}")
            try:
                shutil.rmtree(path, ignore_errors=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise SystemExit(
                    f"无法清理 {path}：{exc}\n"
                    "请关闭正在占用该文件夹的窗口（资源管理器、杀毒软件）后重试。"
                ) from exc

    _say("=" * 60)
    _say(f"  {APP_NAME} · 绿色版打包")
    _say("=" * 60)
    _say(f"仓库：{ROOT}")
    _say(f"Python：{sys.version.split()[0]} ({platform.architecture()[0]})")
    _say()

    model_dir = None
    if not args.skip_model:
        if args.model_dir:
            model_dir = Path(args.model_dir).expanduser().resolve()
            if not _model_ready(model_dir):
                raise SystemExit(
                    f"指定的模型目录不完整：{model_dir}\n"
                    "需要 det_10g.onnx 和 w600k_r50.onnx 两个文件。"
                )
        else:
            model_dir = _find_local_model()
            if model_dir is None:
                if args.offline:
                    raise SystemExit(
                        "没有找到本地模型，已按 --offline 取消。\n"
                        "请把 buffalo_l 放到 windows-offline\\buffalo_l\\，"
                        "或用 --model-dir 指定目录。"
                    )
                model_dir = _download_model()
            else:
                _say(f"使用本地模型：{model_dir}（不会联网下载）")

    _say("[1/3] PyInstaller 打包（需要几分钟）……")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        str(ROOT / "packaging" / "photoorg.spec"),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)

    dist_dir = ROOT / "dist" / APP_NAME
    if not dist_dir.exists():
        raise SystemExit(f"打包没有生成预期目录：{dist_dir}")

    _say()
    _say("[2/3] 复制模型与说明……")
    if model_dir is not None:
        _copy_model(model_dir, dist_dir)
    else:
        _say("已跳过模型：首次双击 exe 时需要联网下载。")
    _copy_docs(dist_dir)

    _say()
    _say("[3/3] 汇总")
    exe_name = f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME
    exe_path = dist_dir / exe_name
    if not exe_path.exists():
        raise SystemExit(f"没有找到可执行文件：{exe_path}")
    size_mb = sum(p.stat().st_size for p in dist_dir.rglob("*") if p.is_file()) / 1024 / 1024
    _say(f"目录：{dist_dir}（{size_mb:.0f} MB）")
    _say(f"程序：{exe_path}")

    if not args.skip_zip:
        tag = "Windows-x64" if os.name == "nt" else f"macOS-{platform.machine()}"
        zip_path = _make_zip(dist_dir, tag)
        digest = _sha256(zip_path)
        (dist_dir.parent / f"{zip_path.stem}.sha256.txt").write_text(
            f"{digest}  {zip_path.name}\n", encoding="utf-8"
        )
        _say(f"压缩包：{zip_path}（{zip_path.stat().st_size / 1024 / 1024:.0f} MB）")
        _say(f"SHA256：{digest}")
        _say()
        _say("把 zip 拷到 Windows 电脑，解压后双击 exe 即可使用。")
    else:
        _say()
        _say("已跳过压缩。把整个 dist/照片归档工具 文件夹拷到 Windows 即可。")

    _say(f"完成，用时 {time.time() - started:.0f} 秒。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
