# -*- coding: utf-8 -*-
"""发布包运行库自检。

这份检查必须在打出来的 exe 内执行。仅在打包机上 ``import`` 成功，不能证明
PyInstaller 把动态导入的子模块、Pillow 编解码器或 imageio-ffmpeg 的可执行文件
一起带走了。
"""

from __future__ import annotations

import io
import os
import subprocess
import importlib
import hashlib
from pathlib import Path
from typing import Callable

RUNTIME_MODULES = ["pypdf", "PIL.Image", "imageio_ffmpeg", "boto3", "botocore",
                   "psutil", "webview",
                   "core.agent_projects", "core.agent_runtime", "core.agent_handoff",
                   "core.assemble", "core.bulk_place", "core.recycle", "core.submitted",
                   "server.agent_api", "core.studio_login"]


def resource_inventory() -> dict:
    """Hashes from the actual source/bundle, never from the user's data directory."""
    from . import paths
    base = Path(paths.BUNDLE_DIR)
    files = [base / "web" / name for name in ("agent.html", "agent.js", "agent.css", "login.js", "login.css", "updates.js", "respect-logo.png", "favicon.ico")]
    files += list((base / "prompts").glob("*.md"))
    files += [p for p in (base / "skills" / "production-skill").rglob("*")
              if p.is_file() and "__pycache__" not in p.parts]
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def _try(name: str, fn: Callable[[], str]) -> dict:
    try:
        detail = fn()
        return {"name": name, "ok": True, "detail": detail}
    except Exception as exc:  # noqa: BLE001 - 自检需要把所有缺项一次列全
        return {"name": name, "ok": False,
                "detail": f"{type(exc).__name__}: {exc}"}


def _check_requests() -> str:
    import certifi
    import requests
    import urllib3

    ca = certifi.where()
    if not ca or not os.path.isfile(ca) or os.path.getsize(ca) < 10_000:
        raise FileNotFoundError(f"HTTPS 根证书文件不存在或异常：{ca!r}")
    requests.Session()

    return (f"requests {requests.__version__}; urllib3 {urllib3.__version__}; "
            "HTTPS 根证书正常")


def _check_object_storage() -> str:
    import boto3
    import botocore
    import s3transfer
    from boto3.s3.transfer import TransferConfig
    from botocore.config import Config

    # 构造配置对象不会访问网络，但能覆盖实际上传时会动态加载的关键子模块。
    cfg = Config(connect_timeout=1, retries={"max_attempts": 1})
    TransferConfig(max_concurrency=1)
    client = boto3.client(
        "s3", endpoint_url="https://example.invalid", region_name="auto",
        aws_access_key_id="package-check", aws_secret_access_key="package-check",
        config=cfg,
    )
    # 不访问网络；生成签名可验证 botocore 的 S3 服务模型、端点数据和签名模块齐全。
    signed = client.generate_presigned_url(
        "put_object", Params={"Bucket": "package-check", "Key": "probe"},
        ExpiresIn=60,
    )
    if "package-check" not in signed:
        raise RuntimeError("对象存储预签名结果异常")
    return (f"boto3 {boto3.__version__}; botocore {botocore.__version__}; "
            f"s3transfer {s3transfer.__version__}")


def _check_pillow() -> str:
    from PIL import Image

    # 只 import PIL 不够：丢了插件时 import 正常，真正读写 PNG/JPEG 才报错。
    for fmt in ("PNG", "JPEG"):
        buf = io.BytesIO()
        Image.new("RGB", (3, 2), (12, 34, 56)).save(buf, format=fmt)
        buf.seek(0)
        with Image.open(buf) as im:
            im.load()
            if im.size != (3, 2):
                raise RuntimeError(f"{fmt} 编解码结果尺寸错误：{im.size}")
    import PIL

    return f"Pillow {PIL.__version__}; PNG/JPEG 编解码正常"


