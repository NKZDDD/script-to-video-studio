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
    # **这三个以前是空的，那是个洞。**
    #
    # 它们各自承载一整组设置：程序按取值生成一句话，塞进这个位置。
    # 占位符被删掉（或者改写是基于「那时候还写死一句话」的旧版内置做的），
    # 那一组设置生成的句子就**没有位置可去，静静地被丢掉**——
    # 页面上照旧显示着那几个下拉，存了、显示着，就是一个字都进不了提示词。
    #
    # 用户实遇：全局 `_common.md` 改写里第 10 条是写死的
    # 「画面内禁止出现字幕」，第 11/12/13 是手写的「3D漫剧风格」
    # 「无需剧内对话全部改为画外音」「男女主颜值」—— 三个占位符一个都不在。
    # 于是字幕、旁白、媒介三组设置对他的每一个项目都完全无效，
    # 而他手写的第 11、12 条正好在替代后两个 —— 他多半是**发现设置不管用
    # 才手写的**，或者反过来，手写之后就再没发现设置被架空。
    "_common": ["SUBTITLE_RULE", "NARRATION_RULE", "MEDIUM_RULE"],
}

# 占位符 → 它承载的是哪一组设置。用来把「缺了 {{X}}」翻译成人话
# 「**字幕**那一组设置现在一个字都进不了提示词」。
#
# 只说「占位符不见了」没用：读的人不知道 SUBTITLE_RULE 是什么，
# 也就不知道自己刚才在设置页填的东西白填了。
VAR_GROUPS = {
    "SUBTITLE_RULE": "字幕（画面里要不要字幕、字幕语言）",
    "NARRATION_RULE": "旁白 / 画外音（有没有、什么形式、谁的声音、要不要动嘴）",
    "MEDIUM_RULE": "拍成真人还是 3D（视觉媒介与风格）",
    "PARAMS": "生产参数（单段秒数、画幅、图片尺寸）",
    "SCRIPT": "剧本正文",
    # 下面这些是**上一环节的产物**。删了同样不报错，只是那一环节收不到
    # 它该看的东西，然后自己编一份 —— 编出来的和剧情没关系。
    "EPISODE": "本集编号（这一环节只处理哪一集）",
    "GLOBAL": "环节1 的全局设定产物",
    "SEGMENTS": "环节2 划出来的段落表",
    "SEGMENTS_TARGET": "这一集预计切几段",
    "STATES": "环节3 的段落状态时间线",
    "ASSETS": "环节4 的资产表",
    "ASSET_CATALOG": "已有资产清单（跨集复用靠它）",
    "KNOWN_ASSETS": "已登记的资产编号（防止同一个角色出两份定义）",
    "KNOWN_SPACES": "已登记的空间编号",
    "BINDINGS": "环节6 的资产-段落绑定",
    "SHOTS": "环节7 的镜头表",
    "TONE": "这部剧的整体调性",
}


def groups_of(missing: list) -> list:
    """缺了这几个占位符 = 哪几组设置现在不生效。认不出的原样返回。"""
    return [VAR_GROUPS.get(v, v) for v in missing]


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
    return 0, _UNDERSCORE.get(name, ("这一份不属于任何环节", ""))[0], []


# 下划线开头这几份不属于任何环节。**每份得有自己的名字。**
#
# 原来它们共用一个兜底名「所有环节共用的系统提示词」—— 三份长得一模一样。
# 用户按这个名字挑了一份去改「画面内禁止出现任何文字、字幕」，
# 挑到的是 `_settings_extract.md`；而那一行在那份里只是**引用的一个反面
# 例子**（用来教模型认出散文互相矛盾），改它对出图出片没有任何影响。
# 保存成功、标「已改写」，然后画面照旧 —— 用户原话「好像不听我的」。
#
# 第二项是「这一份管什么」，直接显示在编辑框上面。
_UNDERSCORE = {
    "_common": (
        "全局规则（每个环节都带上它）",
        "出图出片的硬规则就在这一份里。**第 10 条「画面里能不能有文字」"
        "是按设置生成的**（{{SUBTITLE_RULE}}）—— 要不要字幕去勾"
        "「设置 → 字幕」，不要在这里写散文：散文和生成出来的那一句会"
        "互相矛盾，而矛盾没有任何一处会报错，结果是其中一方悄悄失效。"
        "剧情本身要求的文字（手机屏幕、招牌、信件、报纸、弹幕这些）"
        "一律允许，不用声明也不用改这里。"),
    "_settings_extract": (
        "把你写的一段话读成设置项（只在「设置」页点识别时用）",
        "**它不参与出图出片。** 只负责把你随手写的一段要求"
        "读成一条条设置项。里面引用的那些规则原文是给它做对照用的"
        "反面例子，改了不会改变画面 —— 想改画面去改 `_common.md`，"
        "或者直接改「设置」页对应的那一项。"),
    "_soften": (
        "提示词被审核拒了之后，怎么改写重试",
        "**只在出图出片被服务商拒了之后才用。** 平时一次都不会被调到，"
        "所以在这里改规则不会影响正常流程。"),
}


