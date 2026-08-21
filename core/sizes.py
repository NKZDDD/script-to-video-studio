# -*- coding: utf-8 -*-
"""把「我要什么尺寸」翻译成**这一家吃得下**的写法。

用户原话（2026-08-21）：「尺寸应该按照服务商的 api 规范去转换成他能吃的值
确保尺寸正确」。

起因是实跑里 7 张资产图请求 `16:9`、回来的是 `1024x1536`、`1086x1448`、
`1254x1254` —— 三个人物出成了竖图，两个场景出成了方图。

各家的写法根本不是一套：

    派欧 / 灵感鸭 / 小裴 / 章鱼   1024x1536      像素
    零视                          还多一档 2048x2048
    超模 / M86 / 小巴龙            16:9           **比例**
    坤鸡                          1K / 2K / 4K   **档位**，也收像素

所以同一个值换一家就可能不合法。而不合法的后果**不是报错**：多数家会
自己挑一个默认值出图 —— 图出来了、尺寸不是你要的，任务标 ok。
要靠人在几百张里用肉眼发现一张躺倒的图。

这一层只做一件事：**给定「想要什么」和「这一家支持什么」，挑出那个家吃得下、
而且形状最接近的写法。** 挑不出来就照原样发过去并说一句 ——
不自己发明一个值（发明的比例可能连这一家的档位都不在）。
"""

from __future__ import annotations

import re
from math import gcd
from typing import Optional

# `1024x1536`、`1024*1536`、`1024×1536` 都认
_PIXEL = re.compile(r"^\s*(\d{2,5})\s*[x*×]\s*(\d{2,5})\s*$", re.I)
# `16:9`、`16 : 9`
_RATIO = re.compile(r"^\s*(\d{1,3})\s*[:：]\s*(\d{1,3})\s*$")
# `1K`、`2k`、`4K`
_TIER = re.compile(r"^\s*([124])\s*k\s*$", re.I)

# 档位 → 一个代表性的像素尺寸。**只用来算形状和排序**，不当成能发出去的值：
# 「1K」在坤鸡那边是一整档，不是某一个具体分辨率。
_TIER_PIXELS = {"1K": (1024, 1024), "2K": (2048, 2048), "4K": (4096, 4096)}


def parse(v) -> Optional[tuple]:
    """`"1024x1536"` → `("pixel", 1024, 1536)`；`"16:9"` → `("ratio", 16, 9)`；
    `"2K"` → `("tier", 2048, 2048)`。认不出返回 None。"""
    s = str(v or "").strip()
    m = _PIXEL.match(s)
    if m:
        return ("pixel", int(m.group(1)), int(m.group(2)))
    m = _RATIO.match(s)
    if m and int(m.group(2)):
        return ("ratio", int(m.group(1)), int(m.group(2)))
    m = _TIER.match(s)
    if m:
        w, h = _TIER_PIXELS[m.group(1).upper() + "K"]
        return ("tier", w, h)
    return None


def aspect(v) -> Optional[float]:
    """宽高比。认不出返回 None。"""
    p = parse(v)
    if not p or not p[2]:
        return None
    return p[1] / p[2]


def as_ratio(v) -> str:
    """`"1024x1536"` → `"2:3"`。约到最简，认不出返回空串。

    不四舍五入到「常见比例」——`1086x1448` 约出来是 3:4，那就是 3:4；
    硬凑成 9:16 会让日志说的和实际发出去的不是一回事。
    """
    p = parse(v)
    if not p:
        return ""
    w, h = p[1], p[2]
    g = gcd(w, h) or 1
    return f"{w // g}:{h // g}"


def _kind_of(vals: list) -> str:
    """这一家的清单是哪一种写法为主。混着写（坤鸡）时按第一个算。"""
    for v in vals:
        p = parse(v)
        if p:
            return p[0]
    return ""


