"""PyInstaller 打包入口（Windows 绿色版 exe）。"""
from __future__ import annotations

import multiprocessing
import sys


def _run() -> int:
    from photoorg.desktop import main

    return main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(_run())