def note_of(name: str) -> str:
    """这一份模板管什么 —— 显示在编辑框上面，免得改错那一份。"""
    return _UNDERSCORE.get(name, ("", ""))[1]


def all_template_names() -> list:
    """两套体系的全部模板名，按体系分组保持顺序。"""
    names = []
    for _sys, _stages, spec in _systems():
        for tpl, _, _ in spec.values():
            if tpl not in names:
                names.append(tpl)
    # 下划线开头的是**跨体系的**，不属于任何环节，所以上面那圈扫不到。
    # 不列进来的后果：文件在盘上、页面上改不了 —— 而这几份恰恰是最该能改的
    # （全局规则、抽取提示词、过审改写）。
    #
    # **从盘上扫，不手写清单。** 原来这里是一行写死的 `["_common", ...]`，
    # 配着一句「加新的 _xxx.md 记得往这里加一行」—— 而"记得"是靠不住的：
    # 加了 _soften.md 就漏了，表现是那份模板在页面上根本不存在，不报错。
    names += sorted(n for n in _loose_templates() if n not in names)
    return names


def _loose_templates() -> list:
    """prompts/ 下不属于任何环节的模板（`_开头`）。

    `*_adapter.md` 不算：它是拼在别的模板后面的传输层，不单独用。
    """
    import os
    try:
        files = os.listdir(S.PROMPT_DIR)
    except OSError:
        return []
    return [f[:-3] for f in files
            if f.startswith("_") and f.endswith(".md")
            and not f.endswith("_adapter.md")]


def _paths(name: str, pj=None) -> tuple:
    """(内置, 全局改写, 本剧改写) 三个路径 + 各自存不存在。"""
    b, g, p = S.prompt_files(name, pj)
    return b, g, p


# 改写版是「基于哪一版内置改的」。存改写时记下当时内置那份的指纹，
# 之后内置模板一改，就能认出这份改写已经落后了。
#
# 为什么必须有：改写是**粘性**的 —— 它永远盖住内置。程序升级把内置模板
# 重写了（比如这一轮把五份从逐集改成全剧级、参考图映射从四段改成六字段），
# 带着旧改写的机器上那些改动**一条都不生效，而且一声不吭**。
# 换机器、复用配置时最容易撞上。
_STAMP = "_改写基准.json"


def _stamp_path(pj=None) -> str:
    from . import paths
    return os.path.join(paths.prompts_dir(), _STAMP) if pj is None else \
        os.path.join(S.project_prompt_dir(pj), _STAMP)


def _stamps(pj=None) -> dict:
    from .store import read_json
    return read_json(_stamp_path(pj), {}) or {}


