# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：Windows 绿色版（onedir，双击 exe 即用）。

- 静态网页资源打到 _internal/photoorg/web/static；
- 人脸模型不打进 exe，由 build_windows.py 复制到 exe 旁边的 insightface/，
  这样升级程序时模型不用重打，也方便单独替换模型；
- 不做 UPX 压缩，减少杀毒软件误报。
"""
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "照片归档工具"
ICON = ROOT / "packaging" / "photoorg.ico" if sys.platform == "win32" else None
VERSION_FILE = ROOT / "packaging" / "version_info.txt" if sys.platform == "win32" else None

datas = [
    (str(ROOT / "photoorg" / "web" / "static"), "photoorg/web/static"),
]
# InsightFace 的 data/ 下可能有随包资源；体积很小，一并带上。
datas += collect_data_files("insightface", include_py_files=False)

hiddenimports = [
    "multipart",
    "python_multipart",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "insightface.app",
    "insightface.app.face_analysis",
    "insightface.model_zoo",
    "insightface.model_zoo.model_zoo",
    "insightface.model_zoo.scrfd",
    "insightface.model_zoo.arcface_onnx",
    "insightface.utils.face_align",
    "insightface.utils.storage",
    "onnxruntime.capi._pybind_state",
]
# uvicorn 通过字符串动态加载协议/事件循环实现，静态分析容易漏。
hiddenimports += collect_submodules("uvicorn")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "torch",
        "tensorflow",
        "IPython",
        "notebook",
        "PyQt5",
        "PySide6",
        "wx",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON and ICON.exists() else None,
    version=str(VERSION_FILE) if VERSION_FILE and VERSION_FILE.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