def _check_pdf() -> str:
    from pypdf import PdfReader, PdfWriter
    import pypdf

    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=20, height=20)
    writer.write(buf)
    buf.seek(0)
    if len(PdfReader(buf).pages) != 1:
        raise RuntimeError("PDF 写入后无法重新读取")
    return f"pypdf {pypdf.__version__}; 读写正常"


def _check_ffmpeg() -> str:
    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    if not exe or not os.path.isfile(exe):
        raise FileNotFoundError(f"imageio-ffmpeg 自带程序不存在：{exe!r}")
    size = os.path.getsize(exe)
    if size < 10 * 1024 * 1024:
        raise RuntimeError(f"ffmpeg 文件异常小：{size} bytes")
    result = subprocess.run([exe, "-version"], capture_output=True,
                            timeout=20, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 无法执行，退出码 {result.returncode}")
    first = (result.stdout or b"").decode("utf-8", errors="replace").splitlines()
    version = first[0] if first else "版本输出为空"
    return f"{version}; {size / 1024 / 1024:.1f} MB"


def _check_agent_assets() -> str:
    from . import paths, agent_projects
    for name in ("agent.html", "agent.js", "agent.css", "login.js", "login.css", "updates.js", "respect-logo.png", "favicon.ico"):
        if not os.path.isfile(paths.res("web", name)):
            raise FileNotFoundError(f"Agent 页面资源缺失：{name}")
    manifest = agent_projects.file_manifest(agent_projects.skill_source())
    if len(manifest) < 51 or "SKILL.md" not in manifest:
        raise FileNotFoundError("完整生产技能包缺失")
    if paths.FROZEN:
        from .client_build import MANIFEST, identity
        if Path(paths.PROGRAM_DIR, MANIFEST).exists():
            identity()
    return f"Agent 页面资源齐全；完整技能包 {len(manifest)} 文件"


def _check_agent_creation() -> str:
    import tempfile
    from . import agent_projects as A, providers
    with tempfile.TemporaryDirectory(prefix="stv-package-project-") as base:
        pj = A.create_project(base, {"title": "打包自检项目", "source": "离线自检"},
                              providers.list_capabilities())
        root = Path(pj.root)
        if not root.resolve().is_relative_to(Path(base).resolve()):
            raise ValueError("自检项目没有写入隔离目录")
        template = (root / "00_项目说明" / "AGENT_TASK.md").read_text(encoding="utf-8")
        copied = A.file_manifest(root / "00_项目说明" / "skill" / "production-skill")
        if str(root) not in template or copied != A.file_manifest(A.skill_source()):
            raise ValueError("项目契约或技能快照不完整")
        return f"新建项目与技能快照 {len(copied)} 文件正常"


def _check_threads() -> str:
    import psutil
    threads = psutil.Process().num_threads()
    if threads < 1:
        raise ValueError("线程统计不可用")
    return f"当前线程数 {threads}"


def run_package_check() -> dict:
    """返回适合命令行和打包脚本消费的完整检查结果。"""
    checks = [
        _try("离线项目创建", _check_agent_creation),
        _try("线程统计", _check_threads),
        _try("Agent 工作台资源", _check_agent_assets),
        _try("HTTP 请求库", _check_requests),
        _try("对象存储库", _check_object_storage),
        _try("图片库", _check_pillow),
        _try("PDF 库", _check_pdf),
        _try("视频拼接库", _check_ffmpeg),
    ]
    modules = {}
    for name in RUNTIME_MODULES:
        item = _try(name, lambda name=name: (importlib.import_module(name), "可导入")[1])
        modules[name] = item["ok"]
        checks.append(item)
    inventory = {}
    try:
        inventory = resource_inventory()
    except Exception as exc:
        checks.append({"name": "发布资源校验和", "ok": False, "detail": str(exc)})
    from . import providers
    provider_ids = sorted(p["id"] for p in providers.status()["providers"] if p.get("builtin"))
    return {"ok": all(x["ok"] for x in checks), "checks": checks,
            "modules": modules, "resources": inventory, "providers": provider_ids}