def sha(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _stale(name: str, L: dict, pj=None) -> str:
    """这份改写是不是基于旧版内置改的。返回给人看的一句话，没问题返回空。"""
    if L["effective"] == "builtin":
        return ""
    # 基准记在改写所在的那一层：全局改写记在数据目录，本剧改写记在项目里
    rec = _stamps(pj if L["effective"] == "project" else None).get(name) or {}
    cur = sha(read_text(L["builtin_path"])) if os.path.isfile(L["builtin_path"]) else ""
    base = rec.get("builtin_sha")
    if not base:
        return ("这份改写没记录基于哪一版内置改的（存它的时候还没有这个机制）。"
                "程序升级过的话，内置模板的新改动**不会**生效 —— 对一下再决定留不留。")
    if base != cur:
        return (f"内置模板在你改写之后**又更新过**。你这份是基于旧版改的，"
                f"新版的改动一条都不会生效。对一下差异，或者还原成内置版再改一遍。"
                f"（改写记于 {rec.get('at', '?')}）")
    return ""


# 旧版内置里写死过、现在已经改成占位符的那几句。**认出来是为了替换掉它**，
# 而不是在它旁边再加一条 —— 两条并存就是「散文和生成出来的句子互相矛盾」，
# 正是这一整套占位符要解决的问题。
#
# 只认**判词**，不认整句：用户多半在原句上又改过几个字。
_OLD_HARDCODED = {
    "SUBTITLE_RULE": re.compile(r"画面内(禁止|不(允许|得))出现.*(文字|字幕)"),
    "NARRATION_RULE": re.compile(r"^\s*\d+\.\s*.{0,12}(旁白|画外音)"),
    "MEDIUM_RULE": re.compile(r"^\s*\d+\.\s*.{0,12}(真人|3D|漫剧|二维动画|视觉风格)"),
}

# 编号列表那一行：`10. 内容`
_NUMBERED = re.compile(r"^(\s*)(\d+)\.\s*(.*)$")


def voided(name: str, text: str) -> list:
    """这份模板里缺了哪几个必需占位符 —— 用**设置组的名字**说。

    「缺了 {{SUBTITLE_RULE}}」对读的人没有意义；
    「**字幕**那一组设置现在一个字都进不了提示词」才有意义。

    这一条比 `_stale` 更硬：`_stale` 说的是「内置更新过、新改动不生效」，
    读的人会想「那我少几条新规则，无所谓」。而这里说的是
    **你在设置页填的东西白填了** —— 完全不是一件事。
    """
    have = set(re.findall(r"\{\{(\w+)\}\}", text or ""))
    return [v for v in required_vars(name) if v not in have]


def upgrade(name: str, text: str) -> dict:
    """把缺失的必需占位符补回这份改写里。返回 {text, changes}。

    **只提议，不保存。** 调用方把结果放进编辑框，人看过差异再决定存不存 ——
    这份东西是他手写的，程序不该替他按下保存。

    两步，都刻意保守：

      ① 能认出「这一行就是旧版写死的那句」→ 原地换成占位符（保留行号）。
         不认出来就不动它。
      ② 还缺的 → 在编号列表末尾补一行，编号接着排。

    **不重排编号，不移动任何现有行。** 想「放回原来的位置」就得猜他的编号
    和内置的编号怎么对应，猜错了会把他自己写的规则挪走或覆盖掉。
    补在末尾看着不如原位整齐，但不会动他的东西 —— 而占位符在第几条不影响效果。
    """
    miss = voided(name, text)
    if not miss:
        return {"text": text, "changes": []}

    lines = (text or "").splitlines()
    changes = []

    # ① 原地替换认得出的旧写死行
    still = []
    for v in miss:
        pat = _OLD_HARDCODED.get(v)
        hit = -1
        if pat is not None:
            for i, ln in enumerate(lines):
                if pat.search(ln):
                    hit = i
                    break
        if hit < 0:
            still.append(v)
            continue
        m = _NUMBERED.match(lines[hit])
        keep = f"{m.group(1)}{m.group(2)}. " if m else ""
        changes.append(f"第 {hit + 1} 行原来写死的「{lines[hit].strip()[:40]}」"
                       f"换成了 {{{{{v}}}}}（{VAR_GROUPS.get(v, v)}）")
        lines[hit] = f"{keep}{{{{{v}}}}}"

    # ② 剩下的补在编号列表末尾
    if still:
        last, num = -1, 0
        for i, ln in enumerate(lines):
            m = _NUMBERED.match(ln)
            if m:
                last, num = i, int(m.group(2))
        add = [f"{num + k}. {{{{{v}}}}}" for k, v in enumerate(still, 1)]
        if last < 0:
            lines = lines + [""] + add          # 没有编号列表，接在最后
        else:
            lines = lines[:last + 1] + add + lines[last + 1:]
        for v in still:
            changes.append(f"补了一条 {{{{{v}}}}}（{VAR_GROUPS.get(v, v)}）"
                           f"—— 接在编号列表末尾，没有动你现有的任何一行")

    return {"text": "\n".join(lines), "changes": changes}


def _same(a: str, b: str) -> bool:
    """两个路径指的是不是同一个文件。Windows 上不区分大小写。"""
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _layers(name: str, pj=None) -> dict:
    """三层各是什么状态，以及最终生效的是哪一层。

    源码方式跑（以及配置放在程序目录的老装法）时，数据目录**就是程序目录**，
    于是「全局改写」和「内置」指向同一个文件。不识别这种重合的后果有三个，
    一个比一个糟：
      · 24 份模板全被标成「已改写」，看不出到底改过哪几份
      · 点「保存」是在改**源码里的内置模板**，而不是存一份改写
      · 点「还原」会 os.remove 那个路径 —— **把内置模板删掉**
    打包成 exe 之后两者不同（内置在解压的临时目录里），所以只在开发时踩。
    """
    b, g, p = _paths(name, pj)
    collide = _same(b, g)
    has_g = os.path.isfile(g) and not collide
    has_p = bool(p) and os.path.isfile(p) and not _same(b, p)
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
            "stale": _stale(name, L, pj),
            # 缺了哪几组设置。**比 stale 更要紧**：stale 说的是
            # 「新规则不生效」，这一条说的是「你在设置页填的东西白填了」。
            # catalog() 里这一份文本叫 cur，不叫 text —— 两处的变量名不一样，
            # 一起替换就会在这儿留一个 NameError（只有真跑到才炸）。
            "voided": groups_of(voided(name, cur)),
            "can_upgrade": bool(voided(name, cur)),
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
            # 「这一份管什么」。不说的话，改错那一份是必然的 ——
            # 三份下划线模板长得像，而改错之后一切正常、只是不生效。
            "note": note_of(name),
            "text": text, "builtin": b, "global_text": g,
            "project_text": p, "inherited": inherited,
            "customized": L["effective"] != "builtin",
            "required_vars": required_vars(name),
            "required_fields": req,
            "stale": _stale(name, L, pj),
            # 缺了哪几组设置。**比 stale 更要紧**：stale 说的是
            # 「新规则不生效」，这一条说的是「你在设置页填的东西白填了」。
            "voided": groups_of(voided(name, text)),
            "can_upgrade": bool(voided(name, text)),
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
            + " —— 它们是程序往模板里填数据的口子。删了之后**这几组设置在"
              "页面上照旧显示、存得下、就是一个字都进不了提示词**："
            + "；".join(groups_of(missing)))

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
        dst = p
    elif scope == "global":
        dst = g
    else:
        raise ValueError(f"作用域只能是 global 或 project：{scope}")
    # 改写的目标不能就是内置那份。重合时保存等于改源码、
    # 还原等于把内置模板删掉 —— 后者是不可逆的。
    if _same(b, dst):
        raise ValueError(
            f"改写目标和内置模板是同一个文件（{dst}）。"
            f"这是源码方式跑、且数据目录就是程序目录时才会出现的情况 —— "
            f"保存会直接改掉源码里的内置模板，还原会把它删掉。"
            f"用 --data 指定一个单独的数据目录，或者直接编辑 prompts/ 下那份。")
    return dst


