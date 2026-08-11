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
    "s4_assets": ["EPISODE", "GLOBAL", "SEGMENTS", "STATES", "KNOWN_ASSETS",
                  "KNOWN_SPACES"],
    "s5_asset_prompts": ["EPISODE", "TONE", "ASSETS", "ASSET_CATALOG"],
    "s6_binding": ["EPISODE", "SEGMENTS", "STATES", "ASSETS"],
    "s7_shots": ["EPISODE", "SEGMENTS", "STATES", "BINDINGS"],
    "s8_compile": ["EPISODE", "SEGMENTS", "STATES", "ASSETS", "BINDINGS", "SHOTS"],
    "_common": [],
}


def required_vars(name: str) -> list:
    """这份模板里不能删的占位符。

    V6.1 的是上面那张手写表（历史原因，占位符和依赖不是一一对应）。
    V3.4 的**从环节图推导**：依赖了哪份产物，模板里就必须用对应的占位符 ——
    再手抄一份表，迟早和依赖表对不上，然后校验就成了摆设。
    """
    if name in REQUIRED_VARS:
        return REQUIRED_VARS[name]
    from . import system_v34 as V34
    for sid, (tpl, deps, _req) in V34.LLM_SPEC.items():
        if tpl == name:
            return [V34.placeholder_of(d) for d in deps]
    return []


def _systems() -> list:
    """两套体系的 (体系名, 环节表, 环节规格)。

    模板注册表要认全两套 —— 只认 V6.1 的话，V3.4 那 15 份模板在设置页里
    看不见也改不了，而模板是这套体系里最需要改的东西。
    打包自检就是这么发现的：模板文件在 exe 里，接口不认。
    """
    from . import system_v34 as V34
    return [("v61", S.STAGES, S._LLM_SPEC), ("v34", V34.STAGES, V34.LLM_SPEC)]


def system_of_template(name: str) -> str:
    """这份模板属于哪套体系。_common 两套共用。"""
    if name == "_common":
        return ""
    for sys_id, _stages, spec in _systems():
        if any(tpl == name for tpl, _, _ in spec.values()):
            return sys_id
    return ""


def _spec(name: str) -> tuple:
    """(环节号, 环节名, 必需输出字段)。_common 不属于任何环节。"""
    for _sys, stages, spec in _systems():
        for sid, (tpl, _deps, req) in spec.items():
            if tpl == name:
                st = next((x for x in stages if x["id"] == sid), {})
                return st.get("no", 0), st.get("name", name), req
    return 0, "所有环节共用的系统提示词", []


def all_template_names() -> list:
    """两套体系的全部模板名，按体系分组保持顺序。"""
    names = []
    for _sys, _stages, spec in _systems():
        for tpl, _, _ in spec.values():
            if tpl not in names:
                names.append(tpl)
    names.append("_common")
    return names


def _paths(name: str, pj=None) -> tuple:
    """(内置, 全局改写, 本剧改写) 三个路径 + 各自存不存在。"""
    b, g, p = S.prompt_files(name, pj)
    return b, g, p


def _layers(name: str, pj=None) -> dict:
    """三层各是什么状态，以及最终生效的是哪一层。"""
    b, g, p = _paths(name, pj)
    has_g, has_p = os.path.isfile(g), bool(p) and os.path.isfile(p)
    eff = "project" if has_p else ("global" if has_g else "builtin")
    return {"builtin_path": b, "global_path": g, "project_path": p,
            "has_global": has_g, "has_project": has_p, "effective": eff}


def catalog(pj=None) -> list:
    """所有模板 + 各自的状态。pj 给了就带上本剧那一层。"""
    out = []
    names = all_template_names()
    for name in names:
        L = _layers(name, pj)
        no, label, req = _spec(name)
        cur = S.load_prompt(name, pj)
        out.append({
            "name": name,
            "system": system_of_template(name),
            "stage_no": no,
            "label": label,
            # customized 指「相对内置有没有被改过」，给列表打标用
            "customized": L["effective"] != "builtin",
            "chars": len(cur),
            "vars": sorted(set(re.findall(r"\{\{(\w+)\}\}", cur))),
            "required_vars": required_vars(name),
            "required_fields": req,
            **L,
        })
    out.sort(key=lambda x: (x["system"] or "zz", x["stage_no"] or 99, x["name"]))
    return out


