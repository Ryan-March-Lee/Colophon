"""Colophon 启动入口：不打包时和 exe 走的是同一条路径。

    python run.py              # 桌面窗口（双击 exe 的效果）
    python run.py --browser    # 浏览器里打开；开发时配合 --reload
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

HOST = "127.0.0.1"
TITLE = "Colophon"
DEFAULT_PORT = 8765


# ---------------------------------------------------------------- 基础设施

def _prepare_streams() -> None:
    """窗口版 exe 没有控制台，sys.stdout 可能是 None 或不认中文。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass


def _say(message: str) -> None:
    if sys.stdout is not None:
        print(message, flush=True)


def _preferred_port(cli_port: int | None) -> int:
    if cli_port:
        return cli_port
    try:
        return int(os.environ.get("COLOPHON_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _free_port(preferred: int, tries: int = 20) -> int:
    """端口被占用时顺延，避免第二次启动直接失败。"""
    for port in range(preferred, preferred + tries):
        with socket.socket() as sock:
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
        return port
    return preferred


def _health(port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=timeout) as resp:
            return b"Colophon" in resp.read(256)
    except OSError:
        return False


def _find_running(preferred: int, limit: int = 10) -> int | None:
    """已经开着一个 Colophon 就复用它，避免起第二份服务器。"""
    for port in range(preferred, preferred + limit):
        if _health(port):
            return port
    return None


def _configure_logging() -> None:
    """窗口版没有控制台，uvicorn 的日志落到 library/colophon.log。"""
    import logging

    from app.paths import LIBRARY

    handler = logging.FileHandler(LIBRARY / "colophon.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _start_server(port: int):
    import logging

    import uvicorn

    from app.main import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app,
        host=HOST,
        port=port,
        log_level="info",
        log_config=None,  # 交给 _configure_logging 的根处理器
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="colophon-server", daemon=True)
    thread.start()

    deadline = time.time() + 40
    while time.time() < deadline:
        if _health(port):
            logging.getLogger("colophon").info("server ready on %s", f"http://{HOST}:{port}/")
            return server
        if not thread.is_alive():
            raise RuntimeError("服务线程提前退出，详见 library/colophon-error.log")
        time.sleep(0.15)
    raise RuntimeError("服务启动超时")


def _report_failure(exc: BaseException) -> None:
    from app.paths import LIBRARY

    try:
        LIBRARY.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (LIBRARY / "colophon-error.log").open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{stamp}] {exc.__class__.__name__}: {exc}\n")
            fh.write(traceback.format_exc())
    except OSError:
        pass

    # 有控制台就让它把 traceback 打出来；窗口版只能弹框
    if sys.stdout is None and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"Colophon 启动失败：\n{exc}\n\n详情见：\n{LIBRARY / 'colophon-error.log'}",
                TITLE,
                0x10,
            )
        except Exception:
            pass


def _focus_existing() -> bool:
    """已经开着一个 Colophon 窗口就把它提到前台，第二次双击不再起副本。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hits: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _param):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value == TITLE:
                hits.append(hwnd)
                return False
            return True

        user32.EnumWindows(callback, 0)
        if not hits:
            return False
        user32.ShowWindow(hits[0], 9)  # SW_RESTORE
        user32.SetForegroundWindow(hits[0])
        return True
    except Exception:
        return False


def _resource_icon() -> Path | None:
    from app.paths import resource_root

    icon = resource_root() / "web" / "favicon.ico"
    return icon if icon.is_file() else None


# ---------------------------------------------------------------- 两种启动方式

def run_window(preferred_port: int) -> None:
    import webview

    from app.paths import LIBRARY

    try:
        _configure_logging()
    except OSError:
        pass

    # 窗口里的下载（PDF 另存）与站外链接交给系统处理
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["SHOW_DEFAULT_MENUS"] = False

    if _focus_existing():
        # 另一个 Colophon 窗口已经开着：把它带上来，本进程直接退出
        return

    running = _find_running(preferred_port)
    if running is not None:
        port, server = running, None
    else:
        port = _free_port(preferred_port)
        server = _start_server(port)

    webview.create_window(
        TITLE,
        f"http://{HOST}:{port}/",
        width=1480,
        height=940,
        min_size=(1040, 700),
        text_select=True,
        background_color="#f5f4ef",
    )
    icon = _resource_icon()
    webview.start(
        debug=bool(os.environ.get("COLOPHON_DEBUG")),
        private_mode=False,
        storage_path=str(LIBRARY / ".colophon" / "webview"),
        **({"icon": str(icon)} if icon else {}),
    )

    if server is not None:
        server.should_exit = True
        time.sleep(0.2)


def run_browser(preferred_port: int, open_browser: bool, reload: bool) -> None:
    import uvicorn

    from app.paths import FROZEN, LIBRARY

    # 窗口版 exe 里没有 stdout，uvicorn 的默认日志配置会直接崩；
    # 这种情况下改成写 library/colophon.log。
    file_logs = sys.stdout is None
    if file_logs:
        try:
            _configure_logging()
        except OSError:
            file_logs = False
    log_kwargs = {"log_config": None} if file_logs else {}

    reload = reload and not FROZEN
    port = preferred_port if reload else _free_port(preferred_port)

    if open_browser:
        url = f"http://{HOST}:{port}/"
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()

    _say(f"Colophon http://{HOST}:{port}/")
    _say(f"书库目录：{LIBRARY}")

    try:
        if reload:
            uvicorn.run("app.main:app", host=HOST, port=port, reload=True, **log_kwargs)
        else:
            from app.main import app as fastapi_app

            uvicorn.run(fastapi_app, host=HOST, port=port, **log_kwargs)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _report_failure(exc)
        raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Colophon")
    parser.add_argument("--browser", action="store_true", help="在浏览器里打开（默认是桌面窗口）")
    parser.add_argument("--port", type=int, default=None, help=f"首选端口，默认 {DEFAULT_PORT}")
    parser.add_argument("--no-browser", action="store_true", help="配合 --browser：不自动打开浏览器")
    parser.add_argument("--reload", action="store_true", help="配合 --browser：开发热重载")
    args = parser.parse_args(argv)

    _prepare_streams()
    port = _preferred_port(args.port)

    if args.browser:
        run_browser(port, open_browser=not args.no_browser, reload=args.reload)
    else:
        run_window(port)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _report_failure(exc)
        raise