def save(name: str, text: str, force: bool = False, pj=None,
         scope: str = "global") -> dict:
    """写改写版。有 errors 且没 force 就不写。"""
    r = check(name, text)
    if r["errors"] and not force:
        return {"ok": False, **r}
    dst = _target(name, pj, scope)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    write_text(dst, text)
    _remember_base(name, pj, scope)
    return {"ok": True, **r, "saved_to": dst, **read(name, pj, scope)}


def _remember_base(name: str, pj=None, scope: str = "global") -> None:
    """记下这份改写是基于哪一版内置改的。

    不记的话，程序升级重写了内置模板，带着旧改写的机器上那些改动
    一条都不生效，而且一声不吭 —— 换机器、复用配置时最容易撞上。
    """
    import time
    from .store import write_json
    b = _paths(name, pj)[0]
    if not os.path.isfile(b):
        return
    key_pj = None if scope == "global" else pj
    cur = _stamps(key_pj)
    cur[name] = {"builtin_sha": sha(read_text(b)),
                 "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    dst = _stamp_path(key_pj)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    write_json(dst, cur)


def reset(name: str, pj=None, scope: str = "global") -> dict:
    """删掉这一层的改写，回到它继承的那一层。"""
    dst = _target(name, pj, scope)
    if os.path.isfile(dst):
        os.remove(dst)
    return {"ok": True, "removed": dst, **read(name, pj, scope)}