def resolve(want, supported: list) -> tuple:
    """返回 `(要发出去的值, 说明)`。

    值为 `None` = **换不过来，该停下来让人改配置**（说明里写了怎么改）。
    说明为空串 = 原样发，什么都没换。

    挑选顺序，每一步都比下一步更保守：

      ① 清单里有一模一样的 → 就用它（哪怕形状不理想，那是用户自己选的）
      ② 形状完全相同的（`1024x1536` ↔ `2:3`）→ 换写法，形状不变
      ③ 形状最接近的 → 换，并且**说清换了什么**
      ④ 清单是空的 / 想要的认不出 → 原样发，说一句

    **③ 一定要出声。** 悄悄换形状和悄悄不换一样糟：人看到的是一张
    形状不对的图，而日志里什么都没有。
    """
    s = str(want or "").strip()
    vals = [str(x).strip() for x in (supported or []) if str(x).strip()]
    if not vals:
        return s, ""                     # 这一家没声明支持什么，别自作聪明
    if s in vals:
        return s, ""

    p = parse(s)
    # **档位（1K/2K/4K）说的是分辨率，不是形状。** 按形状去匹配的话
    # `2K` 会被算成 1:1（因为代表像素是 2048x2048），于是所有图变成方的 ——
    # 而那不是任何人的意思。这一家不收档位时，只能按分辨率挑，形状说不了。
    if p and p[0] == "tier" and _kind_of(vals) == "ratio":
        # 这一家只收比例，而档位说的是分辨率 —— **两者表达的不是一回事**，
        # 换不过来。按形状硬匹配的话 `2K`（代表像素 2048x2048）会算成 1:1，
        # 或者在只有比例的清单里挑出 21:9（因为「面积」最大）——
        # 两个结果都不是任何人的意思。
        #
        # 这是配置填错了，而**猜一个的后果是几百张图形状不对却不报错**。
        # 所以这里停，让人去改那一栏。
        return None, (f"出图尺寸填的是「{s}」（分辨率档位），而这一家只收比例"
                      f"（{'、'.join(vals[:6])}）—— 档位表达不了形状，换不过来。\n"
                      f"去「一键跑到底」面板的「出图尺寸」改成比例（比如 9:16）"
                      f"或者具体像素（比如 1024x1536），或者把这一类活换一家"
                      f"收档位的（坤鸡）。")
    if p and p[0] == "tier":
        best = _pick_biggest([v for v in vals if parse(v) and parse(v)[0] != "ratio"]
                             or vals)
        return best, (f"「{s}」是分辨率档位，这一家不收这个写法，"
                      f"按分辨率挑了 {best}（{as_ratio(best) or '形状未知'}）。"
                      f"要指定形状就把出图尺寸填成比例或具体像素。")

    a = aspect(s)
    if a is None:
        # 原样发过去的话，多数家会自己挑一个默认值出图 ——
        # 图出来了、尺寸不是你要的、任务标 ok。那是最坏的一类。
        return None, (f"出图尺寸填的是「{s}」，看不懂 —— 既不是 1024x1536 这样的"
                      f"像素，也不是 16:9 这样的比例。\n"
                      f"原样发过去的话，多数服务商会自己挑一个默认值出图："
                      f"图出来了、尺寸不是你要的、而且不报错。所以这里停。\n"
                      f"这一家支持：{'、'.join(vals[:6])}")

    # ② 形状完全一样的（比较约简后的比例，避开浮点）
    same = [v for v in vals if as_ratio(v) and as_ratio(v) == as_ratio(s)]
    if same:
        pick = _pick_biggest(same)
        return pick, (f"你要的 {s} 这一家不收，换成同形状的 {pick}"
                      f"（{as_ratio(pick)}，形状一模一样）")

    # ③ 形状最接近的
    scored = [(abs((aspect(v) or 0) - a), v) for v in vals if aspect(v)]
    if not scored:
        return s, (f"这一家声明的尺寸都看不懂（{'、'.join(vals[:6])}），"
                   f"{s} 原样发过去了")
    scored.sort(key=lambda x: (x[0], -_area(x[1])))
    pick = scored[0][1]
    return pick, (f"你要的 {s}（{as_ratio(s)}）这一家不收，也没有同形状的，"
                  f"换成最接近的 {pick}（{as_ratio(pick)}）—— "
                  f"**形状变了**，画面的构图会跟着变。"
                  f"这一家支持：{'、'.join(vals[:6])}")


def _area(v) -> int:
    p = parse(v)
    return (p[1] * p[2]) if p else 0


def _pick_biggest(vals: list) -> str:
    """同形状里挑分辨率最高的。比例写法（没有真实像素）排在像素之后 ——
    比例是「让服务商自己定分辨率」，能指定就指定。"""
    def key(v):
        p = parse(v)
        return (0 if (p and p[0] == "ratio") else 1, _area(v))
    return sorted(vals, key=key)[-1]
