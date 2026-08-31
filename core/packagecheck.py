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
from typing import Callable


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


def run_package_check() -> dict:
    """返回适合命令行和打包脚本消费的完整检查结果。"""
    checks = [
        _try("HTTP 请求库", _check_requests),
        _try("对象存储库", _check_object_storage),
        _try("图片库", _check_pillow),
        _try("PDF 库", _check_pdf),
        _try("视频拼接库", _check_ffmpeg),
    ]
    return {"ok": all(x["ok"] for x in checks), "checks": checks}
