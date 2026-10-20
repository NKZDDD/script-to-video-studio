"""Studio installer updates. Only a verified release from our HTTPS origin is executable."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import threading
from urllib.parse import urlparse

import requests

from . import build_info, paths
from .studio_login import AUTH_URL

VERSION = "2026.10.20.0"
LIFECYCLE = threading.RLock()
INSTALLING = False
MAX_INSTALLER = 2 * 1024**3


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){3}", value):
        raise ValueError("版本号需要四段数字，例如 2026.10.20.0")
    return tuple(map(int, value.split(".")))


def validate_release(data, origin=AUTH_URL):
    if not isinstance(data, dict):
        raise ValueError("更新服务返回格式不正确")
    version(data.get("version"))
    parsed, expected = urlparse(str(data.get("url", ""))), urlparse(origin)
    if (parsed.scheme != "https" or parsed.netloc != expected.netloc or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            not parsed.path.startswith("/downloads/") or not parsed.path.endswith(".exe")):
        raise ValueError("安装包必须来自登录服务同域名的 HTTPS /downloads/ 地址")
    if not re.fullmatch(r"[a-f0-9]{64}", str(data.get("sha256", ""))):
        raise ValueError("安装包 SHA256 无效")
    if type(data.get("bytes")) is not int or not 0 < data["bytes"] <= MAX_INSTALLER:
        raise ValueError("安装包大小无效")
    if data.get("flavor") != (build_info.only() or "all"):
        raise ValueError("更新包与当前程序体系不一致")
    return {k: data.get(k, "") for k in ("version", "url", "sha256", "bytes", "flavor", "notes")}


class UpdateManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.release = None
        self.installer = None
        self.phase = "idle"
        self.message = "点击检查更新"
        self.received = 0

    def status(self):
        with self.lock:
            return {"current": VERSION, "release": self.release, "phase": self.phase,
                    "message": self.message, "received": self.received,
                    "desktop": bool(getattr(self, "install_callback", None))}

    def check(self):
        with self.lock:
            if self.phase in ("checking", "downloading", "installing"):
                return self.status()
            self.phase, self.message = "checking", "正在检查版本"
        try:
            with requests.get(AUTH_URL + "/api/v1/updates/latest",
                              params={"flavor": build_info.only() or "all"},
                              timeout=(10, 20), allow_redirects=False, stream=True) as response:
                if response.status_code == 404:
                    raise ValueError("服务端尚未部署更新接口，请先升级登录服务")
                if response.status_code != 200:
                    raise ValueError("更新服务暂不可用，请稍后重试")
                response.raw.decode_content = True
                raw = response.raw.read(65537)
                if len(raw) > 65536:
                    raise ValueError("版本信息过大")
                import json
                data = json.loads(raw)
            release = validate_release(data["release"]) if data.get("release") else None
            with self.lock:
                # Never install a stale cached artifact after a new check.
                self.release, self.installer, self.received = release, None, 0
                available = release and version(release["version"]) > version(VERSION)
                self.phase = "available" if available else "current"
                self.message = "有新版本可下载" if available else "当前已是最新发布版本"
        except Exception as exc:
            with self.lock:
                self.phase = "error"
                self.message = str(exc) if isinstance(exc, ValueError) else "检查更新失败；请确认登录服务已升级、网络正常后重试"
        return self.status()

    def download(self):
        with self.lock:
            if self.phase in ("checking", "downloading", "installing"):
                return self.status()
            if not self.release or version(self.release["version"]) <= version(VERSION):
                raise ValueError("请先检查新版本")
            self.phase, self.message, self.received = "downloading", "正在下载安装包", 0
            self.installer = None
            release = dict(self.release)
            threading.Thread(target=self._download, args=(release,), daemon=True).start()
        return self.status()

    def _download(self, release):
        target = None
        try:
            folder = Path(paths.data_dir()) / "updates"
            folder.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(folder).free < release["bytes"] + 128 * 1024**2:
                raise ValueError("数据盘空间不足，无法下载安装包")
            target = folder / ("Respect-Studio-" + release["version"] + ".exe")
            partial = target.with_suffix(".part")
            digest, total = hashlib.sha256(), 0
            with requests.get(release["url"], timeout=(15, 60), stream=True,
                              allow_redirects=False, headers={"Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    raise ValueError("安装包下载失败，请确认后台下载地址可访问")
                with partial.open("wb") as fp:
                    for chunk in response.iter_content(1024 * 1024):
                        total += len(chunk)
                        if total > release["bytes"]:
                            raise ValueError("下载体积与发布信息不符")
                        fp.write(chunk)
                        digest.update(chunk)
                        with self.lock:
                            self.received = total
            if total != release["bytes"] or digest.hexdigest() != release["sha256"]:
                raise ValueError("安装包校验不通过，请重新下载")
            os.replace(partial, target)
            with self.lock:
                self.installer, self.phase, self.message = target, "ready", "下载并校验完成；任务结束后可安装"
        except Exception as exc:
            if target:
                try:
                    target.with_suffix(".part").unlink(missing_ok=True)
                except OSError:
                    pass  # A locked partial file must not leave the UI stuck downloading.
            with self.lock:
                self.phase = "error"
                self.message = str(exc) if isinstance(exc, ValueError) else "下载中断或写入失败，请重试"

    def install(self, jobs):
        global INSTALLING
        with LIFECYCLE, self.lock:
            if jobs.active_count():
                raise ValueError("仍有生产任务运行，请等批次结束或先停止批次，再安装更新")
            if not getattr(self, "install_callback", None):
                raise ValueError("当前为浏览器模式；请从安装版 Studio 窗口中安装更新")
            if self.phase != "ready" or not self.installer or not self.release:
                raise ValueError("请先下载并校验安装包")
            candidate = self.installer
            if not candidate.is_file() or candidate.stat().st_size != self.release["bytes"]:
                raise ValueError("安装包已改变，请重新下载")
            with candidate.open("rb") as fp:
                if hashlib.file_digest(fp, "sha256").hexdigest() != self.release["sha256"]:
                    raise ValueError("安装包已改变，请重新下载")
            INSTALLING = True
            try:
                self.install_callback(candidate)
            except Exception:
                INSTALLING = False
                raise
            self.phase, self.message = "installing", "正在退出 Studio 并打开安装器"
            return self.status()


MANAGER = UpdateManager()