def read(name: str, pj=None, scope: str = "") -> dict:
    """一份模板。

    scope 决定编辑框里放哪一层的内容：
      global   全局改写（没有就拿内置当起点）
      project  本剧改写（没有就拿「全局或内置」当起点 —— 那才是它现在实际继承的）
      ''       只看，不编辑：给最终生效的那份
    """
    if name not in all_template_names():
        raise ValueError(f"没有这个模板：{name}")
    L = _layers(name, pj)
    no, label, req = _spec(name)
    b = read_text(L["builtin_path"]) if os.path.isfile(L["builtin_path"]) else ""
    g = read_text(L["global_path"]) if L["has_global"] else ""
    p = read_text(L["project_path"]) if L["has_project"] else ""
    inherited = g or b               # 本剧那层没有时，它继承的是这个
    text = {"global": g or b, "project": p or inherited}.get(scope, p or g or b)
    return {"name": name, "system": system_of_template(name),
            "stage_no": no, "label": label, "scope": scope,
            "text": text, "builtin": b, "global_text": g,
            "project_text": p, "inherited": inherited,
            "customized": L["effective"] != "builtin",
            "required_vars": required_vars(name),
            "required_fields": req,
            "vars": sorted(set(re.findall(r"\{\{(\w+)\}\}", text))),
            **L}


def check(name: str, text: str) -> dict:
    """存之前先验一遍。返回 {errors, warnings}。errors 非空就别存。"""
    errors, warnings = [], []
    if not (text or "").strip():
        errors.append("模板是空的")
        return {"errors": errors, "warnings": warnings}

    builtin = S.prompt_files(name)[0]
    b = read_text(builtin) if os.path.isfile(builtin) else ""
    # 环节4业务正文按用户TXT逐字保存；变量与JSON外壳在只读适配层，校验时合起来看。
    adapter = ""
    if name == "s4_assets":
        ap = S.prompt_files("s4_assets_adapter")[0]
        adapter = "\n\n" + (read_text(ap) if os.path.isfile(ap) else "")
    effective, builtin_effective = text + adapter, b + adapter
    have = set(re.findall(r"\{\{(\w+)\}\}", effective))
    orig = set(re.findall(r"\{\{(\w+)\}\}", builtin_effective))

    missing = [v for v in required_vars(name) if v not in have]
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
    lost = [f for f in req if f.split("[")[0].split(".")[0] not in effective]
    if lost:
        errors.append(
            "输出 schema 里找不到必需字段 " + "、".join(lost)
            + " —— 模型不按这个字段输出的话，程序校验不过会反复重试然后失败")

    for block in re.findall(r"```json\s*(.*?)```", effective, re.S):
        probe = re.sub(r"\{\{\w+\}\}", "0", block)
        try:
            json.loads(probe)
        except ValueError as exc:
            warnings.append(f"里面的 JSON 示例解析不了（{exc}）—— "
                            f"示例写歪了模型容易跟着输出错格式")
        break
    return {"errors": errors, "warnings": warnings}


def _target(name: str, pj=None, scope: str = "global") -> str:
    if name not in all_template_names():
        raise ValueError(f"没有这个模板：{name}")
    b, g, p = _paths(name, pj)
    if scope == "project":
        if not p:
            raise ValueError("要改本剧的模板得先打开一个项目")
        return p
    if scope != "global":
        raise ValueError(f"作用域只能是 global 或 project：{scope}")
    return g


def save(name: str, text: str, force: bool = False, pj=None,
         scope: str = "global") -> dict:
    """写改写版。有 errors 且没 force 就不写。"""
    r = check(name, text)
    if r["errors"] and not force:
        return {"ok": False, **r}
    dst = _target(name, pj, scope)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    write_text(dst, text)
    return {"ok": True, **r, "saved_to": dst, **read(name, pj, scope)}


def reset(name: str, pj=None, scope: str = "global") -> dict:
    """删掉这一层的改写，回到它继承的那一层。"""
    dst = _target(name, pj, scope)
    if os.path.isfile(dst):
        os.remove(dst)
    return {"ok": True, "removed": dst, **read(name, pj, scope)}
