"""Small native WebView2 host. No Chromium distribution and no console window."""
from __future__ import annotations

import ctypes
import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import threading

from . import paths, updates

MUTEX = "Local\\Respect.Studio.Desktop"


def setup_logs():
    """Windowed PyInstaller has no stdout; bounded logs retain diagnostics."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = Path(paths.data_dir()) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("studio-console")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(folder / "desktop.log", maxBytes=2 * 1024**2,
                                  backupCount=2, encoding="utf-8")
    logger.addHandler(handler)

    class Stream(io.TextIOBase):
        def write(self, text):
            if text.strip():
                logger.info(text.rstrip())
            return len(text)

        def flush(self):
            handler.flush()

    sys.stdout = sys.stderr = Stream()


def hide_child_consoles():
    # All subprocess paths (including third-party ffmpeg tools) inherit this
    # setting in our process. Pipes and return codes keep their normal semantics.
    if os.name != "nt" or getattr(subprocess.Popen, "_studio_hidden", False):
        return
    original = subprocess.Popen

    class HiddenPopen(original):
        _studio_hidden = True

        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = HiddenPopen


def show_error(text):
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, str(text), "Respect Studio", 0x10)


def launch_update(installer):
    """Start the updater before closing; it waits for this process to exit."""
    directory = Path(paths.PROGRAM_DIR).resolve()
    if not getattr(sys, "frozen", False) or not (directory / "Respect-Studio.exe").is_file():
        raise ValueError("请从已安装的 Studio 中重启更新")
    # NSIS requires /D to be the last argument, without quotes even with spaces.
    # Pass a command line directly to CreateProcess, never through a shell.
    command = subprocess.list2cmdline([str(installer), "/S", f"/STUDIOUPDATE={os.getpid()}"])
    command += " /D=" + str(directory)
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1", STV_DATA_DIR=paths.data_dir())
    try:
        return subprocess.Popen(command, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        raise ValueError("无法启动更新程序，Studio 尚未退出；请重试或手动运行已下载的安装包") from exc


def run(server, url, jobs):
    import webview
    handle = None
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, False, MUTEX)
        if not handle:
            raise OSError("无法创建 Studio 运行锁")
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(handle)
            server.server_close()
            show_error("Studio 已在运行，请切换到已打开的窗口。")
            return 0
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    window = webview.create_window("Respect Studio", url, width=1400, height=900,
                                  min_size=(960, 640), text_select=True)

    def closing():
        with updates.LIFECYCLE:
            if updates.active_jobs(jobs):
                show_error("仍有任务运行。请在工作台停止批次或等待完成，再关闭 Studio。")
                return False
            updates.INSTALLING = True  # prevent a concurrent start after the close check
        return True

    def request_install(installer):
        launch_update(installer)
        # Let the HTTP response reach the page before shutting down its host.
        threading.Timer(0.5, window.destroy).start()

    window.events.closing += closing
    updates.MANAGER.install_callback = request_install
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        webview.start(gui="edgechromium" if os.name == "nt" else None,
                      icon=paths.res("web", "favicon.ico"),
                      private_mode=False, storage_path=str(Path(paths.data_dir()) / "webview"),
                      localization={"global.quit": "退出", "global.cancel": "取消",
                                    "global.ok": "确定", "global.saveFile": "保存文件"})
    finally:
        updates.MANAGER.install_callback = None
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if handle:
            kernel.CloseHandle(handle)
    return 0
