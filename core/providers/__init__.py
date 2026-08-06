# -*- coding: utf-8 -*-
"""服务商注册表。

新增服务商：
    1. 在本目录写 myprovider.py，继承 Provider，实现 capabilities/generate_*
    2. 在下面 _CLASSES 里加一行
前端会自动出现该服务商及其模型/参数选项，无需改前端。
"""

from __future__ import annotations

from .base import ImageTask, Provider, VideoTask   # noqa: F401  对外导出
from .aicopy import AicopyProvider
from .kunji import KunjiProvider
from .lingganya import LingganyaProvider
from .m86 import M86Provider
from .octopus import OctopusProvider
from .paisio import PaisioProvider
from .zeroapi import ZeroApiProvider

# 顺序 = 前端下拉的默认顺序，把实测稳的排前面
_CLASSES = [PaisioProvider, LingganyaProvider, ZeroApiProvider,
            M86Provider, AicopyProvider, KunjiProvider, OctopusProvider]

REGISTRY = {c.id: c for c in _CLASSES}

# 别名 → 正式 id。同一家网关常有好几个叫法（「鹤」「派系」「pis」都是
# api.paisio.online），配置里写了哪个都该认，不然会报「未知服务商」。
ALIASES = {a: c.id for c in _CLASSES for a in getattr(c, "aliases", ())}


def resolve_id(provider_id: str) -> str:
    pid = (provider_id or "").strip()
    return pid if pid in REGISTRY else ALIASES.get(pid, pid)


def list_capabilities() -> list:
    """所有服务商的能力声明（前端渲染用）。"""
    return [c().capabilities() for c in _CLASSES]


def build(provider_id: str, api_key: str, base_url: str = "",
          proxy: str = "", timeout: int = 900) -> Provider:
    cls = REGISTRY.get(resolve_id(provider_id))
    if not cls:
        raise ValueError(f"未知服务商: {provider_id}"
                         f"（可用: {'、'.join(REGISTRY)}；"
                         f"别名: {'、'.join(ALIASES) or '无'}）")
    return cls(api_key=api_key, base_url=base_url, proxy=proxy, timeout=timeout)
