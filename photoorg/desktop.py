"""桌面绿色版入口：双击 exe → 准备模型 → 启动本机网页 → 打开浏览器。

设计目标（Windows 老师电脑）：
- 不需要命令行知识，双击即可；
- 数据放在 exe 同一目录，整个文件夹拷走就带走全部照片与名单；
- 人脸模型优先使用 exe 旁边的 ``insightface/``，没打包模型时才联网下载；
- 重复双击不会起第二个服务：发现同目录的服务在跑，就直接打开浏览器。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import warnings
import webbrowser
from pathlib import Path

from . import __version__
from .config import (
    AppPaths,
    default_workspace_root,
    is_frozen,
)
from .db import PersonStore

APP_TITLE = "班级照片归档与出勤统计"
DEFAULT_CLASS = "我的班"
DEFAULT_PORT = 8765
MODEL_NAME = "buffalo_l"
REQUIRED_MODEL_FILES = ("det_10g.onnx", "w600k_r50.onnx")

# 本机健康检查绝不能走系统代理：学校/公司电脑常配代理，localhost 可能被转发。
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class DesktopError(RuntimeError):
    """可以直接展示给用户看的启动错误。"""


# ------------------------------------------------------------------ 控制台


def _setup_console() -> None:
    """让 Windows 控制台正确显示中文和 emoji，并设置窗口标题。"""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleTitleW(APP_TITLE)
            # 部分老系统仍使用 GBK；切到 UTF-8 后中文/符号不会变成问号。
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _say(text: str = "") -> None:
    print(text, flush=True)


def _pause() -> None:
    """双击运行时保留窗口，方便看清错误；从命令行/CI 调用时不阻塞。"""
    if not is_frozen():
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\n按回车键关闭此窗口……")
    except Exception:
        pass


# ------------------------------------------------------------------ 工作区


def _ensure_workspace(root: Path) -> AppPaths:
    """首次运行自动建目录与空白班级，避免用户先学 CLI。"""
    app = AppPaths(root)
    app.ensure()
    for p in (app.photos, app.out, app.root / "roster"):
        p.mkdir(parents=True, exist_ok=True)

    cfg = app.global_config
    if cfg.exists():
        try:
            json.loads(cfg.read_text("utf-8"))
        except Exception:
            # 配置文件损坏时不让程序起不来：备份后重建。
            try:
                cfg.replace(cfg.with_suffix(".json.bad"))
            except Exception:
                pass

    store = PersonStore(app.persons_db)
    classes = store.classes()
    if not cfg.exists():
        if not classes:
            store.upsert_class(DEFAULT_CLASS)
            classes = store.classes()
        data = {"default_class": classes[0]["name"] if classes else DEFAULT_CLASS}
        cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return app


def _default_class(app: AppPaths, override: str | None = None) -> str:
    if override:
        return override
    try:
        data = json.loads(app.global_config.read_text("utf-8"))
        if data.get("default_class"):
            return str(data["default_class"])
    except Exception:
        pass
    classes = PersonStore(app.persons_db).classes()
    return classes[0]["name"] if classes else DEFAULT_CLASS


# ------------------------------------------------------------------ 模型


def _model_files_ready(model_root: Path, model_name: str) -> bool:
    model_dir = model_root / "models" / model_name
    if model_name != MODEL_NAME:
        return model_dir.is_dir() and any(model_dir.glob("*.onnx"))
    return all((model_dir / f).is_file() for f in REQUIRED_MODEL_FILES)


def _ensure_model(model_root: Path, model_name: str) -> None:
    """检查本地模型；没打包模型时给出进度并下载一次。"""
    if _model_files_ready(model_root, model_name):
        return

    if model_name != MODEL_NAME:
        # 自定义模型交给 InsightFace 自己处理，不猜文件名。
        return

    model_dir = model_root / "models" / model_name
    if model_dir.exists():
        # 上次下载中断留下半个目录时，InsightFace 会误以为已下载完成。
        broken = model_dir.with_name(f"{model_name}.incomplete")
        try:
            if broken.exists():
                import shutil

                shutil.rmtree(broken, ignore_errors=True)
            model_dir.rename(broken)
        except OSError as exc:
            raise DesktopError(
                f"模型目录不完整且无法移动：{model_dir}\n{exc}\n"
                "请删除该目录后重试，或把完整的 buffalo_l 模型放进去。"
            ) from exc

    _say("首次启动需要准备人脸模型（约 280 MB，只需一次）。")
    _say(f"模型保存到：{model_root}")
    _say("正在下载，请保持网络连接……")
    try:
        from insightface.utils.storage import ensure_available

        ensure_available("models", model_name, root=str(model_root))
    except Exception as exc:
        raise DesktopError(
            "人脸模型下载失败。\n"
            f"目标目录：{model_root / 'models' / model_name}\n"
            f"错误：{exc}\n\n"
            "可以联网后重试；也可以手动把 buffalo_l 模型文件夹放到上面的目录。"
        ) from exc

    if not _model_files_ready(model_root, model_name):
        raise DesktopError(
            f"模型下载后仍缺少文件：{model_dir}\n"
            "请确认 det_10g.onnx 与 w600k_r50.onnx 都在该目录。"
        )
    _say("模型准备完成。")


# ------------------------------------------------------------------ 端口 / 实例


def _norm_path(p: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(p))))


def _health(port: int) -> dict | None:
    """本机服务健康检查；端口没服务或不是本程序时返回 None。"""
    try:
        with _LOCAL_OPENER.open(
            f"http://127.0.0.1:{port}/api/health", timeout=0.8
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("ok"):
                return data
    except Exception:
        pass
    return None


def _bindable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Windows 上 SO_REUSEADDR 会允许「抢占」正在监听的端口，不能用于探测。
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _same_workspace(health: dict, root: Path) -> bool:
    theirs = health.get("root")
    return not theirs or _norm_path(theirs) == _norm_path(root)


def _pick_port(
    preferred: int, root: Path, allow_reuse: bool = True
) -> tuple[int, dict | None]:
    """返回 (端口, 已有同工作区实例的 health)。

    优先复用同目录已经启动的服务；端口被别的软件占用就顺延。
    ``allow_reuse=False`` 时跳过已有实例，用于强制启动第二个服务（调试用）。
    """
    for port in range(preferred, preferred + 20):
        health = _health(port)
        if health:
            if allow_reuse and _same_workspace(health, root):
                return port, health
            continue
        if _bindable(port):
            return port, None
        # 端口已被占用但健康检查还没就绪：可能另一个实例正在启动。
        # 等一秒再判断，避免双击两次时起来两个服务抢同一个数据库。
        for _ in range(4):
            time.sleep(0.25)
            health = _health(port)
            if health:
                if _same_workspace(health, root):
                    return port, health
                break
        if _health(port) is None and _bindable(port):
            return port, None
    raise DesktopError(
        f"端口 {preferred}–{preferred + 19} 都被占用了。请关闭占用端口的程序后重试。"
    )


def _open_browser_when_ready(port: int, url: str, no_browser: bool) -> None:
    deadline = time.time() + 90.0
    while time.time() < deadline:
        if _health(port):
            if not no_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            return
        time.sleep(0.25)
    _say(f"服务启动较慢，请手动在浏览器打开：{url}")


# ------------------------------------------------------------------ 入口


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="照片归档工具",
        description="启动本机网页界面（照片和特征都只留在本机）",
    )
    p.add_argument("--root", help="工作区目录（默认 exe 所在目录）")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口，默认 {DEFAULT_PORT}")
    p.add_argument("--host", default="127.0.0.1", help="监听地址，默认只允许本机")
    p.add_argument("--class", dest="class_name", help="启动时默认显示的班级")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--no-reuse", action="store_true", help="端口已有服务时也重新启动")
    p.add_argument("--check", action="store_true", help="只做环境/模型自检后退出")
    return p


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    # skimage 的旧版 estimate 弃用提示会混在正常输出里，普通用户看了会以为出错。
    warnings.filterwarnings(
        "ignore",
        message=r".*`estimate` is deprecated.*",
        category=FutureWarning,
    )
    args = _build_parser().parse_args(argv)

    root = Path(args.root).expanduser() if args.root else default_workspace_root()
    try:
        root = root.resolve()
        app = _ensure_workspace(root)
    except Exception as exc:
        _say(f"❌ 无法创建/读取工作区：{root}\n{exc}")
        _say("请把程序放在有写权限的文件夹（例如桌面或 D 盘），不要放在 Program Files。")
        _pause()
        return 1

    model_env = os.environ.get("PHOTOORG_MODEL_ROOT")
    if model_env:
        model_root = Path(model_env).expanduser()
    elif is_frozen():
        # 绿色版：模型跟 exe 放在一起（不是跟工作区走），这样 --root 改数据目录也能离线用。
        model_root = Path(sys.executable).resolve().parent / "insightface"
    else:
        model_root = Path.home() / ".insightface"

    # FaceEngine 默认读取这个环境变量；保证 Web 进程里后续加载的模型与上面自检的是同一份。
    os.environ["PHOTOORG_MODEL_ROOT"] = str(model_root)

    _say("=" * 60)
    _say(f"  {APP_TITLE}  v{__version__}")
    _say("=" * 60)
    _say(f"程序目录：{Path(sys.executable).resolve().parent if is_frozen() else Path.cwd()}")
    _say(f"工作区　：{app.root}")
    _say(f"照片目录：{app.photos}")
    _say(f"模型目录：{model_root}")
    _say()

    port: int | None = None
    existing: dict | None = None
    if not args.check:
        try:
            port, existing = _pick_port(
                int(args.port), app.root, allow_reuse=not args.no_reuse
            )
        except DesktopError as exc:
            _say(f"❌ {exc}")
            _pause()
            return 1

        if existing:
            if str(existing.get("version") or "") not in ("", __version__):
                _say(
                    f"检测到旧版本（v{existing.get('version')}）还在运行。\n"
                    "请先关闭旧程序的黑色窗口，再重新双击本程序。"
                )
                _pause()
                return 1
            url = f"http://127.0.0.1:{port}/"
            _say(f"检测到这个工作区已经在运行，直接打开：{url}")
            if not args.no_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            time.sleep(1.0)
            return 0

    try:
        _ensure_model(model_root, MODEL_NAME)
    except DesktopError as exc:
        _say(f"❌ {exc}")
        _pause()
        return 1

    if args.check:
        _say("正在加载人脸模型并做一次推理自检……")
        try:
            from .config import Settings
            from .engine import FaceEngine

            engine = FaceEngine(Settings())
            _say(f"模型版本：{engine.model_ver}")
            _say("✅ 自检通过：目录、模型与推理运行库都可用。")
            return 0
        except Exception:
            _say("\n❌ 模型/推理运行库自检失败：\n")
            traceback.print_exc()
            _pause()
            return 1

    assert port is not None
    url = f"http://127.0.0.1:{port}/"
    _say(f"网页地址：{url}")
    _say("浏览器会自动打开；若没有，请手动复制上面的地址。")
    _say("使用期间请保留这个黑色窗口；关闭窗口或按 Ctrl+C 即停止服务。")
    _say()

    threading.Thread(
        target=_open_browser_when_ready,
        args=(port, url, bool(args.no_browser)),
        daemon=True,
    ).start()

    try:
        import uvicorn

        from .web.app import create_app

        app_obj = create_app(app.root, default_class=_default_class(app, args.class_name))
        uvicorn.run(
            app_obj,
            host=args.host,
            port=port,
            log_level="warning",
            access_log=False,
        )
    except KeyboardInterrupt:
        _say("\n服务已停止。")
        return 0
    except Exception:
        _say("\n❌ 服务启动失败，错误信息如下：\n")
        traceback.print_exc()
        _pause()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
