# -*- coding: utf-8 -*-
"""提示词模板的查看与改写。

每个 LLM 环节都有一份模板。改它是正常需求（换风格、加约束、适配新模型），
但改坏了不会立刻报错 —— 会一路跑下去，几百次调用之后才发现产出不对。
所以这里做两件事：

  1. 改写存到**数据目录**，不动内置那份。随时能一键还原，程序更新也不会冲掉。
  2. 保存前校验：占位符删了没、必需字段还在不在、JSON 示例还能不能解析。
     校验分「拦住」和「提醒」两级 —— 删掉 {{SCRIPT}} 是必拦的（模型根本
     收不到剧本），而加一句自定义要求只是提醒。
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from . import paths, stages as S
from .store import read_text, write_text

# 每个模板里**不能删**的占位符：删了就等于不给模型这份输入。
# 值取自 stages.run_llm_stage 实际填的那张 mapping。
REQUIRED_VARS = {
    "s1_global": ["PARAMS", "SCRIPT"],
    # 注：镜头数 5-8、关键帧 4-6 这些区间已经写死在模板正文里，不再是占位符。
    # 它们是给模型判断用的创作区间，不该由配置去「控制」。
    "s2_segments": ["EPISODE", "GLOBAL", "SCRIPT", "SEGMENTS_TARGET"],
    "s3_states": ["EPISODE", "SEGMENTS"],
    "s4_assets": ["EPISODE", "GLOBAL", "SEGMENTS", "STATES", "KNOWN_ASSETS"],
    "s5_asset_prompts": ["EPISODE", "TONE", "ASSETS"],
    "s6_binding": ["EPISODE", "SEGMENTS", "STATES", "ASSETS"],
    "s7_shots": ["EPISODE", "SEGMENTS", "STATES", "BINDINGS"],
    "s8_compile": ["EPISODE", "SEGMENTS", "STATES", "ASSETS", "BINDINGS", "SHOTS"],
    "_common": [],
}


def _spec(name: str) -> tuple:
    """(环节号, 环节名, 必需输出字段)。_common 不属于任何环节。"""
    for sid, (tpl, _deps, req) in S._LLM_SPEC.items():
        if tpl == name:
            st = next(x for x in S.STAGES if x["id"] == sid)
            return st["no"], st["name"], req
    return 0, "所有环节共用的系统提示词", []


def catalog() -> list:
    """所有模板 + 各自的状态，给设置页列表用。"""
    out = []
    names = [tpl for tpl, _, _ in S._LLM_SPEC.values()] + ["_common"]
    for name in names:
        builtin, custom = S.prompt_files(name)
        no, label, req = _spec(name)
        cur = read_text(custom) if os.path.isfile(custom) else (
            read_text(builtin) if os.path.isfile(builtin) else "")
        out.append({
            "name": name,
            "stage_no": no,
            "label": label,
            "customized": os.path.isfile(custom),
            "builtin_path": builtin,
            "custom_path": custom,
            "chars": len(cur),
            "vars": sorted(set(re.findall(r"\{\{(\w+)\}\}", cur))),
            "required_vars": REQUIRED_VARS.get(name, []),
            "required_fields": req,
        })
    out.sort(key=lambda x: (x["stage_no"] or 99, x["name"]))
    return out


def read(name: str) -> dict:
    """一份模板的当前内容 + 内置原文（供对比和还原）。"""
    if name not in REQUIRED_VARS:
        raise ValueError(f"没有这个模板：{name}")
    builtin, custom = S.prompt_files(name)
    no, label, req = _spec(name)
    b = read_text(builtin) if os.path.isfile(builtin) else ""
    c = read_text(custom) if os.path.isfile(custom) else ""
    return {"name": name, "stage_no": no, "label": label,
            "customized": bool(c), "text": c or b, "builtin": b,
            "builtin_path": builtin, "custom_path": custom,
            "required_vars": REQUIRED_VARS.get(name, []),
            "required_fields": req,
            "vars": sorted(set(re.findall(r"\{\{(\w+)\}\}", c or b)))}


def check(name: str, text: str) -> dict:
    """存之前先验一遍。返回 {errors, warnings}。errors 非空就别存。"""
    errors, warnings = [], []
    if not (text or "").strip():
        errors.append("模板是空的")
        return {"errors": errors, "warnings": warnings}

    have = set(re.findall(r"\{\{(\w+)\}\}", text))
    builtin, _ = S.prompt_files(name)
    b = read_text(builtin) if os.path.isfile(builtin) else ""
    orig = set(re.findall(r"\{\{(\w+)\}\}", b))

    missing = [v for v in REQUIRED_VARS.get(name, []) if v not in have]
    if missing:
        errors.append(
            "少了必需的占位符 " + "、".join(f"{{{{{v}}}}}" for v in missing)
            + " —— 这些是程序往模板里填数据的口子，删了模型就收不到对应的输入")

    dropped = sorted(orig - have - set(missing))
    if dropped:
        warnings.append("内置模板里有、你这份没有的占位符："
                        + "、".join(f"{{{{{v}}}}}" for v in dropped)
                        + "（不是必需的，确认是有意去掉的就行）")
    unknown = sorted(have - orig)
    if unknown:
        warnings.append("这些占位符内置模板里没有："
                        + "、".join(f"{{{{{v}}}}}" for v in unknown)
                        + " —— 程序不会填它们，会原样发给模型")

    _, _, req = _spec(name)
    lost = [f for f in req if f.split("[")[0].split(".")[0] not in text]
    if lost:
        errors.append(
            "输出 schema 里找不到必需字段 " + "、".join(lost)
            + " —— 模型不按这个字段输出的话，程序校验不过会反复重试然后失败")

    for block in re.findall(r"```json\s*(.*?)```", text, re.S):
        probe = re.sub(r"\{\{\w+\}\}", "0", block)
        try:
            json.loads(probe)
        except ValueError as exc:
            warnings.append(f"里面的 JSON 示例解析不了（{exc}）—— "
                            f"示例写歪了模型容易跟着输出错格式")
        break
    return {"errors": errors, "warnings": warnings}


def save(name: str, text: str, force: bool = False) -> dict:
    """写改写版。有 errors 且没 force 就不写。"""
    if name not in REQUIRED_VARS:
        raise ValueError(f"没有这个模板：{name}")
    r = check(name, text)
    if r["errors"] and not force:
        return {"ok": False, **r}
    _, custom = S.prompt_files(name)
    os.makedirs(os.path.dirname(custom), exist_ok=True)
    write_text(custom, text)
    return {"ok": True, **r, "custom_path": custom, **read(name)}


def reset(name: str) -> dict:
    """删掉改写版，回到内置模板。"""
    if name not in REQUIRED_VARS:
        raise ValueError(f"没有这个模板：{name}")
    _, custom = S.prompt_files(name)
    if os.path.isfile(custom):
        os.remove(custom)
    return {"ok": True, **read(name)}
