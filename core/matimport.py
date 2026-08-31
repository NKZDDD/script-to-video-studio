# -*- coding: utf-8 -*-
"""把一份「全剧最终生产材料」md 拆开、验收、导入成 tasks.json。

用户原话（2026-08-24）：「所以我需要一个 md 内容分割和验收导入器就直接查一个
固定项目路径或是点击导入生产材料即可，参考图小于等于目标模型上限再导入后
需要有提醒」。

## 这条路为什么成立

材料是 codex 一次想完整部剧产出的，形状是 234 条【生产序号】，每条一个
可直接投喂的生产任务。实测那份《烟火尽头》21 集：

    234 条 = 150 个 png + 84 个 mp4（21 集 × 4 段）
    被引用的 150 个 ID **全部**在同一份文件里有产出 —— 引用链闭合
    每条都有 建议保存文件名 / 完整 production_prompt / Image N 映射

出图出片那一层要的四样它全给齐了（落哪、发什么、传哪几张、什么尺寸），
而且 `Image N＝<ID>｜是谁｜控制｜不控制｜适用范围` 正好是出图前那两道检查
要的形状 —— 所以这条路不缺任何东西，**27 个 LLM 环节可以整个跳过**。

## 会失去什么（要说清，不能假装没有）

那几道靠中间 JSON 字段的闸门查不了了：时间线跳秒、装箱漏镜头、判定打架、
集数对账 —— 它们查的是 `n9_shots.timing_plan`、`n10_segs.included_shots`
这些字段，而材料里只有散文。

失去的不是「安全」，是**一种特定的安全**：那几道闸门抓的是「程序一步步问
模型」时「上一环节和下一环节各自都对、凑起来做不出来」。一次性想完整部剧
换成了另一类错，而那一类没法从散文里查。

所以这里做**能做的那几样**，一样都不少：引用链闭合、编号连续、张数不超过
目标模型的上限、每集的段号连续。
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

# ---------------------------------------------------------------- 分割

# 「## 【生产序号 001】」。序号位数不固定，中英文括号都认。
_UNIT = re.compile(r"^#{1,3}\s*[【\[]\s*生产序号\s*(\d+)\s*[】\]]", re.M)

# 「建议保存文件名：PRJ_..._R02.png」
_FILENAME = re.compile(r"建议保存文件名\s*[：:]\s*(\S+)")
_GOAL = re.compile(r"生产目标\s*[：:]\s*(.+)")
_CANON = re.compile(r"完整\s*Canonical\s*Revision\s*ID\s*[：:]\s*(\S+)", re.I)

# 「Image 1＝PRJ_xxx｜…」。**全角等号和竖线都要认** —— 实际文件里用的就是它们，
# 只认半角的话一条都解析不出来，而那会表现为「导入了 234 条、每条 0 张参考图」。
_REF = re.compile(r"Image\s*(\d+)\s*[=＝:：]\s*([A-Za-z0-9_\-]+)")

_NO_REF = "无需上传参考图"

# 分节标题。正文里也会出现 `Image N`（提示词自己复述一遍），所以**只在
# 「需要上传的参考图」那一节里找映射** —— 不分节的话同一条会数出两倍张数。
_SEC_REFS = re.compile(r"[【\[]\s*需要上传的参考图\s*[】\]]")
_SEC_PROMPT = re.compile(r"[【\[]\s*完整可复制\s*production_prompt\s*[】\]]")
_SEC_AFTER = re.compile(r"[【\[]\s*完成后返回\s*[】\]]")

# 尺寸：正文里写的「9:16竖幅」「一次生成完整15秒9:16」
_RATIO = re.compile(r"(\d{1,2}\s*[:：]\s*\d{1,2})")
_SECONDS = re.compile(r"(\d{1,3})\s*秒")

# 文件名里的集号段号：`..._VIDEO_EP01_SEG01_R01.mp4`
_EPSEG = re.compile(r"_?(EP\d{2,3})[_-](SEG\d{1,3})", re.I)

# 提示词正文里的身份映射。**和 produce._IMAGE_MAP 保持同一个写法** ——
# 两份正则迟早对不上，而对不上的表现是「导入说没问题、出片才拦下」。
_IMAGE_MAP = re.compile(r"[Ii]mage\s*(\d+)\s*[=＝:：]\s*([A-Za-z0-9_\-]+)")


def _section(body: str, start, end) -> str:
    """取某一节的正文。取不到返回空串。"""
    m = start.search(body)
    if not m:
        return ""
    tail = body[m.end():]
    stop = end.search(tail) if end else None
    return tail[:stop.start()] if stop else tail


def _clean(s: str) -> str:
    """去掉分节末尾的水平分隔线和空白。

    `---` 是条目之间的分隔线，不是提示词内容。不去掉的话，一条**没写提示词**
    的单元会解析出 `prompt == "---"` —— 非空，于是「缺提示词」检查不出来，
    而出图时发出去的提示词就是三个横线。
    """
    lines = [ln for ln in (s or "").splitlines()]
    while lines and not lines[-1].strip().strip("-*_ "):
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def split_units(text: str) -> list:
    """把材料拆成一条条生产单元。**不做任何清洗** —— 原文照抄。"""
    marks = list(_UNIT.finditer(text or ""))
    out = []
    for i, m in enumerate(marks):
        body = text[m.end():(marks[i + 1].start() if i + 1 < len(marks)
                             else len(text))]
        out.append({"no": int(m.group(1)), "body": body})
    return out


def parse_unit(u: dict) -> dict:
    """一条生产单元 → 结构化。缺什么在 `missing` 里说，不猜、不补。"""
    body = u["body"]
    fn = _FILENAME.search(body)
    goal = _GOAL.search(body)
    canon = _CANON.search(body)
    refs_sec = _section(body, _SEC_REFS, _SEC_PROMPT)
    prompt = _clean(_section(body, _SEC_PROMPT, _SEC_AFTER))
    # 分节之后再找映射 —— 正文里的复述不算（不分节会数出两倍）
    refs = [(int(n), a) for n, a in _REF.findall(refs_sec)]
    filename = fn.group(1) if fn else ""
    kind = ("video" if filename.lower().endswith((".mp4", ".mov"))
            else "image" if filename else "")
    missing = []
    if not filename:
        missing.append("建议保存文件名")
    if not prompt:
        missing.append("完整可复制production_prompt")
    if not refs and _NO_REF not in refs_sec:
        missing.append("参考图那一节既没有 Image N 也没写「无需上传参考图」")
    ep = seg = ""
    m = _EPSEG.search(filename)
    if m:
        ep, seg = m.group(1).upper(), m.group(2).upper()
    return {
        "no": u["no"], "filename": filename, "stem": filename.rsplit(".", 1)[0],
        "kind": kind, "goal": (goal.group(1).strip() if goal else ""),
        "canonical_id": (canon.group(1) if canon else ""),
        "refs": refs, "prompt": prompt, "missing": missing,
        "episode": ep, "seg": seg,
        "ratio": (_RATIO.search(prompt).group(1).replace("：", ":").replace(" ", "")
                  if _RATIO.search(prompt) else ""),
        "seconds": (int(_SECONDS.search(prompt).group(1))
                    if kind == "video" and _SECONDS.search(prompt) else 0),
    }


MANIFEST = "manifest"


def manifest_of(units: list) -> dict:
    """材料自己的申报（共几条、图片几条、几集、每集几段），没有就返回 {}。

    这一条是**唯一**能查出「少产了」的东西。没有申报时，84 条只给 80 条 ——
    剩下 80 条引用链闭合、编号连续、段号连续，全绿，一句话说不出来。
    查「你有没有兑现你自己说的」比查「你符不符合我猜的」准，也不会误报。
    """
    for u in units:
        if u.get("kind") == MANIFEST:
            return u.get("declared") or {}
    return {}


def units_of(units: list) -> list:
    """去掉申报头，剩下真正要生产的。

    申报头**绝不能变成任务** —— 它没有提示词也没有产物，
    混进去就是一条永远做不完的活。
    """
    return [u for u in units if u.get("kind") != MANIFEST]


def parse(text: str) -> list:
    """自动认格式：JSONL（契约）优先，md 散文兜底。

    **契约格式是主路，md 是兼容**。用户原话：「不应该是我去解析 codex 生产的
    结果，应该是我告诉他我要什么样的结构」—— 对。散文那条路要猜排版
    （全角等号、分节、`---`），每一处猜错都是静默的；JSONL 没有这些歧义。
    """
    s = (text or "").strip()
    if _looks_jsonl(s):
        return parse_jsonl(text)
    return [dict(parse_unit(u), src="md") for u in split_units(text)]


def _looks_jsonl(s: str) -> bool:
    """这是契约格式还是 md 散文。

    **不能只看第一个字符** —— 材料前面常有一行说明或注释（`# 生产材料 V2`），
    那时首字符不是 `{`，于是整份被当成 md 交给散文解析器，
    而散文解析器找不到【生产序号】，返回 **0 条** —— 「导入了 0 条」，
    不报错。

    判据：没有【生产序号】标记，而且**至少有一行能解析成 JSON 对象**。
    """
    if s.startswith("["):
        return True
    if _UNIT.search(s):
        return False            # 有生产序号标记 → 散文那条路
    for ln in s.splitlines():
        ln = ln.strip().rstrip(",")
        if not ln or ln.startswith(("#", "//")):
            continue
        if ln.startswith("{"):
            try:
                return isinstance(json.loads(ln), dict)
            except Exception:                               # noqa: BLE001
                return True     # 像 JSON 但坏了 —— 交给 jsonl 那条路去报行号
        return False
    return False


def parse_jsonl(text: str) -> list:
    """一行一条 JSON（或一个 JSON 数组）→ 和 md 那条路一样的形状。

    坏行**不跳过**：记进 `missing`，让验收把行号报出来。跳过的话
    「产了 234 条、导进来 230 条」不会有人发现。
    """
    rows, bad = [], []
    s = (text or "").strip()
    if s.startswith("["):
        try:
            rows = [r for r in json.loads(s) if isinstance(r, dict)]
        except Exception as exc:                            # noqa: BLE001
            bad.append((1, f"整个数组解析不了：{exc}"))
    else:
        for i, ln in enumerate(s.splitlines(), 1):
            ln = ln.strip().rstrip(",")
            if not ln or ln.startswith(("#", "//")):
                continue
            try:
                r = json.loads(ln)
                (rows if isinstance(r, dict) else bad).append(
                    r if isinstance(r, dict) else (i, "这一行不是一个对象"))
            except Exception as exc:                        # noqa: BLE001
                bad.append((i, f"解析不了：{str(exc)[:80]}"))
    out = [_from_json(i, r) for i, r in enumerate(rows, 1)]
    for i, why in bad:
        out.append({"no": i, "filename": "", "stem": "", "kind": "", "goal": "",
                    "canonical_id": "", "refs": [], "prompt": "",
                    "missing": [f"第 {i} 行 {why}"], "episode": "", "seg": "",
                    "ratio": "", "seconds": 0, "src": "jsonl", "spine": []})
    return out


def _blank(no: int, kind: str = "", **kw) -> dict:
    """一条的空壳。字段齐全，下游取哪个都不会 KeyError。"""
    d = {"no": no, "filename": "", "stem": "", "kind": kind, "goal": "",
         "canonical_id": "", "refs": [], "prompt": "", "missing": [],
         "episode": "", "seg": "", "ratio": "", "seconds": 0,
         "src": "jsonl", "spine": []}
    d.update(kw)
    return d


def _from_json(no: int, r: dict) -> dict:
    """契约格式的一条 → 内部形状。**缺什么记下来，不猜不补。**"""
    fn = str(r.get("filename") or "").strip()
    kind = str(r.get("kind") or "").strip().lower()
    if kind == MANIFEST:
        return _blank(no, MANIFEST, declared={
            "total": int(r.get("total") or 0),
            "image": int(r.get("image") or 0),
            "video": int(r.get("video") or 0),
            "episodes": int(r.get("episodes") or 0),
            "segs_per_episode": r.get("segs_per_episode")
            if isinstance(r.get("segs_per_episode"), dict)
            else int(r.get("segs_per_episode") or 0),
            # 项目参数：**材料给程序的，不是程序给材料的限制。**
            # 用户原话（2026-08-25）：「项目参数也写进契约但是是他给你的
            # 不能做任何的限制」。所以这里只收下来 —— 不夹范围、不改值、
            # 不因为和页面上填的不一样就拦。夹一下的代价很具体：
            # 它按剧情定的 20 秒被夹成 15 秒，而提示词还是按 20 秒写的。
            "params": r.get("params") if isinstance(r.get("params"), dict)
            else {},
        })
    if kind and kind not in ("image", "video"):
        # **认不出来的 kind 不许当图片。** 猜成图片的话，一条视频会被
        # 当资产图去出图 —— 出得来、任务标成功，成片里少一段而没人报错。
        return _blank(no, "", filename=fn, stem=str(r.get("key") or ""),
                      canonical_id=str(r.get("key") or ""),
                      missing=[f"kind 认不出（给的是「{kind}」，"
                               f"只认 image / video / manifest）"])
    if not kind:
        kind = ("video" if fn.lower().endswith((".mp4", ".mov"))
                else "image" if fn else "")
    # 骨架排前面、补图接着排 —— 顺序就是上传顺序，所以这里按 image_n 排一次
    def _rows(key):
        got = []
        for x in (r.get(key) or []):
            if isinstance(x, dict):
                aid = str(x.get("key") or x.get("asset_id") or "").strip()
                if aid:
                    got.append((int(x.get("image_n") or 0), aid))
            elif str(x).strip():
                got.append((0, str(x).strip()))
        return got
    refs = _rows("storyboard_refs") + _rows("reference_images")
    # 没给编号的按出现顺序补 —— 给了的照它的
    seq, n = [], 0
    for num, aid in refs:
        n = num if num else n + 1
        seq.append((n, aid))
    missing = []
    if not fn:
        missing.append("filename")
    if not str(r.get("prompt") or "").strip():
        missing.append("prompt")
    if not str(r.get("key") or "").strip():
        missing.append("key")
    ep = str(r.get("episode") or "").upper()
    seg = str(r.get("seg") or "").upper()
    # **集号对图也要解出来，不只是视频。**
    #
    # 契约里只有 video 行要求写 episode / seg；故事板那种 image 行的集号
    # 藏在 key 里（`..._SBSHEET_EP01_SEG01_A_R01`）。原来这段只在
    # kind == "video" 时才解，于是所有图的 episode 都是空串 ——
    # 而按集过滤的两个入口对空串的处理**是相反的**：
    #   · 生产页 /api/generate 用 `t.get("episode","") == ep` → 空串永远
    #     不等于 EP01 → 选了集就一条都不剩，点「开始」什么都不做
    #   · 「只跑生产」的 _produce_todo 用 `not t.get("episode") or ...`
    #     → 空串一律留下 → 选了 EP01 却把全部集的图都出了，钱按全剧花
    # 一个漏做一个多做，两个都不报错。所以在源头解出来，别在两个下游各补一次。
    #
    # 资产（CHAR_001 这种）本来就不属于任何一集，key 里没有 EPnn，
    # 解不出来就是空 —— 那是对的，资产全剧共享，不参与按集过滤。
    if not (ep and seg):
        m = _EPSEG.search(str(r.get("key") or "") + " " + fn)
        if m:
            ep, seg = ep or m.group(1).upper(), seg or m.group(2).upper()
        elif kind == "video":
            # 视频缺集号是硬伤：分集和拼接顺序全靠它。图缺了只是不参与按集。
            missing.append("episode / seg（视频要它来分集和排序，拼接靠这个）")
    spine = [a for _n, a in _rows("storyboard_refs")]
    if kind == "video" and not spine:
        missing.append("storyboard_refs（本段的有序故事板骨架 —— "
                       "视频那一层读这个字段，缺了报「缺故事板」直接不出片）")
    return {
        "no": no, "filename": fn,
        "stem": str(r.get("key") or fn.rsplit(".", 1)[0]),
        "kind": kind, "goal": str(r.get("name") or r.get("goal") or ""),
        "canonical_id": str(r.get("key") or ""),
        "refs": seq, "prompt": str(r.get("prompt") or "").strip(),
        "missing": missing, "episode": ep, "seg": seg,
        "ratio": str(r.get("ratio") or r.get("size") or ""),
        "seconds": int(r.get("duration") or 0),
        "spine": spine, "src": "jsonl",
    }


# ---------------------------------------------------------------- 验收

def audit(units: list, limits: Optional[dict] = None) -> list:
    """能查的都查。返回 `[{level, code, msg}]`，`level` 是 error / warn。

    `limits` = `{"image": N, "video": N}` —— 目标模型一次能吃几张参考图。
    没给就不查那一条（不知道上限时判「超了」是在猜）。

    **每一条都对应一种「导进去之后才发现」**：
      · 引用链不闭合  → 出图时报「参考图不存在」，而那张图压根没人做
      · 编号不连续    → 出图前那道检查直接拦，整条不出
      · 张数超上限    → 服务商截掉多的（截掉的正是排在后面的），
                        画面用错参考而**不报错** —— 这一条用户点名要提醒
      · 段号缺号      → 成片短一截，而拼接不会说话
    """
    out = []
    decl = manifest_of(units)
    units = units_of(units)
    made = {u["stem"] for u in units if u["stem"]}
    limits = limits or {}

    # ---- 一、材料是不是照契约产的（用户原话：「同一个 key 出现两次的本质是
    #      codex 没有正常按照你的需要给出」）。这一类**不自动兜**：
    #      不去重、不补号、不挑一条留下 —— 报出来让它重产那几条。
    seen: dict = {}
    for u in units:
        if u["stem"]:
            seen.setdefault(u["stem"], []).append(u["no"])
    for key, nos in sorted(seen.items()):
        if len(nos) > 1:
            out.append({"level": "error", "code": "KEY_DUPLICATE",
                        "msg": f"key「{key}」出现了 {len(nos)} 次（第 "
                               + "、".join(f"{n:03d}" for n in nos)
                               + " 条）—— 提示词按 key 落盘，"
                                 "**后一条会盖掉前一条**，"
                                 "被盖掉那条的提示词永远丢了，"
                                 "而两条任务都还在，其中一条读的是别人的提示词。"
                                 "这几条要让它重产成各自唯一的 key"})
    fns: dict = {}
    for u in units:
        if u["filename"]:
            fns.setdefault(u["filename"].lower(), []).append(u["no"])
    for fn, nos in sorted(fns.items()):
        if len(nos) > 1:
            out.append({"level": "error", "code": "FILENAME_DUPLICATE",
                        "msg": f"文件名「{fn}」被 {len(nos)} 条共用（第 "
                               + "、".join(f"{n:03d}" for n in nos)
                               + " 条）—— 产物落同一个路径，"
                                 "**后出的盖掉先出的**，"
                                 "而「做过没有」看的是产物在不在，"
                                 "于是两条都算做完了，实际只剩一张"})

    # ---- 二、有没有少产。**没有申报就查不出来**，所以申报缺失也要说话。
    out += _reconcile(units, decl)

    for u in units:
        who = f"第 {u['no']:03d} 条（{u['filename'] or '没有文件名'}）"
        for m in u["missing"]:
            out.append({"level": "error", "code": "UNIT_INCOMPLETE",
                        "msg": f"{who} 缺「{m}」"})
        nums = [n for n, _ in u["refs"]]
        if nums and nums != list(range(1, len(nums) + 1)):
            out.append({"level": "error", "code": "REF_NUMBER_GAP",
                        "msg": f"{who} 参考图编号不连续：{nums} —— "
                               f"出图前那道检查会直接拦住，整条不出"})
        for _n, aid in u["refs"]:
            if aid not in made:
                out.append({"level": "error", "code": "REF_NOT_PRODUCED",
                            "msg": f"{who} 引用了 {aid}，而这份材料里"
                                   f"**没有任何一条产出它** —— "
                                   f"出图时会报「参考图不存在」"})
        cap = limits.get(u["kind"])
        if cap and len(u["refs"]) > cap:
            out.append({"level": "error", "code": "REF_OVER_LIMIT",
                        "msg": f"{who} 要传 {len(u['refs'])} 张参考图，"
                               f"而目标模型一次只吃 {cap} 张。"
                               f"**服务商会截掉多的，而截掉的正是排在后面的那几张** —— "
                               f"画面用错参考，任务照样标成功。"
                               f"要么减到 {cap} 张以内，要么换一家吃得下的。"})

    # 提示词的编号 vs 实际上传顺序
    for u in units:
        for why in _map_vs_order(u):
            out.append({"level": "error", "code": "REF_ORDER_MISMATCH",
                        "msg": f"第 {u['no']:03d} 条（{u['filename']}）{why} —— "
                               f"**模型按正文的说明去用图**，编号错位之后每条"
                               f"描述都套到别的图上，画面出得来而参考全是错的"})

    # 骨架只有一张：V6.2 第 19 章要求覆盖完整关键时间推进。
    # **只提醒不拦** —— 已经产出来的材料是一段一张（84 张故事板 : 84 段视频），
    # 判 error 就是把它们全变成死路；而死路比漏检更糟。
    for u in units:
        if u["kind"] == "video" and len(u.get("spine") or []) == 1:
            out.append({"level": "warn", "code": "VIDEO_SPINE_SINGLE",
                        "msg": f"第 {u['no']:03d} 条（{u['filename']}）"
                               f"只有 1 张故事板骨架 —— 一张只能说明某个瞬间，"
                               f"模型不知道这一段先发生什么后发生什么，"
                               f"**会把后段的画面当前段用而不报错**，"
                               f"出来的片子时间顺序是乱的。"
                               f"要覆盖完整关键时间推进的全部有序 Sheet"})

    # 段号连续：缺一段 = 成片短一截，而拼接不会说话
    byep: dict = {}
    for u in units:
        if u["kind"] == "video" and u["episode"] and u["seg"]:
            byep.setdefault(u["episode"], set()).add(int(u["seg"][3:]))
    for ep, segs in sorted(byep.items()):
        want = set(range(1, max(segs) + 1))
        lost = sorted(want - segs)
        if lost:
            out.append({"level": "error", "code": "SEG_GAP",
                        "msg": f"{ep} 的段号不连续，缺 "
                               + "、".join(f"SEG{n:02d}" for n in lost)
                               + " —— 成片会短一截，而拼接那一步不会说话"})
    return out


def _map_vs_order(u: dict) -> list:
    """提示词正文里的 `Image N = <key>` 和这一条的实际上传顺序对不对。

    **这是这一类里最贵的一种错，而且此前一处都查不到。**
    实遇（2026-08-27）：提示词写着 `Image 5 = PRJ_YHJ__LOC_009_VIEW_A01_R01`，
    而那张图实际排在第 7 位 —— 前面多了两张。模型按正文的说明去用图，
    编号错位之后每条描述都套到别的图上：**画面出得来，参考全是错的**，
    而出图出片、验收、面板全是绿的。

    出片那一层的 `_check_video_ref_map` 只查「声明了的有没有真上传」，
    查不出「上传了但位置不对」。而位置这件事在导入这一刻就是确定的。

    只在正文真的写了映射时才查（V6.1 的视频提示词不写映射），
    而且**只报编号对不上的那几条** —— 正文里同一个编号被复述好几遍是常态。
    """
    prompt = u.get("prompt") or ""
    hits = _IMAGE_MAP.findall(prompt)
    if not hits:
        return []
    order = {n: aid for n, aid in u.get("refs") or []}
    if not order:
        return []
    bad = []
    seen = {}
    for num, aid in hits:
        n = int(num)
        if seen.get(n) == aid:
            continue                    # 同一条复述，不重复报
        seen[n] = aid
        got = order.get(n, "")
        if not got:
            bad.append(f"正文说 Image {n} = {aid}，而实际只上传 "
                       f"{len(order)} 张，没有第 {n} 张")
        elif got != aid and not (aid.endswith(got) or got.endswith(aid)):
            at = next((k for k, v in order.items() if v == aid), 0)
            bad.append(f"正文说 Image {n} = {aid}，"
                       + (f"而它实际排在第 {at} 位；第 {n} 位是 {got}"
                          if at else f"而第 {n} 位是 {got}，{aid} 压根没在上传清单里"))
    return bad


def _reconcile(units: list, decl: dict) -> list:
    """拿材料自己的申报和实物对账。

    对账能查出的是「你说 84 条、实际给了 80 条」。它查不出「你申报 84 条、
    实际也是 84 条、但本该 88 条」—— 那是它自己判断的总量，程序无从知道。
    这一条的边界要说清，不然会被当成「导入通过 = 材料齐全」。
    """
    out = []
    if not decl:
        if any(u.get("src") == "jsonl" for u in units):
            out.append({"level": "warn", "code": "MANIFEST_MISSING",
                        "msg": "材料里没有申报头（第一行的 "
                               "`{\"kind\":\"manifest\", ...}`）—— "
                               "**这样就查不出「少产了」**："
                               "该 84 条只给 80 条时，剩下的内部完全自洽，"
                               "引用链、编号、段号全绿，没有任何一处会说话。"
                               "让 codex 补一行申报再导一次"})
        return out
    got = {
        "total": len(units),
        "image": sum(1 for u in units if u["kind"] == "image"),
        "video": sum(1 for u in units if u["kind"] == "video"),
        "episodes": len({u["episode"] for u in units if u["episode"]}),
    }
    names = {"total": "总条数", "image": "图片条数", "video": "视频条数",
             "episodes": "集数"}
    for k, name in names.items():
        want = int(decl.get(k) or 0)
        if want and want != got[k]:
            d = got[k] - want
            out.append({"level": "error", "code": "MANIFEST_MISMATCH",
                        "msg": f"{name}对不上：材料自己申报 {want}，"
                               f"实际只有 {got[k]}"
                               f"（{'少' if d < 0 else '多'}了 {abs(d)}）—— "
                               f"{'少的那些内部完全自洽，不对账查不出来'
                                  if d < 0 else
                                  '多出来的是重复产的还是申报写错了，要它确认'}"})
    want_seg = decl.get("segs_per_episode")
    if want_seg:
        byep: dict = {}
        for u in units:
            if u["kind"] == "video" and u["episode"]:
                byep.setdefault(u["episode"], 0)
                byep[u["episode"]] += 1
        for ep, n in sorted(byep.items()):
            want = int((want_seg.get(ep) if isinstance(want_seg, dict)
                        else want_seg) or 0)
            if want and want != n:
                out.append({"level": "error", "code": "MANIFEST_MISMATCH",
                            "msg": f"{ep} 的段数对不上：申报 {want} 段，"
                                   f"实际 {n} 段 —— 缺的那几段成片会短一截，"
                                   f"而拼接那一步不会说话"})
    return out


def reproduce_request(units: list, issues: list) -> str:
    """把不合格的点写成一份「要重产的清单」，直接丢给 codex。

    用户原话（2026-08-25）：「我觉得可以先告知不合格的点」。整份不导仍然成立 ——
    这份清单是为了**不用重产整份 234 条**：它只列坏的那几条和坏在哪。

    **一次只读一个材料文件**（`_read_material`），所以补出来的那几条要替换回
    原材料里 —— JSONL 是一行一条，替换对应的行就行。这里绝不能写成
    「补丁和原材料一起放就行」：程序不会合并两个文件，
    写了就是承诺一个不存在的行为，而它失败的样子是「补的那几条压根没进来」。
    """
    bad = [i for i in issues if i["level"] == "error"]
    L = ["# 要重产的清单", "",
         f"这份材料有 {len(bad)} 条不合契约，**整份没有导入**。",
         "下面每一条说清了错在哪。只重产这些，其它的不用动。",
         "补出来的那几条**替换回原材料**里（JSONL 是一行一条，"
         "换掉对应的行就行），然后重新导入这一个文件 —— "
         "程序一次只读一个材料文件，不会把两份合起来。",
         ""]
    if not bad:
        return "# 要重产的清单\n\n没有不合格的条目。\n"
    by: dict = {}
    for i in bad:
        by.setdefault(i["code"], []).append(i["msg"])
    for code, msgs in by.items():
        L.append(f"## {code}（{len(msgs)} 条）")
        L.append("")
        L += [f"- {m}" for m in msgs]
        L.append("")
    L.append("## 重产时注意")
    L.append("")
    L.append("- key 全局唯一，filename 全局唯一 —— 重了就是静默覆盖，"
             "程序看不出是两条。")
    L.append("- 提示词正文要**完整可投喂**，不要写「同上」「同前」「略」。"
             "这一条程序查不了（长度和占位词都抓不准），"
             "残篇会一路跑到出图那一刻，图也出得来，只是不对。")
    L.append("- 补的这一份也带上申报头，写清这一份里有几条。")
    return "\n".join(L) + "\n"


def summary(units: list) -> dict:
    """给页面看的一眼数据。"""
    units = units_of(units)
    eps = sorted({u["episode"] for u in units if u["episode"]})
    return {
        "total": len(units),
        "image": sum(1 for u in units if u["kind"] == "image"),
        "video": sum(1 for u in units if u["kind"] == "video"),
        "episodes": eps,
        "refs_max": max([len(u["refs"]) for u in units] or [0]),
        "refs_hist": {k: sum(1 for u in units if len(u["refs"]) == k)
                      for k in sorted({len(u["refs"]) for u in units})},
    }



def episodes_stub(units: list) -> dict:
    """材料导入模式下的**集清单**，形状和环节1 切集的产物一致。

    为什么非有不可：材料里每一条都带集号，任务也带（`_produce_todo` 和
    `/api/generate` 都按它过滤），**按集过滤的机器一直是通的**。缺的只是
    「这个项目有哪几集」这份清单 —— 而那份清单只有环节1（切集）写，
    材料导入把环节1 整个顶掉了，于是它一直是空的。

    后果是静默降级，不是报错：
      · 页头那个「集」下拉是空的 → 生产页发出去的 `episode` 是空串
        → 不过滤 → 想只出 EP01，实际把全部集都出了（钱照花）
      · 「只跑生产」走的是 `_eps.ids(pj)`，同样空 → produce 步的
        `only` 是 None → 也是全部集
      · 生产页那一行连「全部集」的标签都不显示（`multi` 要求 >1 集），
        所以页面上看不出范围失效了

    只填集号和条数 —— 正文（`script`）故意留空，材料导入模式下压根没有
    剧本正文这回事。后面真跑环节1 的话 `episodes.build()` 会整份覆盖掉。
    """
    units = units_of(units)
    eps = sorted({u["episode"] for u in units if u["episode"]})
    rows = []
    for ep in eps:
        mine = [u for u in units if u["episode"] == ep]
        rows.append({
            "episode": ep, "title": "", "range": "",
            "entry_state": "", "exit_state": "",
            "duration_sec": 0, "key_events": [],
            "start_line": 0, "chars": 0, "script": "",
            # 页面上一眼看出这一集有多少活
            "image_units": sum(1 for u in mine if u["kind"] == "image"),
            "video_units": sum(1 for u in mine if u["kind"] == "video"),
        })
    return {"episodes": rows, "issues": [], "scope": "",
            # 这份不是切出来的，是数出来的。**标出来** —— 不标的话，
            # 后面谁拿它当「环节1 已经跑过」用，会得到一份没有正文的集清单。
            "from_material": True}


# ---------------------------------------------------------------- 导入

# ID 家族 → 落哪个文件夹。沿用现有目录，**别另起一套** ——
# 产物页、拼接、指纹注册表都按这几个目录找东西。
_DIRS = (
    ("SBSHEET", "04_故事板"), ("SBPKG", "04_故事板"), ("STORYBOARD", "04_故事板"),
    ("SCSTATE", "03b_场景状态图"), ("SCST", "03b_场景状态图"),
    ("VIDEO", "05_分段视频"),
)
_ASSET_DIRS = (
    ("CHAR", "人物身份资产"), ("PH", "人物身份资产"),
    ("LOOK", "人物造型资产"), ("LK", "人物造型资产"),
    ("CT", "连续状态资产"), ("COST", "服饰资产"),
    ("LOC", "场景资产"), ("PROP", "道具资产"),
    ("VEH", "载具资产"), ("CRE", "生物资产"),
    ("GRP", "群体资产"), ("VFX", "特效资产"),
)


def out_path(u: dict) -> str:
    """这一条的产物落在哪（相对项目根）。

    **kind 说了是视频就落分段视频目录，不看 key 长什么样。**
    契约要求视频的 key 是 `EP01-SEG01`（拼接按这个前缀挑本集分段），
    而那个形状里没有 `VIDEO` 家族前缀 —— 光按前缀猜的话，
    每一段视频都会落进 `02_固定资产/其它资产/`。
    表现出来是：出片全成功、任务全绿，而拼接在 `05_分段视频` 里
    一个文件都找不到，报「这一集没有分段」。契约里怎么说的、
    实际就得落哪，这两件事对不上是这里最贵的错。
    """
    stem = u["stem"]
    up = stem.upper()
    if u.get("kind") == "video":
        return f"05_分段视频/{u['filename']}"
    for key, folder in _DIRS:
        if key in up:
            return f"{folder}/{u['filename']}"
    for key, folder in _ASSET_DIRS:
        if re.search(rf"(^|_){key}[_\d]", up):
            return f"02_固定资产/{folder}/{u['filename']}"
    return f"02_固定资产/其它资产/{u['filename']}"


def prompt_path(u: dict, out_rel: str) -> str:
    """提示词落哪。**和 LLM 路径完全一致** —— `core/run_v34._rel` 那四个目录。

    用户原话（2026-08-24）：「文件结构需要和 LLM 处理的一致」。对。
    那四个子目录不是装饰，有几处按路径找东西：任务明细页按 `prompt_ref`
    显示并**就地改提示词**（改完立刻生效，worker 是出图那一刻才读文件的）、
    排错包按目录挑要打包哪些、以及人自己翻文件夹。
    全倒进一个目录的话，「这一集的视频提示词」要在 234 个文件里找。

    文件名保留材料里的长 ID —— 短号化会撞：材料里 `CHAR_001_R02` 和
    `CHAR_001_PH01_R02` 压成短号是同一个，而**撞了是静默覆盖**
    （两条任务写同一个文件，后一条盖前一条，只表现为少了一张图）。
    """
    if out_rel.startswith("05_分段视频"):
        return f"03_提示词/视频提示词/{u['stem']}_PROMPT.txt"
    if out_rel.startswith("04_故事板"):
        return f"03_提示词/故事板提示词/{u['stem']}_PROMPT.txt"
    if out_rel.startswith("03b_场景状态图"):
        return f"03_提示词/场景状态提示词/{u['stem']}_PROMPT.txt"
    return f"03_提示词/资产生产提示词/{u['stem']}_PROMPT.txt"


def task_key(u: dict) -> str:
    """任务的 key。

    视频用 `EP01-SEG01` —— **拼接那一步按这个前缀挑本集的分段**
    （`assemble` 里 `id.startswith(f"{ep}-")`）。换个写法拼接就找不到了。
    """
    if u["kind"] == "video" and u["episode"] and u["seg"]:
        return f"{u['episode']}-{u['seg']}"
    return u["stem"]


def build(units: list, size: str = "", ratio: str = "",
          duration: int = 15, system: str = "") -> dict:
    """units → tasks.json 的形状 + 要落盘的提示词 txt。

    参考图的 `file_ref` 指向**产出它的那一条**的落点 —— 引用链闭合是前提，
    所以这里能直接算出来，不用等出图时再解析。
    """
    # 材料申报的参数**盖过项目参数** —— 它是照剧情定的，页面上那几个是给
    # LLM 路径用的默认值。反过来（项目参数盖材料）的失败样子很难看：
    # 提示词按 20 秒写的，派出去的活是 15 秒，片子和提示词对不上而不报错。
    p = manifest_of(units).get("params") or {}
    size = str(p.get("image_size") or "") or size
    ratio = str(p.get("ratio") or "") or ratio
    duration = int(p.get("seg_duration") or 0) or duration
    units = units_of(units)        # 申报头不是任务，混进去就是一条永远做不完的活
    where = {u["stem"]: out_path(u) for u in units if u["stem"]}
    assets, storyboards, videos, texts = [], [], [], {}
    skipped = []
    for u in units:
        if u["kind"] not in ("image", "video"):
            # 认不出 kind 的**不建任务**。落到 else 分支会变成一条资产图 ——
            # 一条视频被当图片出掉，任务标成功，成片里少一段而没人报错。
            # 压过验收（force）导进来时，这里是最后一道，所以要记下来给人看。
            skipped.append({"no": u["no"], "key": u["stem"],
                            "why": "kind 认不出，没建任务"})
            continue
        if not u["filename"] or not u["prompt"]:
            skipped.append({"no": u["no"], "key": u["stem"],
                            "why": "缺文件名或提示词，没建任务"})
            continue
        rel = out_path(u)
        pr = prompt_path(u, rel)
        texts[pr] = u["prompt"]
        refs = [{"image_n": n, "asset_id": a, "file_ref": where.get(a, "")}
                for n, a in u["refs"]]
        t = {"key": task_key(u), "episode": u["episode"],
             "prompt_ref": pr, "reference_images": refs,
             "no_image_refs": [], "output": rel,
             "from_material": True}
        if u["kind"] == "video":
            t["params"] = {"duration": u["seconds"] or duration,
                           "ratio": u["ratio"] or ratio or "9:16"}
            t["segment"] = u["seg"]
            # 骨架就是它引的那几张故事板 —— 视频那一层读 storyboard_refs
            # 契约明确给了 `storyboard_refs` 就用它的 —— **别去猜 ID**。
            # 猜（按名字里有没有 SBSHEET）对 md 那条路是唯一办法，
            # 但契约里这件事是说清了的，猜只会在命名不同时悄悄挑错。
            named = set(u.get("spine") or [])
            spine = ([r for r in refs if r["asset_id"] in named] if named
                     else [r for r in refs if "SBSHEET" in r["asset_id"].upper()
                           or "STORYBOARD" in r["asset_id"].upper()])
            t["storyboard_refs"] = [
                {"order": i, "sheet_id": r["asset_id"], "spine_role": "",
                 "file_ref": r["file_ref"]} for i, r in enumerate(spine, 1)]
            t["storyboard_ref"] = spine[0]["file_ref"] if spine else ""
            # **骨架要从 reference_images 里拿掉。**
            #
            # `spine` 是从 `refs` 里挑出来的**子集**，而上面 `t` 里的
            # `reference_images` 是整份 `refs` —— 出片那一层传的是
            # 「storyboard_refs 整条 + reference_images 整条」，
            # 一加就是双倍：9 张骨架变 18 张，而 `image_n` 是按上传顺序算的，
            # 于是后面每一条描述都套到别的图上。**画面出得来，参考全是错的。**
            #
            # 用户实遇（2026-08-26，材料导入的项目）：面板上「参 0/18」。
            # 上一轮我把同样的毛病修在 `run_v34` 的装配里 —— 那是 LLM 那条路，
            # 材料这条路压根没经过它，所以「修完还是 18」。
            spine_keys = {id(r) for r in spine}
            t["reference_images"] = [r for r in refs if id(r) not in spine_keys]
            videos.append(t)
        else:
            t["params"] = {"size": u["ratio"] or size or "9:16"}
            (storyboards if rel.startswith("04_故事板") else assets).append(t)
    return {"skipped": skipped, "params": {"image_size": size, "ratio": ratio,
                                          "seg_duration": duration},
            # 体系写真值。原来写死 "material"，虽然眼下没人读这个字段
            # （页面挑体系只认 /api/project 给的 meta 值），
            # 但写一个两套都不认的值，第一个来读它的人就会挑错。
            "tasks": {"system": system or "material",
                      "from_material": True, "asset_tasks": assets,
                      "scstate_tasks": [], "storyboard_tasks": storyboards,
                      "video_tasks": videos},
            "prompts": texts}
