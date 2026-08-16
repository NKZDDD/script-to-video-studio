# -*- coding: utf-8 -*-
"""这一版 exe 是给哪套体系用的。

两套体系的代码和模板**始终都在包里** —— 限制的只是「新建项目时能选哪套」。
这样分出来的两个包，各自主打一套，但都能打开对方建的老项目、
照常跑完、照常看产物。

为什么不真的裁掉另一套：
  · 裁了之后拿错包打开老项目 = 产物全被判成「还没做」，重跑一遍花第二份钱
  · 引擎层（服务商、诊断、打包、并发）是两套共用的，裁不干净
  · 包大小省不下多少 —— 大头是 Python 运行时和依赖，不是那几十 KB 模板

`""` = 两套都能建（源码方式跑、以及不带参数打的包就是这个）。
打包时由 打包exe.py 写入。
"""

from __future__ import annotations

import os

# 打包时会被改写成 "v34" 或 "v61"
SYSTEM = ""


def only() -> str:
    """这一版限定的体系；空 = 不限定。

    环境变量优先，方便同一个 exe 临时按另一套跑一次去验证，
    不用重打一版。
    """
    return (os.environ.get("STV_SYSTEM") or SYSTEM or "").strip().lower()


def flavor_name(labels: dict) -> str:
    """给人看的这一版叫什么。"""
    s = only()
    if s and s in labels:
        return labels[s]["name"]
    return "全体系"
