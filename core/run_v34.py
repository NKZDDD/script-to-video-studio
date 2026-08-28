# -*- coding: utf-8 -*-
"""V3.4 体系的执行层：把环节图和模板真正跑起来。

分工：
    system_v34.py   环节图（数据）
    prompts/n*.md   模板（数据）
    这里            按范围取依赖、填占位符、调模型、存盘
    produce.py      出图出片（体系无关）
    stages.py       V6.1 的执行层，和这里并存互不干扰

单独成文件而不是改 stages.py：两套体系的执行逻辑混在一个 1800 行的文件里，
改一处就要担心碰坏另一套。这里只写 V3.4 的，V6.1 一行不动。
"""

from __future__ import annotations

import itertools
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import diagnose, episodes as _eps, ledger, system_v34 as V
from .executor import LLM_GATE
from .llm import LLMCancelled, refusal_reason
from .stages import jd, load_prompt, prompt_files, render, system_prompt
from .store import Project, keep_partial, write_text


# ---------------------------------------------------------------- 依赖与占位符

def deps_data(pj: Project, stage_id: str, episode: str = "") -> dict:
    """把这个环节声明的依赖产物读出来。

    按范围取：全剧级产物存在项目根下（episode=""），逐集/逐段的存在集目录里。
    取错目录的后果是拿到空字典 —— 模板里那个占位符变成 `{}`，
    模型看到空的输入通常会自己编，而不是报错。
    """
    _, deps, _ = V.LLM_SPEC[stage_id]
    out = {}
    for d in deps:
        src = next((s for s in V.STAGES if s.get("out") == d), None)
        ep = "" if (src or {}).get("scope") == "series" else episode
        out[d] = pj.stage_data(d, ep) or {}
    return out


def mapping(pj: Project, stage_id: str, params: dict, data: dict,
            episode: str = "", segment: str = "",
            extra: Optional[dict] = None) -> dict:
    """模板里 {{X}} 各填什么。真跑和预览共用这一个，分开写迟早飘。

    `extra` 里下划线开头的是**给这个函数看的开关**（比如这一批要写哪几个
    资产），不是占位符；其余的最后覆盖上去，是分批跑要换掉的那几个值。
    """
    m = {
        # 项目参数只发白名单里的几项。以前是「除了剧本全发」，结果配置里
        # 删掉的旧旋钮还留在 config.json 里照样发过去，模型当成指令执行。
        "PARAMS": jd({k: params[k] for k in
                      ("project_code", "duration", "ratio", "image_size")
                      if k in params}),
        "EPISODE": episode,
        "SEGMENT": segment,
        # DURATION 是**一个容器**的秒数（视频模型一次最多生成多久），
        # 不是这一集多长。这两个数字必须分开发 —— 合成一个的后果实跑撞过：
        # 第九环节是**整集级**的，它要为一整集设计镜头，而拿到的唯一秒数是 15，
        # 就把 8 个场次压成了 15 秒的「高密度蒙太奇」（模型自己在
        # shot_count_rationale 里写了「完整实时演出远超15秒」，它知道装不下）。
        # 然后第十环节照着 15 秒装出 1 个 SEG，第十二环节被迫在一张纸上画 16 格
        # （模板上限 3×3），模型扛不住 8 个场次的世界状态就开始瞎填 ——
        # 审计报的 7 条 BLOCK 里有 5 条是这一个故障的下游。
        "DURATION": params.get("duration", 15),
        "EPISODE_DURATION": _ep_seconds(pj, episode, params),
        "SEGMENTS_TARGET": _seg_target(pj, episode, params)[0],
        "SEGMENTS_WHY": _seg_target(pj, episode, params)[1],
        "IMAGE_SIZE": params.get("image_size", "1024x1536"),
        "SEG_COUNT": len(segments_of(pj, episode)) if episode else 0,
        "SCRIPT": _script_for(pj, stage_id, episode, params),
        # 能力档位必须进提示词，否则冻结了也白冻：第九环节会照着「六类机制
        # 随便挑」写，而模型做不出来 —— 转场糊掉或者变成一个长镜头，不报错。
        "CAPABILITY": _capability_block(pj),
        "REF_LIMIT": _ref_limit_block(params, stage_id),
        # 分批用的两个，**必须有默认值**：没分批时（老项目没切集、预览）
        # 留空的话模板里会原样出现 `{{BATCH_SCOPE}}`，模型会把它当成
        # 一个要填的空位或者一句奇怪的指令。
        "BATCH_SCOPE": "**一次处理全剧。** 所有集都在这一次里排完。",
        "DONE_SCENES": "（这是第一次排，前面没有已排好的集）",
    }
    # 第八/九环节分批用的两个「上一批末尾」也要有默认值：预览、
    # 老项目退回一次整集跑的场合，模板里的占位符不能原样发出去。
    # 分批跑时这两段由 run_n8_batched / run_n9_batched 用 extra 覆盖。
    if stage_id == "n8":
        m["BATCH_SCOPE"] = "（这一集的场次一次做完，不分批。）"
        m["PREV_CVS"] = "（这是第一批，前面没有已写好的场次。）"
    if stage_id == "n9":
        m["BATCH_SCOPE"] = ("（这一集一次做完：镜头和时间计划铺满全集，"
                            "不按时窗分批。）")
        m["LAST_SHOT"] = "（这是第一批，前面没有镜头。）"
    # 项目基础信息：50 多个字段，模板里写了 {{X}} 才收到。
    # 放在产物之前合并，让产物占位符能覆盖同名的（实际上不重名，
    # 但顺序写死比"应该不会撞"可靠）。
    from . import settings as _st
    # 旁白那一段和字幕规则一样，是**按取值生成的整段文字**，不是几个孤立的值 ——
    # 丢占位符进去的话，没旁白的项目会看到「本项目：否　声音属于：」这种
    # 半截句子，模型读到空标签会自己去填。
    m["NARRATION_RULE"] = _st.narration_rule(pj)
    # 字幕/画面文字规则同款（n4b 的收尾句要按这个口径转述 —— 实跑 N001
    # 写死了「画面内不得出现任何文字」，把道具上的字一起抹了）。
    m["SUBTITLE_RULE"] = _st.subtitle_rule(pj)
    # 「拍成真人还是 3D」也要按取值生成整句规则。两套体系共用 _common，
    # 只接一边的话，另一边渲染出来是原样的 {{MEDIUM_RULE}} ——
    # 模型会把它当成一个要填的空位。
    m["MEDIUM_RULE"] = _st.medium_rule(pj)
    # 总时长/集数/每集时长三个量互相决定，合成一段发给环节1 ——
    # 关键是那句「集数是算出来的，不是数剧本里有几章」。
    m["LENGTH_PLAN"] = _st.length_plan(pj)
    m.update(_st.mapping(pj, params, {
        "episode_duration": _ep_seconds(pj, episode, params),
        "current_episode": episode,
        "reference_capacity_per_call": (
            params.get(f"ref_limit_{_LIMIT_CONSUMER.get(stage_id, 'video')}")
            or params.get("ref_limit", "")),
        "target_video_model": (capability_of(pj) or {}).get("target_video_model", ""),
        "native_multishot_support": (capability_of(pj) or {}).get(
            "native_multishot_support", ""),
    }))

    extra = dict(extra or {})
    if stage_id == "n4b":
        data = dict(data, n4_assets=_n4b_worklist(
            pj, data.get("n4_assets") or {}, extra.pop("_n4b_batch", None)))
    # 环节8/9 按场次分批：导演设计和 CVS 只发本批那几场的。
    # 同样走「下划线开关」而不是直接覆盖占位符 —— 这样按用途的裁剪
    # （PRODUCT_NEEDS）照常生效，分批只负责「按场切」这一层。
    scene_batch = extra.pop("_scene_batch", None)
    # 逐段环节：整集级的镜头表/状态表/空间主表按 ID 链裁到这一段。
    # 算一次给所有产物用 —— 每份各算一次会把 n8/n9/n10 各读三遍。
    ctx = _seg_context(pj, episode, segment,
                       extra.pop("_log", None)) if segment else None
    for out_name, obj in data.items():
        # 三层裁剪，各管一件事：
        #   按集   全剧级产物 → 只剩本集（叙事结构、总账）
        #   按段   逐集产物   → 只剩本段（装箱、场景状态图、故事板）
        #   按用途 这个环节真正要哪几部分（PRODUCT_NEEDS）
        if V.scope_of(stage_id) != "series":
            obj = _narrow_episode(out_name, obj, episode)
        if scene_batch and out_name in ("n7_directing", "n8_cvs"):
            obj = _narrow_scenes(out_name, obj, scene_batch)
        if segment:
            obj = _narrow(out_name, obj, segment)
            # 按段号直接裁不到的那几份（整集的镜头表、状态表、空间主表），
            # 走 ID 链再裁一道 —— 这是逐段环节输入的大头
            obj = _narrow_join(out_name, obj, ctx)
        # 逐段裁剪之后再按环节投影：两件事都是「只发这一步真正要的」，
        # 但一个按段切、一个按用途切，顺序无所谓，都做才完整。
        obj = project_product(stage_id, out_name, obj)
        m[V.placeholder_of(out_name)] = jd(obj)
    # 分批的那几个值最后覆盖 —— 上面那些是「这个环节一般发什么」，
    # 这一批要发什么以调用方给的为准（比如 n3 只发本集剧本）。
    m.update({k: v for k, v in extra.items() if not k.startswith("_")})
    return m


def _ep_seconds(pj: Project, episode: str, params: dict) -> int:
    """这一集**总共**多长（秒）。不知道就返回 0。

    第一环节看完全篇按剧情事件定的，存在 episodes.json 的 duration_sec 里。
    V6.1 一直在用它算段数，V5.6 这边我搭 stage 图时漏搬了 —— 结果第九环节
    只看得见容器的 15 秒，把整集当成 15 秒来设计。

    返回 0（老项目的产物里没有这个字段）时模板会退回只按容器算，
    和以前的行为一致 —— 不至于让老项目跑不动。
    """
    if not episode:
        return 0
    for e in _eps.load(pj).get("episodes", []):
        if e.get("episode") == episode and e.get("duration_sec"):
            return int(e["duration_sec"])
    return 0


def _seg_target(pj: Project, episode: str, params: dict) -> tuple:
    """这一集该切几段，以及这个数是怎么来的。没有集号时返回 (0, "")。

    直接用 V6.1 那份 —— 算法是体系无关的（本集秒数 ÷ 单段秒数），
    照着重写一遍只会让两边慢慢飘开。
    """
    if not episode:
        return 0, ""
    return _eps.seg_target(pj, episode, params)


# 全剧级产物里，哪些数组是按集标了号的。逐集环节拿到之后要裁成只剩本集。
#
# 叙事结构、连续性总账这些改成全剧一份之后，逐集环节收到的是**全剧全量**。
# 不裁两个后果，都不报错：
#   · n7 导演拿到全剧的场次，会把别的集的戏排进这一集
#   · 40 集的总账发 40 遍，钱按集翻倍
# 空间主表和资产表**不裁** —— 它们本来就是全剧共享的参照，
# 裁掉会让跨集复用的地点和角色在本集"消失"，模型于是重新发明一个。
_EP_INDEXED = {
    "n3_narrative": [("scenes", "episode"), ("beats", "episode"),
                     ("episode_arcs", "episode")],
    "n6_ledger": [("ledger", "episode")],
}


def _narrow_episode(out_name: str, obj: dict, episode: str) -> dict:
    """把全剧级产物裁成只剩这一集的部分。

    只裁**明确标了集号**的那几个数组。标了集号才裁得准；
    没标的一律整份留着（宁可多发，也别悄悄少给下游一段上下文）。
    """
    if out_name not in _EP_INDEXED or not isinstance(obj, dict) or not episode:
        return obj
    out = dict(obj)
    for key, field in _EP_INDEXED[out_name]:
        rows = obj.get(key)
        if not isinstance(rows, list):
            continue
        mine = [r for r in rows if isinstance(r, dict) and r.get(field) == episode]
        # 一条都没标集号时不裁：那是模型没按 schema 写，
        # 裁完会得到空数组，下游看到"这一集没有任何场次"然后自己编。
        if mine or any(isinstance(r, dict) and r.get(field) for r in rows):
            out[key] = mine
    # beats 按 scene 归属，scene 已经裁过了，再按 scene_id 收一次更准
    if out_name == "n3_narrative":
        ids = {s.get("scene_id") for s in out.get("scenes") or []}
        beats = obj.get("beats")
        if ids and isinstance(beats, list):
            out["beats"] = [b for b in beats
                            if isinstance(b, dict) and b.get("scene_id") in ids]
    return out


# ---------------------------------------------------------------- 按场分批
#
# 第八、九环节是整集一次调用的，一集的场次多了输入和输出一起超 ——
# 实跑撞的就是这个：第八环节整集的走位全量发，第九环节 19+ 镜的大 JSON
# 在中转上随机断流，三次 LLM_SCHEMA_FAIL。所以这两个环节按场次切批：
#   第八环节  每批 1~2 场，输出这场的 CVS/VT，输入带上一场末尾的 CVS 原文
#   第九环节  按预估 SEG 时窗装箱（一批约一个窗的秒数），批间续传末镜
#             原文和时间轴终点，合并时程序加时间偏移
# 场不跨批：一场的走位和 CVS 要整场看得见，劈成两半两边都接不上。

def _scenes_of(pj: Project, episode: str) -> list:
    """这一集有哪些场（第三环节排的顺序就是故事顺序）。"""
    rows = [s for s in (pj.stage_data("n3_narrative", "") or {}).get("scenes") or []
            if isinstance(s, dict) and s.get("scene_id")
            and s.get("episode") == episode]
    return [str(s["scene_id"]) for s in rows]


def _cvs_scene(cvs: dict) -> Optional[str]:
    """一条 CVS 属于哪一场。先看 scene_id，没有就从 CVS 编号里挖
    （模板的编号格式就是 CVS_{集}_SC01_01，SC 号天然在编号里）。

    scene_id 不是必需字段，模型经常只按必填的写 —— 只认字段的话
    老产物全部归不上场，分批的续跑就废了（每场都当没做过）。
    """
    sid = str((cvs or {}).get("scene_id") or "").strip()
    if sid:
        return sid
    m = re.search(r"(SC\d+)", str((cvs or {}).get("cvs_id") or ""))
    return m.group(1) if m else None


def _cvs_scene_map(pj: Project, episode: str) -> dict:
    """{cvs_id: scene_id}，第九环节给镜头归场用（沿 source_cvs 对回去）。"""
    return {str(c.get("cvs_id")): _cvs_scene(c)
            for c in (pj.stage_data("n8_cvs", episode) or {}).get("cvs") or []
            if isinstance(c, dict) and c.get("cvs_id")}


def _narrow_scenes(out_name: str, obj: dict, scenes: set) -> dict:
    """把整集的产物裁成只剩这几场（第八/九环节分批发输入用）。

    和 _narrow_episode 同一个原则：只裁**明确按场标了号**的数组，
    一条都对不上就整份发 —— 裁错的后果是模型看不到本批真正要用的
    走位/状态，然后自己编一个，而这不报错。
    """
    if not scenes or not isinstance(obj, dict):
        return obj
    if out_name == "n7_directing":
        pre = {f"{s}-" for s in scenes}
        out = dict(obj)
        rows = obj.get("scene_directing")
        if isinstance(rows, list):
            mine = [r for r in rows
                    if isinstance(r, dict) and r.get("scene_id") in scenes]
            if mine or any(isinstance(r, dict) and r.get("scene_id") for r in rows):
                out["scene_directing"] = mine
        for key in ("beat_directing", "blocking", "performance_intent",
                    "blocked_by_physics"):
            rows = obj.get(key)
            if not isinstance(rows, list):
                continue
            mine = [r for r in rows if isinstance(r, dict)
                    and any(str(r.get("beat_id") or "").startswith(p) for p in pre)]
            if mine or any(isinstance(r, dict) and r.get("beat_id") for r in rows):
                out[key] = mine
        return out
    if out_name == "n8_cvs":
        rows = [c for c in (obj.get("cvs") or []) if isinstance(c, dict)]
        if not rows:
            return obj
        mine = [c for c in rows if _cvs_scene(c) in scenes]
        # 一条都归不上场（老产物连编号里都没写 SC 号）就不裁
        if not mine and not any(_cvs_scene(c) for c in rows):
            return obj
        ids = {str(c.get("cvs_id") or "") for c in mine}
        out = dict(obj, cvs=mine)
        vt = obj.get("vt")
        if isinstance(vt, list):
            # 跨场的 VT 两头的场任一在本批就带上 —— 第九环节设计
            # 本批第一镜时得知道从上一场是怎么过渡过来的
            out["vt"] = [v for v in vt if isinstance(v, dict)
                         and (str(v.get("source_cvs") or "") in ids
                              or str(v.get("target_cvs") or "") in ids)]
        return out
    return obj


def _scene_hit(scene: str, have) -> bool:
    """场次号带不带集号前缀（EP01-SC01 / SC01）都算同一场。

    n3 并发之后场次号带集号前缀，而模型在 CVS 编号里经常只写裸的 SC 号 ——
    只认全名的话那一场永远算「没做过」，续跑会把它再做一遍，
    做完两份混在一起，还是不报错。
    """
    if scene in have:
        return True
    bare = scene.rsplit("-", 1)[-1]
    # 只比最后一段是安全的：have 全来自本集的产物，SC 号在本集内唯一
    return any(str(h).rsplit("-", 1)[-1] == bare for h in have)


def n8_scene_split(pj: Project, episode: str) -> tuple:
    """第八环节分批的进度：哪几场已有 CVS、哪几场还没有。

    依据是已存产物里能归到场的 CVS —— 一场至少要有一条才算做过。
    批是整批成的：一批里两场要么都在产物里，要么都不在，
    不存在「半场」状态。

    **一条都归不上场的产物不在此列**（老项目、或模型没按模板的
    CVS_{集}_{场}_{序} 编号）：那种当整集已做完，调用方负责回退 ——
    归不上就永远「没做完」，每点一次「继续」都重跑整集。
    """
    scenes = _scenes_of(pj, episode)
    have = {_cvs_scene(c) for c in
            (pj.stage_data("n8_cvs", episode) or {}).get("cvs") or []
            if isinstance(c, dict)}
    have = {h for h in have if h}
    done = [s for s in scenes if _scene_hit(s, have)]
    return done, [s for s in scenes if s not in done]


def n9_scene_split(pj: Project, episode: str) -> tuple:
    """第九环节分批的进度：哪几场已有镜头、哪几场还没有。

    镜头的 scene_id 不是必需字段，模型经常不写 —— 沿 source_cvs 对回
    CVS 归场（_cvs_scene_map 就是干这个的）。两头都没有的镜头归不上场，
    归不上就当没做过：宁可重排，不能悄悄漏掉。

    **一条都归不上的产物例外**（老项目、编号全对不上）：那种当整集
    已做完，由调用方回退 —— 否则每点一次「继续」都重排整集。
    """
    scenes = _scenes_of(pj, episode)
    cvs_map = _cvs_scene_map(pj, episode)
    have = set()
    for s in (pj.stage_data("n9_shots", episode) or {}).get("shots") or []:
        if not isinstance(s, dict):
            continue
        sc = str(s.get("scene_id") or "").strip() \
            or cvs_map.get(str(s.get("source_cvs") or ""))
        if sc:
            have.add(sc)
    done = [s for s in scenes if _scene_hit(s, have)]
    return done, [s for s in scenes if s not in done]


def _beats_per_scene(pj: Project) -> dict:
    """{场次: 拍数}。估每场占多少秒用的 —— 拍多的场戏多，时长份额也该大。
    估错只影响切批的位置，不影响时间轴本身（时间轴以每批实际排出的 end 为准）。"""
    out: dict = {}
    for b in (pj.stage_data("n3_narrative", "") or {}).get("beats") or []:
        if isinstance(b, dict) and b.get("scene_id"):
            sid = str(b["scene_id"])
            out[sid] = out.get(sid, 0) + 1
    return out


def _n8_batches(pj: Project, todo: list) -> list:
    """第八环节切批：普通的两场一批，拍了六拍以上的大场单独一批。

    一场的走位和 CVS 要整场看得见，劈成两半两边都接不上 —— 所以
    批的边界只能落在场的边界上，批内场数是唯一的调节旋钮。
    """
    beats = _beats_per_scene(pj)
    batches, cur = [], []
    for s in todo:
        big = beats.get(s, 0) >= 6
        if cur and (len(cur) >= 2 or big or beats.get(cur[0], 0) >= 6):
            batches.append(cur)
            cur = []
        cur.append(s)
    if cur:
        batches.append(cur)
    return batches


def _n9_windows(pj: Project, episode: str, params: dict, todo: list) -> list:
    """第九环节切批：按预估时窗装箱，一批约一个 SEG 容器的秒数。

    每场多少秒这一步之前没人知道（时长正是第九环节排的），只能按拍数
    分摊本集总时长来估。一批装满一个窗口就收 —— 一批的镜头数自然落在
    5~12 镜那档，正是中转一次吐得稳的量。
    老项目没存本集秒数的，估不了窗口，按两场一批兜底（输出仍有界）。
    """
    dur = _ep_seconds(pj, episode, params)
    clip = int(params.get("duration") or 15) or 15
    if dur <= 0:
        return [(todo[i:i + 2], 0.0) for i in range(0, len(todo), 2)]
    beats = _beats_per_scene(pj)
    weights = [max(1, beats.get(s, 0)) for s in todo]
    total_w = sum(weights) or 1
    batches, cur, cum = [], [], 0.0
    for s, w in zip(todo, weights):
        if cur and (len(cur) >= 3 or cum >= clip):
            batches.append((cur, cum))
            cur, cum = [], 0.0
        cur.append(s)
        cum += dur * w / total_w
    if cur:
        batches.append((cur, cum))
    return batches


def merge_cvs(previous: dict, fresh: dict, episode: str,
              keep_refs=frozenset()) -> dict:
    """第八环节分批合并。**必须合并，不能覆盖** —— 和 n3/n4b 同一个道理：
    这一批只写了自己那几场，直接存盘会把前面几批全冲掉，且不报错。

    两个改号动作都在这里，不指望模型自觉：
      · 撞了已有编号、又不是同一场的 CVS 改成带场次号的唯一编号。
        模型不按 CVS_{集}_{场}_{序} 写、批批从 01 重编的话，按编号合并
        会把前几批的 CVS 挤掉 —— 越跑越少。
      · VT 编号接着已有条数往下编。模板里 VT 就是全集递增的，
        每批都从 01 编起必然撞号；VT 编号没人引用，改了不影响别的。

    `keep_refs` 里的编号**引用**不改 —— 那是上一批末尾 CVS 的真实编号
    （PREV_CVS 里给过模型），跨场 VT 的 source_cvs 指的是它。
    模型重启编号时这个号也会撞上本批的新行，行本身照改（保唯一），
    指向旧行的引用保住（保接得上）。
    """
    previous, fresh = previous or {}, fresh or {}
    prev_scene = {str(c.get("cvs_id") or ""): _cvs_scene(c)
                  for c in previous.get("cvs") or [] if isinstance(c, dict)}
    rename: dict = {}
    cvs_rows = []
    for c in fresh.get("cvs") or []:
        if not isinstance(c, dict) or not c.get("cvs_id"):
            continue
        cid = str(c["cvs_id"])
        old_sc, new_sc = prev_scene.get(cid), _cvs_scene(c)
        # 撞号且明确不是同一场的才改号；同一场的照旧覆盖（重跑语义）
        if cid in prev_scene and old_sc and new_sc and old_sc != new_sc:
            rename[cid] = f"{cid}_{new_sc}"
            c = dict(c, cvs_id=rename[cid])
        cvs_rows.append(c)
    if rename:
        for t in fresh.get("vt") or []:
            if not isinstance(t, dict):
                continue
            for k in ("source_cvs", "target_cvs"):
                v = str(t.get(k) or "")
                if v in rename and v not in keep_refs:
                    t[k] = rename[v]
    n0 = len([t for t in previous.get("vt") or [] if isinstance(t, dict)])
    for i, t in enumerate(fresh.get("vt") or [], 1):
        if isinstance(t, dict):
            t["vt_id"] = f"VT_{episode}_{n0 + i:03d}"
    merged = dict(previous)
    for key, id_field in (("cvs", "cvs_id"), ("vt", "vt_id")):
        rows = [r for r in merged.get(key) or [] if isinstance(r, dict)]
        pos = {str(r.get(id_field) or ""): i for i, r in enumerate(rows)
               if r.get(id_field)}
        for r in (cvs_rows if key == "cvs" else fresh.get("vt") or []):
            if not isinstance(r, dict) or not r.get(id_field):
                continue
            rid = str(r[id_field])
            if rid in pos:
                rows[pos[rid]] = r          # 同场重跑：覆盖旧的那一条
            else:
                pos[rid] = len(rows)
                rows.append(r)
        merged[key] = rows
    # camera_free_check 是一句话，按行累加 —— 后一批盖掉前一批
    # 等于把前面几批的确认丢了
    old = str(previous.get("camera_free_check") or "").strip()
    new = str(fresh.get("camera_free_check") or "").strip()
    lines = [x for x in old.split("\n") if x.strip()]
    if new and new not in lines:
        lines.append(new)
    if lines:
        merged["camera_free_check"] = "\n".join(lines)
    return merged


def _renumber_batch_shots(fresh: dict, episode: str, first_shot: int,
                          first_tr: int, protect=frozenset()) -> None:
    """把这一批的镜头/转场编号改成全局续号，批内引用同步改。

    模型每批都从 001 编起（提示词里说了接着编也拦不住），直接按编号合并
    会把前几批的镜头挤掉 —— 和 n3 并发时各集都从 SC01 编起是同一个坑，
    解法也一样：程序保证不撞，不靠模型自觉。

    引用（from_shot / to_shot / shot_id / outgoing_transition_id）里指向
    本批镜头的跟着改；指向上一批的（LAST_SHOT 里给过它真实编号）原样
    保留 —— 那是已经落盘的编号，改了反而接不上。

    `protect` 里的编号在转场的 from_shot / to_shot 里**不改** —— 那是
    上一批最后一镜的真实编号（LAST_SHOT 里给过模型），跨批转场的
    from_shot 指的是它。模型重启编号时这个号也会撞上本批的旧号，
    行本身照改（保唯一），指向旧行的引用保住（保接得上）——
    和 merge_cvs 的 keep_refs 同一个道理。

    timing_plan 的 shot_id **不受 protect 保护**：撞上保护号的时间行
    几乎总是本批自己的（模型给上一批最后一镜重写时间行的情况，
    merge_shots 的「只收本批镜头」过滤已经按 mine 集拦了）。
    在这里保护它反而会让本批那一镜**静默丢掉时间行** ——
    时间线上少一行不报错，比转场接错更难发现。
    """
    shot_map, tr_map = {}, {}
    for s in fresh.get("shots") or []:
        if not isinstance(s, dict) or not s.get("shot_id"):
            continue
        old = str(s["shot_id"])
        new = f"SH_{episode}_{first_shot + len(shot_map):03d}"
        shot_map[old] = new
        s["shot_id"] = new
    for t in fresh.get("transitions") or []:
        if not isinstance(t, dict) or not t.get("transition_id"):
            continue
        old = str(t["transition_id"])
        new = f"TR_{episode}_{first_tr + len(tr_map):03d}"
        tr_map[old] = new
        t["transition_id"] = new
    for t in fresh.get("transitions") or []:
        if not isinstance(t, dict):
            continue
        for k in ("from_shot", "to_shot"):
            v = str(t.get(k) or "")
            if v in shot_map and v not in protect:
                t[k] = shot_map[v]
    for r in fresh.get("timing_plan") or []:
        if not isinstance(r, dict):
            continue
        v = str(r.get("shot_id") or "")
        if v in shot_map:
            r["shot_id"] = shot_map[v]
        v = str(r.get("outgoing_transition_id") or "")
        if v in tr_map:
            r["outgoing_transition_id"] = tr_map[v]


def _shift_batch_timing(fresh: dict, offset: float,
                        log: Optional[Callable] = None) -> None:
    """把这一批的时间计划整体往后挪 offset 秒。

    每批的 timing_plan 都从 0 铺起 —— 让模型续绝对时间轴等于让它做
    45.2 + 2.4 这类算术，不可靠；加法由程序做。模型没听、自己从
    offset 接着铺的也认得出来（那一批的首镜 start ≈ offset），不挪。

    只挪 start / end / transition_time_range 这三个**绝对量**。
    dialogue_start 这类写在镜内的字段（模板示例里 0.3~2.0 落在
    0~2.4 的镜头里）是相对量，挪了反而错。
    """
    if offset <= 0:
        return
    rows = [r for r in fresh.get("timing_plan") or []
            if isinstance(r, dict) and isinstance(r.get("start"), (int, float))]
    if not rows:
        return
    first = min(float(r["start"]) for r in rows)
    if abs(first - offset) <= 1.0:
        if log:
            log(f"  这一批自己从第 {first:g} 秒接着铺了，不再加偏移")
        return
    for r in fresh.get("timing_plan") or []:
        if not isinstance(r, dict):
            continue
        for k in ("start", "end"):
            if isinstance(r.get(k), (int, float)):
                r[k] = float(r[k]) + offset
        tr = r.get("transition_time_range")
        if isinstance(tr, list) and len(tr) == 2 \
                and all(isinstance(x, (int, float)) for x in tr):
            r["transition_time_range"] = [tr[0] + offset, tr[1] + offset]


def merge_shots(previous: dict, fresh: dict) -> dict:
    """第九环节分批合并。编号在合并前已改成全局续号，按 id 合并即可。

    timing_plan 只收**本批镜头**的行 —— 模型偶尔会把上一批最后一镜的
    时间行也重写一份（想给那条接上跨批转场），那份行的时间坐标是它
    自己批里的，盖上去会把已落盘的那行改坏。
    """
    previous, fresh = previous or {}, fresh or {}
    mine = {str(s.get("shot_id") or "") for s in fresh.get("shots") or []
            if isinstance(s, dict)}
    merged = dict(previous)
    for key, id_field in (("shots", "shot_id"),
                          ("transitions", "transition_id"),
                          ("timing_plan", "shot_id")):
        rows = [r for r in merged.get(key) or [] if isinstance(r, dict)]
        pos = {str(r.get(id_field) or ""): i for i, r in enumerate(rows)
               if r.get(id_field)}
        for r in fresh.get(key) or []:
            if not isinstance(r, dict) or not r.get(id_field):
                continue
            if key == "timing_plan" and str(r.get("shot_id") or "") not in mine:
                continue
            rid = str(r[id_field])
            if rid in pos:
                rows[pos[rid]] = r
            else:
                pos[rid] = len(rows)
                rows.append(r)
        merged[key] = rows
    # 两段说明按行累加：每批各有各的理由，后批盖掉前批 = 丢依据
    for key in ("shot_count_rationale", "transition_summary"):
        old = str(previous.get(key) or "").strip()
        new = str(fresh.get(key) or "").strip()
        lines = [x for x in old.split("\n") if x.strip()]
        if new and new not in lines:
            lines.append(new)
        if lines:
            merged[key] = "\n".join(lines)
    return merged


def _last_cvs_of(out: dict) -> Optional[dict]:
    """已合并产物里最后一条 CVS —— 下一批的 PREV_CVS 用。"""
    rows = [c for c in (out or {}).get("cvs") or [] if isinstance(c, dict)]
    return rows[-1] if rows else None


def _last_shot_of(out: dict) -> Optional[dict]:
    """时间轴上排到最后的那一镜。按 timing_plan 的 end 找（不假设顺序），
    对不上就退回 shots 的最后一条。"""
    shots = {str(s.get("shot_id") or ""): s
             for s in (out or {}).get("shots") or [] if isinstance(s, dict)}
    best = None
    for r in (out or {}).get("timing_plan") or []:
        if isinstance(r, dict) and isinstance(r.get("end"), (int, float)) \
                and str(r.get("shot_id") or "") in shots:
            if best is None or float(r["end"]) >= best[0]:
                best = (float(r["end"]), shots[str(r["shot_id"])], r)
    if best:
        return best[1]
    tail = [s for s in (out or {}).get("shots") or [] if isinstance(s, dict)]
    return tail[-1] if tail else None


# 逐段产物里，哪个数组按段索引、用哪个键认段号。
_SEG_INDEXED = {
    "n10_segs": ("segs", "seg_id"),
    "n11_scstate": ("scstates", "seg_id"),
    "n12_storyboard": ("sbpkg", "seg_id"),
    "n13_video": ("video_plan", "seg_id"),
}


# 逐段环节收到的**整集级**产物，靠一条 ID 链裁到这一段。
#
# 起因很具体：实跑里 n11 是逐段环节，单段输入却有 **101,342 token** ——
# 一集 17 段就把同一份东西发了 17 遍。而输入越大，模型吐第一个字之前
# 想得越久，中转站看不到数据就在 125 秒切断（那一晚三条 524 都是这个）。
#
# 链是这样接的，四层都有 ID 可以对：
#
#     n10_segs.included_shots  →  n9_shots.shot_id
#     n9_shots.source_cvs      →  n8_cvs.cvs_id
#     n8_cvs.spatial_id        →  n5_spatial.spatial_masters.spatial_id
#
# **接不上就整份发。** 任何一环取空（模型没填 included_shots、ID 对不上、
# 老项目的产物没有这些字段）都退回原来的行为并说一声 —— 裁错的后果是
# 模型看不到本段真正要用的镜头/状态，然后自己编一个，而这不报错。
def _seg_context(pj: Project, episode: str, segment: str,
                 log: Optional[Callable] = None) -> Optional[dict]:
    """这一段真正牵涉到哪些 shot / cvs / spatial。接不上返回 None（= 不裁）。"""
    def _why(msg: str):
        if log:
            log(f"  ⚠️ 按段裁剪没接上（{msg}），这一步按整集发 —— "
                f"输入会大很多，但不会少给上下文")
        return None

    me = next((s for s in (pj.stage_data("n10_segs", episode) or {}).get("segs") or []
               if isinstance(s, dict) and s.get("seg_id") == segment), None)
    if not me:
        return _why(f"环节10 的装箱表里没有 {segment}")
    want_shots = {x for x in (me.get("included_shots") or []) if x}
    if not want_shots:
        return _why(f"{segment} 没写 included_shots")
    shots = [s for s in (pj.stage_data("n9_shots", episode) or {}).get("shots") or []
             if isinstance(s, dict) and s.get("shot_id") in want_shots]
    if not shots:
        return _why("环节9 的镜头表里一个都对不上这些 shot_id")
    cvs_ids = {s.get("source_cvs") for s in shots if s.get("source_cvs")}
    cvs_ids |= {x for x in (me.get("entry_cvs"), me.get("exit_cvs")) if x}
    rows = [c for c in (pj.stage_data("n8_cvs", episode) or {}).get("cvs") or []
            if isinstance(c, dict) and c.get("cvs_id") in cvs_ids]
    if not rows:
        return _why("环节8 的状态表里一个都对不上这些 cvs_id")
    return {"shots": {s.get("shot_id") for s in shots},
            "cvs": {c.get("cvs_id") for c in rows},
            "spatial": {c.get("spatial_id") for c in rows if c.get("spatial_id")}}


def _keep(rows, pred) -> list:
    """按条件留一部分。**一条都没留下就整份保留** —— 空数组比多发更糟：
    下游看到「这一段没有任何镜头/状态」，然后自己编。"""
    if not isinstance(rows, list):
        return rows
    mine = [r for r in rows if isinstance(r, dict) and pred(r)]
    return mine or rows


def _narrow_join(out_name: str, obj: dict, ctx: Optional[dict]) -> dict:
    """按上面那条 ID 链把整集级产物裁到这一段。"""
    if not ctx or not isinstance(obj, dict):
        return obj
    if out_name == "n9_shots":
        keep = ctx["shots"]
        out = dict(obj, shots=_keep(obj.get("shots"),
                                    lambda r: r.get("shot_id") in keep))
        got = {r.get("shot_id") for r in out["shots"]}
        # 转场要两端都在本段才留：跨段的那条属于相邻段，发过来只会让模型
        # 以为本段要接一个它看不到的镜头
        out["transitions"] = _keep(
            obj.get("transitions"),
            lambda r: r.get("from_shot") in got and r.get("to_shot") in got)
        out["timing_plan"] = _keep(
            obj.get("timing_plan"),
            lambda r: not r.get("shot_id") or r.get("shot_id") in got)
        return out
    if out_name == "n8_cvs":
        keep = ctx["cvs"]
        out = dict(obj, cvs=_keep(obj.get("cvs"),
                                  lambda r: r.get("cvs_id") in keep))
        out["vt"] = _keep(obj.get("vt"),
                          lambda r: r.get("source_cvs") in keep
                          or r.get("target_cvs") in keep)
        return out
    if out_name == "n5_spatial" and ctx["spatial"]:
        keep = ctx["spatial"]
        return dict(obj,
                    spatial_masters=_keep(obj.get("spatial_masters"),
                                          lambda r: r.get("spatial_id") in keep),
                    loc_views=_keep(obj.get("loc_views"),
                                    lambda r: r.get("spatial_id") in keep))
    return obj


def _narrow(out_name: str, obj: dict, segment: str) -> dict:
    """跑某一段时，把按段索引的产物裁成只剩这一段。

    不裁的话，做 SEG01 会把这一集全部段落的装箱、场景状态图、故事板都发过去：
    一集十几段就是十几倍的输入，钱翻几倍；更糟的是模型会串段 ——
    看到别的段的内容，把那边的动作写进这一段。

    只裁按段索引的那几份。整集共享的（资产表、空间主表、镜头）不裁 ——
    它们本来就是这一段要用的上下文。
    """
    if out_name not in _SEG_INDEXED or not isinstance(obj, dict):
        return obj
    key, id_field = _SEG_INDEXED[out_name]
    rows = obj.get(key)
    if not isinstance(rows, list):
        return obj
    mine = [r for r in rows if isinstance(r, dict) and r.get(id_field) == segment]
    return dict(obj, **{key: mine})


def _capability_block(pj: Project) -> str:
    """给模板看的能力说明。没冻结过就明说，不要装作有。"""
    cap = capability_of(pj)
    if not cap:
        return ("（还没冻结能力档位。按最保守的来：只用 NATIVE_CUT、"
                "完全遮挡、光学覆盖这三类转场。）")
    lv = cap.get("native_multishot_support", "UNKNOWN")
    hint = {"RELIABLE": "这个模型能可靠地一次生成多镜头，六类机制都可以用。",
            "LIMITED": "多镜头能力有限：优先完全遮挡、黑场闪光失焦、简单甩镜，"
                       "减少跨人物跨地点的直接混合。",
            "UNSUPPORTED": "这个模型做不了多镜头。用单镜头连续的机位和走位表达，"
                           "或者在同一次生成里用完整遮挡完成变化。",
            "UNKNOWN": "模型的多镜头能力未知 —— **不要假设它行**，按有限档来。"}[lv]
    return (f"目标视频模型：{cap.get('target_video_model') or '未指定'}\n"
            f"多镜头能力：{lv} —— {hint}\n"
            f"**允许使用的转场机制（只能从这几类里选）**："
            f"{'、'.join(cap.get('allowed_mechanisms') or ['NATIVE_CUT'])}\n"
            f"执行模式：{cap.get('transition_execution_mode', 'MODEL_NATIVE_ONLY')}"
            f"（外部剪辑和后期补转场一律禁止；模型做不出来就降到更稳的机制，"
            f"不许改用后期）")


def _drop_path(obj, path: str):
    """按 `a[].b` 这种路径去掉一处。原对象不动。

    只支持「顶层键」和「数组里每一项的某个键」两种形状 —— 够用了，
    再往下嵌套的话这张表就没人看得懂了。
    """
    if not isinstance(obj, dict):
        return obj
    head, _, rest = path.partition(".")
    if not rest:
        return {k: v for k, v in obj.items() if k != head.rstrip("[]")}
    key = head.rstrip("[]")
    if key not in obj:
        return obj
    rows = obj[key]
    if head.endswith("[]") and isinstance(rows, list):
        return dict(obj, **{key: [_drop_path(r, rest) for r in rows]})
    return dict(obj, **{key: _drop_path(rows, rest)})


def split_scstates(obj):
    """把场景状态表分成「有图的」和「只有文字合同的」两组。

    **十二听十一**：以前这两类混在同一个数组里，只差一个 `decision` 字段 ——
    模型得自己记住「decision 是 LOGICAL_ONLY 的那几条没有 png、不许当参考图」。
    实测记不住：同一次运行里 SEG04 遵守了、SEG05 照样引了一条，
    然后故事板任务指向一个永远不会存在的文件。

    分开发之后这条规则不再依赖模型的记性 —— 它看到的是两个不同的清单，
    其中一个的名字就叫「不许当参考图」。
    """
    if not isinstance(obj, dict):
        return obj
    rows = obj.get("scstates")
    if not isinstance(rows, list) or not rows:
        return obj
    ok, only = [], []
    for sc in rows:
        (only if (isinstance(sc, dict) and scstate_no_image(sc)) else ok).append(sc)
    if not only:
        return obj                      # 没有文字合同，别多塞一个空数组
    out = dict(obj)
    out["scstates"] = ok
    out["scstates_logical_only"] = only
    out["_注"] = (
        "`scstates` 是**会出图**的那几条，只有它们可以出现在 reference_order 里。"
        "`scstates_logical_only` 是第十一环节判成只留文字合同的 —— "
        "**它们没有 png，永远不会有**，把它们写进 reference_order 就是引用一个"
        "不存在的文件，那一段的故事板做不出来。"
        "它们的 Source CVS、Zone、Anchor、Support、朝向照旧是权威，"
        "按那份文字合同去引原子资产（本段人物的当前造型 + 场景环境 + 关键道具）。")
    return out


def project_product(stage_id: str, out_name: str, obj):
    """把上游产物裁成这个环节真正需要的部分。

    见 system_v34.PRODUCT_NEEDS 里那段说明：不裁的代价是
    「同一份 2.8 万字发给 4 个下游、其中 3 个还是逐集的」，
    既把网关的输入上限试出来，又按集重复付钱。
    """
    # 场景状态表发给故事板之前先分组 —— 见 split_scstates。
    # 放在裁剪**之前**：裁剪只挑字段，不改结构。
    if out_name == "n11_scstate" and stage_id == "n12":
        obj = split_scstates(obj)
    spec = V.needs_of(stage_id, out_name)
    if not spec or not isinstance(obj, dict):
        return obj
    keep = spec.get("keep")
    if keep:
        return {k: obj[k] for k in keep if k in obj}
    for path in spec.get("drop") or []:
        obj = _drop_path(obj, path)
    return obj


def trim_saving(pj: Project, stage_id: str, episode: str = "") -> str:
    """这一步的按环节裁剪省了多少字。没裁就返回空。

    必须报出来。裁剪是「悄悄少发一部分输入」，裁错了模型不会报错、
    只会答得差一点 —— 那正是这个项目里最难查的一类问题。
    看得见省了多少，才有人会去核对裁得对不对。
    """
    parts = []
    for out_name, obj in deps_data(pj, stage_id, episode).items():
        if not V.needs_of(stage_id, out_name) or not obj:
            continue
        full, cut = len(jd(obj)), len(jd(project_product(stage_id, out_name, obj)))
        if full > cut:
            parts.append(f"{V.placeholder_of(out_name)} {full:,}→{cut:,} 字")
    if not parts:
        return ""
    return "按环节裁剪：" + "、".join(parts)


# 这一步写出来的提示词，最后是**谁**拿去出东西的 —— 参考图上限得按那一家算。
#
# 以前只有一个 `ref_limit`，取的是**视频链**首选那家的上限，然后发给所有环节。
# 于是第十二环节（它自己的参考图是给**出图**用的）被告知了视频那家的上限：
# 实遇一次出图是超模（9 张）、视频是派欧 seedance（30 张），
# 故事板被告知 30 —— 按 30 张引的话到出图那步撞上限，而那时提示词已经写好了。
#
# 一个数发给用途不同的两拨人，是「两个环节各自都对、凑起来做不出来」的又一种。
_LIMIT_CONSUMER = {
    "n4b": "image",     # 资产提示词 → 资产出图
    "n11": "image",     # 场景状态图提示词 → 出图
    "n12": "image",     # 故事板提示词 → 出图（它自己那几张参考图）
    "n13": "video",     # 视频提示词 → 出片
}


def _ref_limit_block(params: dict, stage_id: str = "") -> str:
    """本次生产的模型一次能吃几张参考图。

    这个数服务商注册表里一直有（灵感鸭 sora-2 只收 1 张，坤鸡 9 张），
    但从来没告诉过模型 —— 于是 LLM 按剧情需要引 5、6 张，
    到出图那步才撞上限。V5.6 明确要求把它写进提示词，
    并且强调**这是容量上限，不是推荐装满的数量**。

    拿不到就明说未知，别编一个数：编大了会让它多引，编小了会让它漏掉
    真正需要的覆盖图。
    """
    who = _LIMIT_CONSUMER.get(stage_id, "video")
    # 按用途取；取不到就回落到那个老的单一值（老配置里只有它）
    lim = params.get(f"ref_limit_{who}") or params.get("ref_limit")
    if not lim:
        return ("本次目标模型一次能吃几张参考图：未知。"
                "按最小充分集来，不确定就少传 —— 撞上限会直接失败，"
                "而多传一张互相打架不会报错，只会把画面搞坏。")
    return (f"本次目标模型一次最多接受 {lim} 张参考图。"
            f"这是**容量上限，不是推荐装满的数量** —— "
            f"按最小充分集挑，够用就停。"
            f"**额度先给故事板骨架，剩下的才是补图的** —— "
            f"骨架那几张不在可裁范围内，裁掉一张就是那段时间没人告诉模型"
            f"发生了什么；要裁只裁补图。"
            f"{'超过 5 张时必须逐张写清缺的是哪一项权威。' if lim > 5 else ''}")


def _script_for(pj: Project, stage_id: str, episode: str, params: dict) -> str:
    """环节1 吃整部剧本，逐集环节只吃本集正文。

    别给逐集环节发全剧剧本：40 集的本子发 40 遍，钱翻几十倍，
    而且模型会拿别的集的情节来填这一集。
    """
    if V.scope_of(stage_id) == "series":
        return params.get("script", "")
    if not episode:
        return ""
    try:
        return _eps.script_of(pj, episode)
    except Exception:                                   # noqa: BLE001
        return ""


def segments_of(pj: Project, episode: str) -> list:
    """这一集有哪些段。段是第十环节装箱装出来的，不是按秒数除出来的。"""
    segs = (pj.stage_data("n10_segs", episode) or {}).get("segs", [])
    return [s for s in segs if s.get("seg_id")]


def needs_script(stage_id: str, pj: Project = None) -> bool:
    """这个环节的模板里有没有 {{SCRIPT}}。看模板本身，不写死一张表。

    写死的话，有人在页面上给别的环节加了 {{SCRIPT}} 就查不到了。
    """
    tpl_name, _, _ = V.LLM_SPEC[stage_id]
    return "{{SCRIPT}}" in load_prompt(tpl_name, pj)


def check_inputs(pj: Project, stage_id: str, params: dict,
                 episode: str = "") -> None:
    """跑之前把这一步的输入验一遍，缺了就停，别拿空输入去调模型。

    实跑撞过：剧本没进去，提示词只剩模板（3246 字里 3177 字是模板本身），
    模型照着空输入吐了个 215 token 的骨架，缺 `characters[]`，
    于是 JSON 校验重试两次、三次调用全废。

    最糟的不是白花钱，是**报错指错了方向** —— 诊断给的是
    「换个更强的模型 / 剧本太长先拆开」，而真实情况正好相反：剧本是空的。
    照着那条建议去换模型，换十个也一样。

    `missing_deps` 查的是上游产物，剧本是**唯一没人查的输入**，这里补齐。
    """
    if not needs_script(stage_id, pj):
        return
    script = _script_for(pj, stage_id, episode, params)
    if script.strip():
        return
    where = "01_剧本与分段/原始剧本.txt" if V.scope_of(stage_id) == "series" \
        else f"{episode} 这一集的正文"
    raise RuntimeError(
        f"{stage_id} 要用剧本，但拿到的是空的（{where}）。"
        f"发出去的话提示词里只有模板、没有剧本，模型只能编一个空壳，"
        f"然后卡在「输出缺少必需字段」上白花三次调用 —— "
        f"而那个报错会让你以为是模型不行。"
        f"去「项目」页确认剧本正文在不在；"
        f"逐集环节的话看环节1 的切集结果有没有把这一集切空。")


def missing_deps(pj: Project, stage_id: str, episode: str = "") -> list:
    """跑之前先看依赖齐没齐，返回还缺哪几个环节（给人看的名字）。"""
    _, deps, _ = V.LLM_SPEC[stage_id]
    by_out = {s["out"]: s for s in V.STAGES if s.get("out")}
    data = deps_data(pj, stage_id, episode)
    miss = []
    for d in deps:
        if not data.get(d):
            s = by_out.get(d, {})
            miss.append(f"第{s.get('no', '?')}环节「{s.get('name', d)}」")
    return miss


# ---------------------------------------------------------------- 跑一个环节

def build_user(pj: Project, stage_id: str, params: dict,
               episode: str = "", segment: str = "",
               extra: Optional[dict] = None) -> str:
    """这个环节这一次实际会发出去的正文。

    `extra` 是分批跑用的：覆盖某几个占位符（这一批做哪几集/哪几个资产）。
    """
    tpl_name, _, _ = V.LLM_SPEC[stage_id]
    data = deps_data(pj, stage_id, episode)
    text = render(load_prompt(tpl_name, pj),
                  mapping(pj, stage_id, params, data, episode, segment, extra))
    # 模板可能被全局/本剧改写，改写版丢了 {{SUBTITLE_RULE}} 这一格的话
    # 收尾句口径就静默丢了（{{MEDIUM_RULE}} 那次的教训）。认特征串兜底，
    # 只在资产提示词环节补 —— 别的环节没有画面文字条款。
    if stage_id == "n4b" and "剧情本身要求的文字" not in text:
        from . import settings as _st
        text += ("\n\n【画面文字规则】（收尾句里的画面文字条款必须逐字转述这一段"
                 "—— 剧情本身要求的文字一律允许，禁的只有字幕、水印、UI 面板"
                 "和不属于剧情的叠加文字）\n" + _st.subtitle_rule(pj))
    if segment:
        text += (f"\n\n【只做这一段】{segment}，"
                 f"输出数组里只放这一段，不要带上别的段。")
    return text


def _n4b_worklist(pj: Project, a4: dict, batch: Optional[set] = None) -> dict:
    """n4b 的输入：**这一批要写的留全量，其余压成一行目录。**

    不能直接把已写过的删掉 —— 模型引用别的资产要靠 ID（LOOK 引 PH、
    CT 引 LOOK）。看不到就会重新发明一个 ID，出图那层再报「查不到这个资产」。
    所以留着，但只留 id/family/name 三个字段，一条几十字节而不是八百字符。

    `batch` 给了就只写这一批（分批跑用）；不给就是这一轮所有没写过的。

    **被这一批引用到的资产要留全量。** 写 LOOK 得看得见它引的那个 PH
    长什么样，只给个 ID 是写不出提示词的 —— 模型会自己编一个外观，
    于是同一个角色在不同批次里长得不一样，而这**不会报错**。

    这一步只改**发过去的内容**，不改环节顺序、不改产物结构。
    """
    if not isinstance(a4, dict):
        return a4
    if batch is None:
        batch = set(n4b_split(pj)[1])
    rows = [a for a in a4.get("assets") or [] if isinstance(a, dict)]
    keep = set(batch)
    for a in rows:                      # 这一批引到的，连同它们的父资产一起留全
        if a.get("asset_id") in batch:
            keep.update(x for x in (a.get("reference_assets") or []) if x)
            if a.get("parent_asset_id"):
                keep.add(a["parent_asset_id"])
    # 只有两栏 —— 模板就是按两栏解释的，多一栏模型不知道该拿它怎么办。
    #   assets[]              这一批要写的，全量
    #   assets_already_done[] 其余的；被这一批引用到的留全量，其它只留一行
    write, other = [], []
    for a in rows:
        aid = a.get("asset_id")
        if not aid:
            continue
        if aid in batch:
            write.append(a)
        elif aid in keep:
            other.append(a)
        else:
            other.append({k: a.get(k) for k in ("asset_id", "family", "name")
                          if a.get(k)})
    return dict(a4, assets=write, assets_already_done=other)


# 这些档位**不出图、也不写生产提示词**。
#
# V6.1 把 `decision` 从三档拆细：逻辑对象要完整登记，但登记 ≠ 出图。
# 简单服装走文字契约（logical_only）、中间动作交给视频执行（defer_to_video）、
# 已有 Canon 沿用编号（existing_canonical）—— 这三类都不需要生产提示词。
#
# 代码不认这些档位的后果很具体：模板判出来了，程序照样给它们写提示词，
# 那一步本来就顶着输出上限，白写的部分直接把它推过线。
NO_IMAGE_DECISIONS = frozenset({
    "skip", "logical_only", "defer_to_video", "existing_canonical", "deferred",
})


def n4b_split(pj: Project, episode: str = "") -> tuple:
    """资产表分三份：已经写过的 / 这次要写的 / 根本不用写的。

    返回 (已写的 id 列表, 要写的 id 列表, 不用写的条数)。

    为什么必须增量：n4b 是全剧级的，一次要写全剧所有资产的完整提示词。
    实测 V6.1 逐集是 17 条 / 14,144 字符 ≈ 8,320 token（装得下），
    而全剧级 4 集就 33,280 token —— 超过本机实测的输出天花板 19,612。
    不增量的话，截断之后重跑写的是同一批东西，永远走不完。

    增量之后每一轮只补没写的，截断也在推进。**不改变环节顺序**：
    n4b 还是一个环节、还在原位、产物文件名不变。

    过滤依据用**已存产物里的 prompt**，不用磁盘上的 txt：
    txt 是 write_prompt_files 写的，而那个函数只在逐集环节跑完才调
    （n4b 是全剧级，episode=""），所以 n4b 刚跑完那一刻 txt 还不存在 ——
    拿它当依据会把刚写好的又判成没写。
    """
    assets = (pj.stage_data("n4_assets", "") or {}).get("assets") or []
    written = {ap.get("asset_id") for ap in
               (pj.stage_data("n4b_asset_prompts", "") or {}).get("asset_prompts") or []
               if ap.get("asset_id") and (ap.get("prompt") or "").strip()}
    done, todo, dropped = [], [], 0
    for a in assets:
        aid = a.get("asset_id")
        if not aid:
            continue
        # skill 第五章：只为**当前范围实际需要**且视觉差异有生产价值的状态
        # 建资产。这几档出图那一层本来就会丢掉（见 build_tasks），
        # 在这里写一遍纯属把 token 花在注定要扔的东西上 ——
        # 而这一步恰恰是最容易被截断的那一步。
        if a.get("decision") in NO_IMAGE_DECISIONS:
            dropped += 1
        elif aid in written:
            done.append(aid)
        else:
            todo.append(aid)
    return done, todo, dropped


def merge_asset_prompts(previous: dict, fresh: dict) -> dict:
    """按 asset_id 合并。增量跑必须合并，不能用一批补写覆盖整份。

    和 V6.1 的 merge_s5_outputs 是同一件事，逻辑照抄 —— 两套体系的
    资产提示词都是「全剧一份、分几次写完」，合并规则没有体系差异。
    """
    previous, fresh = previous or {}, fresh or {}
    merged = dict(previous)
    merged.update(fresh)
    rows, pos = [], {}
    for ap in previous.get("asset_prompts") or []:
        aid = ap.get("asset_id")
        if not aid or aid in pos:
            continue
        pos[aid] = len(rows)
        rows.append(ap)
    for ap in fresh.get("asset_prompts") or []:
        aid = ap.get("asset_id")
        if not aid:
            continue
        if aid in pos:
            rows[pos[aid]] = ap          # 重写覆盖旧的那一条
        else:
            pos[aid] = len(rows)
            rows.append(ap)
    merged["asset_prompts"] = rows
    return merged


# ====================================================================== 分批
#
# n3 和 n4b 都是「全剧一次」，而全剧一次的输出量随剧的长度线性涨 ——
# 涨过这条线路能一次吐完的量，就再也跑不过去了：重试写的是同一批东西，
# 每次断在差不多的地方，钱花掉、进度是零。实跑连撞三次。
#
# 分批**不改环节图**：n3 还是 n3、还在原位、产物文件名不变、下游读法不变。
# 变的只有一件事 —— 这一个环节内部分几次调用，每次存盘。
# 于是断在第三批，前两批是留下的，再点一次从第三批接着跑。
#
# 两者的分批单位不一样，不能套同一套：
#   n3   按集。场次天生带 episode 字段，下游本来就按集裁。
#   n4b  按资产。资产之间没有顺序依赖（提示词只是互相引 ID），随便切。
_N3_BATCH_NOTE = "按集分批"
_N4B_BATCH = 12          # 一批写几个资产的提示词
#
# 12 是这么来的：实测 V6.1 逐集 17 条资产 ≈ 8,320 token 输出（约 490/条），
# 而本机见过的最大一次输出是 19,612 token。12 条 ≈ 5,900，留了足够余量 ——
# 顶到天花板才是最贵的失败（整批白跑）。宁可多调几次。


def n3_split(pj: Project) -> tuple:
    """n3 分批：哪几集已经排过场次了，哪几集还没。

    依据是**已存产物里的 scenes**，不是磁盘上的别的东西 ——
    scene 自带 episode 字段，这是唯一可靠的「这一集做过没有」。
    """
    eps = _eps.ids(pj)
    have = {s.get("episode") for s in
            (pj.stage_data("n3_narrative", "") or {}).get("scenes") or []
            if isinstance(s, dict) and s.get("episode")}
    done = [e for e in eps if e in have]
    todo = [e for e in eps if e not in have]
    return done, todo


def stamp_episode(ep: str, fresh: dict) -> dict:
    """把这一批的 `scene_id` / `beat_id` 打上集号前缀。

    **这是并发的前提。** 顺序跑时靠「编号接着往下走」那句话让模型自己续号；
    并发之后每一批都看不到前面，21 集各自从 SC01 编起 ——
    而 `merge_narrative` 是按 `scene_id` 合并的，同号就覆盖：
    **21 集并发跑完可能只剩一集的场次，而且不报错。**

    所以不能靠模型自觉。模板里也要求了带前缀，这里再兜一道：
    已经带了的不动，没带的补上，`beats` 里指向旧号的一起改。
    """
    scenes = [s for s in (fresh or {}).get("scenes") or [] if isinstance(s, dict)]
    if not ep or not scenes:
        return fresh
    pre = f"{ep}-"
    rename = {}
    for s in scenes:
        old = str(s.get("scene_id") or "").strip()
        if not old or old.startswith(pre):
            continue
        rename[old] = pre + old
        s["scene_id"] = pre + old
    if not rename:
        return fresh
    for b in (fresh or {}).get("beats") or []:
        if not isinstance(b, dict):
            continue
        sid = str(b.get("scene_id") or "").strip()
        if sid in rename:
            b["scene_id"] = rename[sid]
        bid = str(b.get("beat_id") or "").strip()
        # `SC01-B1` → `EP01-SC01-B1`：只在它确实以旧场次号开头时替换，
        # 免得把别的编号规则改花
        for old, new in rename.items():
            if bid.startswith(old):
                b["beat_id"] = new + bid[len(old):]
                break
        else:
            if bid and not bid.startswith(pre):
                b["beat_id"] = pre + bid
    return fresh


def merge_narrative(previous: dict, fresh: dict) -> dict:
    """n3 分批产物合并。**必须合并，不能覆盖。**

    和 merge_asset_prompts 是同一个道理：这一批只做了一集，
    直接存盘会把前面几集排好的场次全冲掉 —— 而且不报错，
    只是「场次越跑越少」，要到第七环节才发现某一集根本没有戏。
    """
    merged = dict(previous or {})
    for key, id_field in (("scenes", "scene_id"), ("beats", "beat_id"),
                          ("episode_arcs", "episode")):
        rows = [r for r in (merged.get(key) or []) if isinstance(r, dict)]
        pos = {r.get(id_field): i for i, r in enumerate(rows) if r.get(id_field)}
        for r in (fresh or {}).get(key) or []:
            if not isinstance(r, dict) or not r.get(id_field):
                continue
            rid = r[id_field]
            if rid in pos:
                rows[pos[rid]] = r          # 重跑同一集时覆盖那一集的旧条目
            else:
                pos[rid] = len(rows)
                rows.append(r)
        merged[key] = rows
    merged["scope"] = "full_series"
    # boundary_note 是一段话不是数组，按行累加。**不能覆盖**：
    # 它记的是「哪几处场次边界是判断题」，每一批各有各的判断，
    # 后一批盖掉前一批等于把前面几集的判断依据丢了。
    old = str((previous or {}).get("boundary_note") or "").strip()
    new = str((fresh or {}).get("boundary_note") or "").strip()
    lines = [x for x in old.split("\n") if x.strip()]
    if new and new not in lines:
        lines.append(new)
    merged["boundary_note"] = "\n".join(lines)
    return merged


def _n3_done_block(pj: Project, done: list) -> str:
    """已经排过的那几集，压成一行一场的目录发回去。

    为什么要发：模板原本一次看全剧，靠的就是「第 5 集能看到第 1 集埋的伏笔」。
    分集之后这个视野没了 —— 不补回来，后面几集会重新编号、
    `entry_state` 接不上上一集的 `exit_state`、前面留的
    `unresolved_tension` 再也没人收。**这三样都不报错。**

    只发摘要不发全文：全文发回去等于把省下的输入又加回来。
    """
    scenes = [s for s in (pj.stage_data("n3_narrative", "") or {}).get("scenes") or []
              if isinstance(s, dict) and s.get("episode") in set(done)]
    if not scenes:
        return "（这是第一批，前面没有已排好的集）"
    lines = []
    for s in scenes:
        lines.append(
            f"{s.get('scene_id', '?')}　{s.get('episode', '?')}　"
            f"目标：{s.get('objective', '')}　结果：{s.get('outcome', '')}　"
            f"收场状态：{s.get('exit_state', '')}　"
            f"未了结：{s.get('unresolved_tension', '') or '无'}")
    return ("已经排好的场次（**不要重排、不要重复编号**，"
            "新场次的编号接着往下走；这一集的第一场 `entry_state` "
            "要接得住上面最后一场的 `exit_state`；上面的「未了结」"
            "该在本集回收的要回收）：\n" + "\n".join(lines))


def _one_call(pj: Project, stage_id: str, *, llm, params: dict, episode: str = "",
              extra: Optional[dict] = None, label: str = "",
              log: Callable = print, cancel: Optional[Callable] = None) -> dict:
    """发一次请求。分批跑就是把这个函数调多次，每次换 extra。"""
    _, _, required = V.LLM_SPEC[stage_id]
    user = build_user(pj, stage_id, params, episode, extra=extra)
    tag = label or (f"{episode} " if episode else "全剧 ")
    log(f"{tag}{stage_id} 提示词 {len(user)} 字，调 {llm.model}")
    saved = trim_saving(pj, stage_id, episode)
    if saved:
        log(f"  {saved}")
    with LLM_GATE.slot():
        return llm.json_call(system_prompt(pj, params), user, required=required,
                             log=log, cancel=cancel,
                             on_usage=_usage(pj, stage_id, episode),
                             on_partial=keep_partial(pj, stage_id, episode,
                                                     label.strip(), llm=llm))


def run_n3_batched(pj: Project, *, llm, params: dict, log: Callable = print,
                   cancel: Optional[Callable] = None,
                   concurrency: int = 1) -> dict:
    """第三环节按集分批，每集存盘。`concurrency > 1` 时按集并发。

    **并发的前提是编号不撞。** 顺序跑时靠「编号接着往下走」让模型自己续号；
    并发之后每一批看不到前面，各集都从 SC01 编起，而合并是按 `scene_id`
    做的 —— 同号就覆盖，21 集跑完可能只剩一集，且不报错。
    所以 `stamp_episode` 会把每一批的编号打上集号前缀，程序保证不撞。

    并发丢掉的是**集与集接缝处那一下状态交接**（上一集 `exit_state` →
    这一集 `entry_state`），以及「前面留的伏笔」那份提示。
    没丢的是跨集因果本身 —— 每一批都收到完整的【故事真相】，
    「第 1 集埋的东西在第 5 集哪一场收」写在那份事件表里。

    每集跑完就存：断在第三集，前两集是留下的，再点一次只补没排的。
    """
    eps = _eps.ids(pj)
    if not eps:
        # 还没切集（老项目、或者第一环节没给出 episode_ranges）。
        # 退回原来的一次全剧 —— 分批是为了跑得过去，不是为了强制换做法。
        log("  没有分集信息，按原来的一次全剧跑")
        return _one_call(pj, "n3", llm=llm, params=params, log=log, cancel=cancel)

    done, todo = n3_split(pj)
    if done:
        log(f"  {len(done)} 集已经排过场次，这次不重排：{'、'.join(done)}")
    if not todo:
        log("  所有集都排过了，直接跳过（不调模型、不花钱）")
        prev = pj.stage_data("n3_narrative", "") or {"scenes": [], "beats": []}
        diagnose.clear(pj.root, "stage:n3", "全剧")
        return prev
    workers = max(1, min(int(concurrency or 1), len(todo)))
    log(f"  按集分批：这次要排 {len(todo)} 集（{'、'.join(todo)}），"
        + (f"一集一次调用，{workers} 集并发" if workers > 1 else "一集一次调用"))

    # 已经排好的那几集（上一轮跑完的）——并发时这一份是**开跑前的快照**，
    # 本轮各批互相看不见。顺序跑时它会随着每一批更新。
    done_block = _n3_done_block(pj, done)
    all_eps = done + todo          # 「全剧共几集」——固定住，别被下面的累加改花
    box = {"out": pj.stage_data("n3_narrative", "") or {}}
    lock = threading.Lock()
    failed: list = []

    def one(i: int, ep: str) -> None:
        if cancel and cancel():
            return
        try:
            fresh = _one_call(
                pj, "n3", llm=llm, params=params, episode=ep,
                label=f"[{i}/{len(todo)}] {ep} ",
                extra={"SCRIPT": _eps.script_of(pj, ep),
                       "BATCH_SCOPE": _n3_batch_scope(ep, all_eps),
                       # 顺序跑时每批都拿最新的；并发时只有开跑前那份
                       "DONE_SCENES": (_n3_done_block(pj, done) if workers == 1
                                       else done_block)},
                log=log, cancel=cancel)
        except LLMCancelled:
            raise
        except Exception as exc:                            # noqa: BLE001
            # 一集失败不拖累别的集 —— 记下来，最后一起报
            d = diagnose.build(exc, stage="stage:n3", target=ep, model=llm.model)
            diagnose.record(pj.root, d)
            with lock:
                failed.append(ep)
            log(f"  {ep} 没排成：{diagnose.one_line(d)}")
            return
        # 打集号前缀 → 合并 → 落盘。**这三步必须在同一把锁里**：
        # 合并是读-改-写，两集同时保存会把先写的那一集挤掉。
        with lock:
            box["out"] = merge_narrative(box["out"], stamp_episode(ep, fresh))
            pj.save_stage("n3_narrative", box["out"], "")
            n = len([s for s in box["out"].get("scenes") or []
                     if s.get("episode") == ep])
            # 顺序跑时把这一集加进「已排好的」——下一批的【已排好的场次】
            # 要看得到它（编号接着走、entry_state 接上）。
            # 并发时这一份用的是开跑前的快照，各批互相看不见。
            if workers == 1:
                done.append(ep)
        log(f"  {ep} 排好 {n} 场")

    jobs = list(enumerate(todo, 1))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda a: one(*a), jobs))
    else:
        for a in jobs:
            one(*a)

    out = box["out"]
    if cancel and cancel():
        raise LLMCancelled(
            f"第三环节已按取消停下，排好的都存了盘；"
            f"再点一次「开始」只补没排的那几集。")
    if failed:
        raise RuntimeError(
            f"有 {len(failed)} 集没排成：{'、'.join(failed[:8])}"
            f"{'…' if len(failed) > 8 else ''}。"
            f"排好的 {len(todo) - len(failed)} 集已经存盘，"
            f"再点一次「开始」只补没排的这几集。")
    diagnose.clear(pj.root, "stage:n3", "全剧")
    return out


def _n3_batch_scope(ep: str, all_eps: list) -> str:
    return (f"**这一次只做 {ep} 这一集。** 全剧共 {len(all_eps)} 集"
            f"（{'、'.join(all_eps)}），其余各集另有批次，不要替它们排场次 ——"
            f"输出的 `scenes` 和 `beats` 里只能有 {ep} 的内容，"
            f"每一条的 `episode` 都必须是 {ep}。`episode_arcs` 同理，只写这一集。")


def run_n4b_batched(pj: Project, *, llm, params: dict, log: Callable = print,
                    cancel: Optional[Callable] = None) -> dict:
    """第四环节（下）按资产分批，每批存盘。

    资产之间没有生产顺序依赖（提示词只是互相引 ID，引到的那几条会带全量
    发过去），所以切在哪儿都行 —— 按 n4 给的顺序切成固定大小就够了。
    """
    done, todo, dropped = n4b_split(pj)
    if dropped:
        log(f"  {dropped} 个资产不需要出图（skip / 逻辑契约 / 交给视频 / "
            f"已有 Canon），不写提示词"
            f"（出图那一层本来也会丢掉，写了是白写）")
    if done:
        log(f"  {len(done)} 个资产已经写过提示词，这次不重写")
    if not todo:
        log("  没有新资产要写提示词，直接跳过（不调模型、不花钱）")
        prev = pj.stage_data("n4b_asset_prompts", "") or {"asset_prompts": []}
        diagnose.clear(pj.root, "stage:n4b", "全剧")
        return prev

    batches = [todo[i:i + _N4B_BATCH] for i in range(0, len(todo), _N4B_BATCH)]
    log(f"  这次要写 {len(todo)} 个，分 {len(batches)} 批"
        f"（每批最多 {_N4B_BATCH} 个）")

    out = pj.stage_data("n4b_asset_prompts", "") or {}
    for i, ids in enumerate(batches, 1):
        if cancel and cancel():
            raise LLMCancelled(
                f"第四环节（下）已按取消停下，写好的都存了盘；"
                f"再点一次「开始」只补没写的那些资产。")
        log(f"  第 {i}/{len(batches)} 批：{'、'.join(ids)}")
        fresh = _one_call(
            pj, "n4b", llm=llm, params=params,
            label=f"[{i}/{len(batches)}] ", extra={"_n4b_batch": set(ids)},
            log=log, cancel=cancel)
        # **必须合并，不能覆盖。** 模型回来的只有这一批，
        # 直接存盘会把前几批写好的全冲掉 —— 而且不报错，只是越跑越少。
        out = merge_asset_prompts(out, fresh)
        pj.save_stage("n4b_asset_prompts", out, "")
    diagnose.clear(pj.root, "stage:n4b", "全剧")
    return out


def _n8_scope(batch: list, total: int, has_prev: bool) -> str:
    """第八环节这一批的范围说明（模板顶上那行引言）。"""
    names = "、".join(batch)
    head = (f"**这一次只写 {names} 这 {len(batch)} 场的 CVS 和 VT。**"
            f"全集共 {total} 场，其余各场另有批次，不要替它们写 —— "
            f"输出里只能有这几场的内容。")
    if not has_prev:
        return head
    return head + (
        " 跨场 VT 归后一批：从上一批末尾那条 CVS 接进你这一批第一条 CVS 的"
        "那条 VT 由你写（source_cvs 用【上一批末尾的 CVS】里那条的编号，"
        "target_cvs 是你自己新写的）；你这一批最后一场到再下一场的那条 VT"
        "不归你写（下一批会写）。批内场与场之间的 VT 照常写。")


def _n9_scope(batch: list, total: int, episode: str, offset: float,
              est: float, first_shot: int, first_tr: int) -> str:
    """第九环节这一批的范围说明。分批时它**覆盖**模板第〇/五章的
    「铺满全集」口径 —— 不写清楚的话模型会试着把全集塞进这一批。"""
    names = "、".join(batch)
    head = (f"**这一次只给 {names} 这 {len(batch)} 场设计镜头、转场和时间计划。**"
            f"全集共 {total} 场，其余各场另有批次，不要替它们排。")
    tail = ("上面第〇、五章说的「铺满本集总时长」在分批时按这一条执行："
            f"你只铺你这几场，**timing_plan 从 0 秒开始**，铺到这几场演完为止")
    if est > 0:
        tail += f"（按拍数估约 {est:g} 秒，只是参考，以这几场演完为准）"
    tail += "。合并时程序会自动把你的时间轴接到前面的批次后面。\n"
    if offset > 0:
        tail += (f"前面的批次已经把时间轴铺到第 {offset:g} 秒；"
                 f"从上一批最后一镜切进你第一镜的那条转场**归你写**"
                 f"（from_shot 用【上一批最后一镜】里那条的编号，"
                 f"它的时间算进你第一镜的 transition_time_range）。\n")
    tail += (f"镜头编号从 SH_{episode}_{first_shot:03d} 接着编，"
             f"转场编号从 TR_{episode}_{first_tr:03d} 接着编。")
    return head + "\n" + tail


def _prev_cvs_block(out: dict) -> str:
    """第八环节分批的【上一批末尾的 CVS】。第一批时是一句说明。"""
    last = _last_cvs_of(out)
    if not last:
        return "（这是第一批，前面没有已写好的场次。）"
    return ("上一批最后一条 CVS 的原文。你这一批第一条 VT 的 source_cvs "
            "用它的编号，第一条 CVS 的 entry_condition 要接得住它的 "
            "exit_condition：\n" + jd(last))


def _last_shot_block(out: dict, offset: float) -> str:
    """第九环节分批的【上一批最后一镜】。第一批时是一句说明。"""
    shot = _last_shot_of(out)
    if not shot:
        return "（这是第一批，前面没有镜头。）"
    timing = next((r for r in (out or {}).get("timing_plan") or []
                   if isinstance(r, dict)
                   and str(r.get("shot_id") or "") == str(shot.get("shot_id"))),
                  None)
    parts = [f"上一批最后一镜的原文（时间轴已铺到第 {offset:g} 秒；你第一镜要"
             f"接得住它的 exit_action，从它切进来的转场归你写）：\n" + jd(shot)]
    if timing:
        parts.append("它的时间计划行：\n" + jd(timing))
    return "\n\n".join(parts)


def run_n8_batched(pj: Project, *, llm, params: dict, episode: str,
                   log: Callable = print,
                   cancel: Optional[Callable] = None) -> dict:
    """第八环节按场分批，每批存盘。

    整集一次的问题是**两头一起超**：输入里整集的走位全量发出去，
    输出里整集的 CVS/VT 一次吐回来 —— 场次多的集两头都顶到上限。
    按场切批之后每批只看自己那几场的走位、只写自己那几场的 CVS，
    跨场的 VT 归后一批写（它要写的终点 CVS 就在自己这批里）。

    每批跑完就存盘：断在第二批，第一批是留下的，
    再点一次只补没写的那几场。
    """
    scenes = _scenes_of(pj, episode)
    out = pj.stage_data("n8_cvs", episode) or {}
    done, todo = n8_scene_split(pj, episode)
    if out and not done:
        # 产物在但一条都归不上场（老项目 / 模型没按模板的编号格式写）：
        # 当整集已做完 —— 归不上就永远「没做完」，每点一次「继续」
        # 都重跑整集，白烧钱。部分归得上的才按场判。
        log("  产物里的 CVS 一条都归不上场（编号里没写场号），"
            "按整集已做完跳过（不调模型、不花钱）")
        diagnose.clear(pj.root, "stage:n8", episode)
        return out
    if done:
        log(f"  {len(done)} 场已有 CVS，这次不重写：{'、'.join(done)}")
    if not todo:
        log("  所有场的 CVS 都写好了，直接跳过（不调模型、不花钱）")
        diagnose.clear(pj.root, "stage:n8", episode)
        return out
    batches = _n8_batches(pj, todo)
    log(f"  按场分批：这次写 {len(todo)} 场，分 {len(batches)} 批"
        f"（{' / '.join('、'.join(b) for b in batches)}）")
    for i, batch in enumerate(batches, 1):
        if cancel and cancel():
            raise LLMCancelled(
                f"{episode} 第八环节已按取消停下，写好的都存了盘；"
                f"再点一次「开始」只补没写的那几场。")
        fresh = _one_call(
            pj, "n8", llm=llm, params=params, episode=episode,
            label=f"[{i}/{len(batches)}] ",
            extra={"_scene_batch": set(batch),
                   "BATCH_SCOPE": _n8_scope(batch, len(scenes),
                                            _last_cvs_of(out) is not None),
                   "PREV_CVS": _prev_cvs_block(out)},
            log=log, cancel=cancel)
        # **必须合并，不能覆盖**：这一批只写了自己那几场。
        # keep_refs = 上一批末尾 CVS 的真实编号（PREV_CVS 里给过模型）：
        # 模型重启编号撞上它时，指向**旧行**的那条跨场 VT 引用不能被改走。
        last = _last_cvs_of(out)
        out = merge_cvs(out, fresh, episode,
                        keep_refs={str(last.get("cvs_id"))} if last else set())
        pj.save_stage("n8_cvs", out, episode)
        log(f"  第 {i}/{len(batches)} 批（{('、'.join(batch))}）写好，"
            f"累计 {len(out.get('cvs') or [])} 条 CVS、"
            f"{len(out.get('vt') or [])} 条 VT")
    diagnose.clear(pj.root, "stage:n8", episode)
    return out


def run_n9_batched(pj: Project, *, llm, params: dict, episode: str,
                   log: Callable = print,
                   cancel: Optional[Callable] = None) -> dict:
    """第九环节按时窗分批，每批存盘。

    这一环是全链路里**单次输出最大**的一步：一镜 20 多个字段，
    一集 20~40 镜的大 JSON 在中转上随机断流（实跑三次 LLM_SCHEMA_FAIL
    都是它）。按预估时窗切批，一批约一个 SEG 容器的秒数、5~12 镜 ——
    一次吐得稳的量。批间续传末镜原文和时间轴终点：每批的 timing_plan
    从 0 铺起，合并时程序加偏移接上；跨批转场归后一批。

    每批跑完就存盘：断在第二批，第一批是留下的，
    再点一次只补没排的那几场。
    """
    scenes = _scenes_of(pj, episode)
    out = pj.stage_data("n9_shots", episode) or {}
    done, todo = n9_scene_split(pj, episode)
    if out and not done:
        # 同第八环节：一条都归不上场的产物当整集已做完，不重排。
        log("  产物里的镜头一条都归不上场（scene_id 和 source_cvs "
            "都对不上场号），按整集已做完跳过（不调模型、不花钱）")
        diagnose.clear(pj.root, "stage:n9", episode)
        return out
    if done:
        log(f"  {len(done)} 场已有镜头，这次不重排：{'、'.join(done)}")
    if not todo:
        log("  所有场的镜头都排好了，直接跳过（不调模型、不花钱）")
        diagnose.clear(pj.root, "stage:n9", episode)
        return out
    batches = _n9_windows(pj, episode, params, todo)
    log(f"  按时窗分批：这次排 {len(todo)} 场，分 {len(batches)} 批"
        f"（{' / '.join('、'.join(b) for b, _ in batches)}）")
    for i, (batch, est) in enumerate(batches, 1):
        if cancel and cancel():
            raise LLMCancelled(
                f"{episode} 第九环节已按取消停下，排好的都存了盘；"
                f"再点一次「开始」只补没排的那几场。")
        offset = _plan_seconds(out)
        n_shots = len([s for s in out.get("shots") or [] if isinstance(s, dict)])
        n_tr = len([t for t in out.get("transitions") or []
                    if isinstance(t, dict)])
        prev_shot = _last_shot_of(out)
        fresh = _one_call(
            pj, "n9", llm=llm, params=params, episode=episode,
            label=f"[{i}/{len(batches)}] ",
            extra={"_scene_batch": set(batch),
                   "BATCH_SCOPE": _n9_scope(batch, len(scenes), episode,
                                            offset, est, n_shots + 1, n_tr + 1),
                   "LAST_SHOT": _last_shot_block(out, offset)},
            log=log, cancel=cancel)
        # protect = 上一批最后一镜的真实编号（LAST_SHOT 里给过模型）：
        # 模型重启编号撞上它时，指向**旧行**的跨批转场引用不能被改走。
        _renumber_batch_shots(
            fresh, episode, n_shots + 1, n_tr + 1,
            protect={str(prev_shot.get("shot_id"))} if prev_shot else set())
        _shift_batch_timing(fresh, offset, log)
        out = merge_shots(out, fresh)
        # 在「合并到目前」的这一份上查时间线：批内跳秒、批间接不上
        # 都在这里拦。拦住时这一批不落盘 —— 再点一次只重排这一批
        check_timeline(pj, "n9", out, params, episode, log)
        pj.save_stage("n9_shots", out, episode)
        log(f"  第 {i}/{len(batches)} 批（{('、'.join(batch))}）排好，"
            f"累计 {len(out.get('shots') or [])} 镜、时间轴铺到 "
            f"{_plan_seconds(out):g} 秒")
    check_runtime(pj, "n9", out, params, episode, log)
    diagnose.clear(pj.root, "stage:n9", episode)
    return out


def run_stage(pj: Project, stage_id: str, *, llm, params: dict,
              episode: str = "", log: Callable = print,
              cancel: Optional[Callable] = None,
              concurrency: int = 1) -> dict:
    """跑一个全剧级或逐集级的 LLM 环节。逐段的走 run_segment_stage。

    `concurrency` 目前只有 n3 用得上（它按集分批，各集互不依赖）。
    真正的总闸门在 LLM_GATE 上 —— 这里给的是这一个环节自己开几路。
    """
    if V.scope_of(stage_id) == "segment":
        raise ValueError(f"{stage_id} 是逐段环节，该走 run_segment_stage")
    tpl_name, _, required = V.LLM_SPEC[stage_id]
    miss = missing_deps(pj, stage_id, episode)
    if miss:
        raise RuntimeError(f"{stage_id} 的前置还没跑：{'、'.join(miss)}")
    check_inputs(pj, stage_id, params, episode)

    # 这两个环节的输出量随剧的长度线性涨，全剧一次跑不过去 —— 分批跑。
    # 它们自己负责存盘和合并，不走下面的单次路径。
    if stage_id == "n3":
        return run_n3_batched(pj, llm=llm, params=params, log=log, cancel=cancel,
                              concurrency=concurrency)
    if stage_id == "n4b":
        return run_n4b_batched(pj, llm=llm, params=params, log=log, cancel=cancel)
    # 第八/九环节场次多时按场/时窗分批（整集一次两头都容易超）。
    # 一场（或没切出场次）的集没有分批的意义，走下面的单次路径。
    if stage_id in ("n8", "n9") and episode \
            and len(_scenes_of(pj, episode)) > 1:
        runner = run_n8_batched if stage_id == "n8" else run_n9_batched
        return runner(pj, llm=llm, params=params, episode=episode,
                      log=log, cancel=cancel)

    out = _one_call(pj, stage_id, llm=llm, params=params, episode=episode,
                    log=log, cancel=cancel)
    check_runtime(pj, stage_id, out, params, episode, log)
    check_timeline(pj, stage_id, out, params, episode, log)
    check_packing(pj, stage_id, out, params, episode, log)
    pj.save_stage(tpl_name, out, "" if V.scope_of(stage_id) == "series" else episode)
    diagnose.clear(pj.root, f"stage:{stage_id}", episode or "全剧")
    if stage_id == "n1":
        _split_episodes(pj, out, params, log)
    return out


# 本集时长和实际排出来的时长，差多少算「压过头了」。
# 给 25% 的余量：镜头时长是估的，取整、转场占时、最后一镜收尾都会有出入。
# 但整集被压进一个容器时差的是好几倍，这个阈值抓得住，又不会天天误报。
_TIME_TOL = 0.75


def check_runtime(pj: Project, stage_id: str, out: dict, params: dict,
                  episode: str, log: Callable = print) -> None:
    """第九环节排出来的总时长，和这一集该有多长，对得上吗。

    这是「不报错、只是错」里最贵的一种，实跑撞过整轮：
    第九环节只拿得到容器的 15 秒，就把 8 个场次压成 15 秒的
    「高密度因果蒙太奇」—— 而且它**知道**装不下，自己在
    shot_count_rationale 里写了「完整实时演出远超15秒」，还是压了。

    压完之后一路不报错：第十环节照 15 秒装出 1 个 SEG，第十二环节
    在一张纸上画 16 格（模板上限 9 格），模型记不住 8 个场次的世界状态，
    于是所有格子的 source_scstate 全填第一个、道具状态和 CVS 互相打架。
    要到第十四环节审计才有人说话，而那时候已经跑了十几个环节。

    所以在这里就停 —— 时间对不上，往下每一步都在错的前提上工作。
    """
    if stage_id != "n9":
        return
    want = _ep_seconds(pj, episode, params)
    if not want:
        return                      # 老项目没存本集秒数，没得比
    got = _plan_seconds(out)
    if not got:
        return                      # 没给时间计划是另一回事，schema 那层管
    if got >= want * _TIME_TOL:
        return
    clip = int(params.get("duration") or 15) or 15
    # **只记不拦。** 这条检查（压缩超过 25% 就停）是本地经验规则，
    # skill 里没有任何对应条文 —— 而 duration 的语义本来就容易配错
    # （实跑：项目参数 15 秒、装箱 12 段共 180 秒），硬停整个环节太重。
    # 审计（n14）本来就会报同一件事，那条现在是提醒。
    diagnose.record(pj.root, diagnose.warn(
        "RUNTIME_SQUEEZED",
        f"{episode} 第九环节排成 {got:g} 秒，而这一集按剧情该有 {want} 秒 —— "
        f"压掉了 {100 - got * 100 / want:.0f}%。"
        f"最常见的原因是把 SEG 容器的 {clip} 秒当成了整集预算："
        f"{clip} 秒是视频模型一次最多生成多久，{want} 秒是这一集多长。",
        stage=f"stage:{stage_id}", target=episode or "全剧"))
    if log:
        log(f"⚠️ 这一集排成 {got:g} 秒，按剧情该有 {want} 秒（压掉 "
            f"{100 - got * 100 / want:.0f}%）—— 已记下，不挡后面")
    return


# 相邻镜头之间允许差多少秒。取整、四舍五入本来就会有零点几秒的出入，
# 而真正的跳秒是好几秒 —— 用户实遇的那条从 24 跳到 30。
_GAP_TOL = 0.35


def check_timeline(pj: Project, stage_id: str, out: dict, params: dict,
                   episode: str, log: Callable = print) -> None:
    """第九环节排的时间线，**中间不许跳秒，也不许叠在一起。**

    用户原话：「一直跳时间」。跳的是这个：

        SH_EP01_003  16.0 - 24.0
        SH_EP01_004  30.0 - 38.0      ← 24 到 30 这 6 秒没有任何镜头

    跳掉的那几秒既没有画面也没有人负责，而下游没有一处会发现：
    第十环节按 `included_shots` 装箱（它只看镜头号，不看秒数），
    第十二、十三环节按段拿数据。要到成片拼出来才看得见 —— 那时候
    每一段都是单独生成的，中间少几秒表现为**动作接不上**，
    而不是一段黑屏，所以连肉眼都不容易认出来是时间线的问题。

    叠在一起是另一头：两个镜头占同一段时间，那段内容会被做两遍
    （两次生成、两次付费），拼起来是同一件事演两回。

    容许 0.35 秒的出入 —— 取整和四舍五入本来就会有零点几秒的差。
    """
    if stage_id != "n9":
        return
    rows = [r for r in (out or {}).get("timing_plan") or []
            if isinstance(r, dict)
            and isinstance(r.get("start"), (int, float))
            and isinstance(r.get("end"), (int, float))]
    if len(rows) < 2:
        return                      # 没有相邻关系可查

    rows.sort(key=lambda r: float(r["start"]))
    gaps, overlaps, backwards = [], [], []
    for a, b in zip(rows, rows[1:]):
        ae, bs = float(a["end"]), float(b["start"])
        d = bs - ae
        if d > _GAP_TOL:
            gaps.append((a.get("shot_id") or "?", b.get("shot_id") or "?", ae, bs))
        elif d < -_GAP_TOL:
            overlaps.append((a.get("shot_id") or "?", b.get("shot_id") or "?", bs, ae))
    for r in rows:
        if float(r["end"]) < float(r["start"]) - _GAP_TOL:
            backwards.append((r.get("shot_id") or "?",
                              float(r["start"]), float(r["end"])))
    if not gaps and not overlaps and not backwards:
        return

    lines = []
    for a, b, ae, bs in gaps[:6]:
        lines.append(f"  · {a} 在第 {ae:g} 秒结束，下一个 {b} 从第 {bs:g} 秒开始"
                     f" —— 中间 {bs - ae:g} 秒没有任何镜头")
    for a, b, bs, ae in overlaps[:4]:
        lines.append(f"  · {a} 和 {b} 叠在一起：{b} 从第 {bs:g} 秒开始，"
                     f"而 {a} 要到第 {ae:g} 秒才结束（重了 {ae - bs:g} 秒）")
    for s, a, b in backwards[:3]:
        lines.append(f"  · {s} 的结束时间 {b:g} 秒早于开始时间 {a:g} 秒")

    msg = ("{ep} 第九环节排的时间线接不上（{n} 处）：\n{lines}\n"
           "**跳掉的那几秒既没有画面，也没有人负责。** 下游一处都发现不了："
           "第十环节按镜头号装箱，不看秒数；第十二、十三环节按段拿数据。"
           "要到成片拼出来才看得见，而那时候表现为**动作接不上**"
           "（不是一段黑屏），连肉眼都不容易认出是时间线的问题。\n"
           "叠在一起是另一头：那段内容会被做两遍、付两次钱，"
           "拼起来是同一件事演两回。\n"
           "去改：重跑第九环节 —— 要求 timing_plan 里相邻镜头首尾相接"
           "（上一个的 end 等于下一个的 start）。"
           "如果那几秒本来就该是空镜或者留白，那就给它一个镜头，"
           "别让时间线上有一段没人负责。"
           ).format(ep=episode or "全剧",
                    n=len(gaps) + len(overlaps) + len(backwards),
                    lines="\n".join(lines))

    diagnose.record(pj.root, diagnose.warn(
        "SHOT_TIMELINE_BROKEN", msg,
        stage=f"stage:{stage_id}", target=episode or "全剧"))
    raise RuntimeError(msg)


def check_scstate_coverage(pj: Project, episode: str,
                          log: Callable = print) -> None:
    """**十一有十二的**：每个 SEG 至少要有一张会出图的场景状态图。

    V6.2 要求每个 SEG 都有故事板，而故事板结构上需要一个主参考
    （n12 模板：`Image 1 = 本段的 SCSTATE（主参考）`）。
    所以第十一环节把某个 SEG 的**全部** SCSTATE 都判成只留文字合同时，
    它造出了一个**结构上做不出故事板的段落**。

    这件事在第十一环节跑完就能查（段落表 + 判定），不用等到第十二环节
    引了一条、再到出图前才发现 —— 中间隔着四个环节的钱。

    用户定的方向：「要么十二听十一的，要么十一有十二的」。这一条是后者：
    把第十二环节的结构性需求（每段要一个主参考）告诉第十一环节。

    **只记，不停。** 我第一版写成了硬停，那是错的 —— 第十二环节的模板里
    有一整节「本段 SCSTATE 没有图的时候（必读）」，写了完整的替代路径
    （改引本段人物的当前造型 + 场景环境 + 关键道具，位置朝向以文字合同为准）。
    也就是说**这个情况下一环节本来就能处理**，硬停在这儿等于凭空造出一个
    「两个环节冲突、要人工介入」的点 —— 正是用户要消灭的那种东西。

    留着这条记录是因为它值得知道：这几段的主参考不是场景状态图而是原子资产，
    出来的画面风格会和别的段落不太一样。但那是**结果差异**，不是错误。
    """
    segs = [s["seg_id"] for s in segments_of(pj, episode)]
    if not segs:
        return
    rows = (pj.stage_data("n11_scstate", episode) or {}).get("scstates") or []
    if not rows:
        return                          # 一条都没有是另一回事（上游对账管）
    have, only = {}, {}
    for sc in rows:
        if not isinstance(sc, dict):
            continue
        seg = str(sc.get("seg_id") or "")
        why = scstate_no_image(sc)
        (only if why else have).setdefault(seg, []).append(
            str(sc.get("scstate_id") or "?"))
    bad = [s for s in segs if not have.get(s) and only.get(s)]
    if not bad:
        return

    rows_txt = "；".join(f"{s}（{len(only[s])} 条全判成只留文字合同："
                        f"{'、'.join(only[s][:3])}"
                        f"{'…' if len(only[s]) > 3 else ''}）" for s in bad[:5])
    msg = ("{ep} 有 {n} 段的场景状态**全部**判成只留文字合同，一张图都不会出：\n"
           "  {rows}{more}\n"
           "**这样的段落做不出故事板。** V6.2 要求每个 SEG 都有故事板，"
           "而故事板结构上需要一个主参考（模板里写的是"
           "「Image 1 = 本段的 SCSTATE（主参考）」）—— 一张图都没有的话，"
           "第十二环节只有两条路：引一条永远不会存在的 png（那一段做不出来），"
           "或者改引原子资产（能做，但主参考的等价性要它自己保证）。\n"
           "**在这里停，是因为这里是唯一还能便宜地改的地方。** 往下走的话，"
           "第十二环节要花钱、故事板要出图、到视频那一步才撞上 —— "
           "中间隔着四个环节。\n"
           "去改：重跑这几段的第十一环节，让物化门控至少给每段留一条出图的"
           "（跨 SEG 入口、首次显露、不可逆结果这几条触发器本来就该命中一条）；"
           "或者确认这几段真的只要文字合同，那就在第十二环节按原子资产做主参考"
           "（模板里写了怎么做），做完这条检查会自己过。"
           ).format(ep=episode, n=len(bad), rows=rows_txt,
                    more="…" if len(bad) > 5 else "")
    diagnose.record(pj.root, diagnose.warn(
        "SCSTATE_SEG_NO_IMAGE", msg, stage="stage:n11", target=episode))


def _shot_windows(pj: Project, episode: str) -> dict:
    """第九环节排的每个镜头占哪一段时间：{镜头号: (start, end)}。"""
    out = {}
    for r in (pj.stage_data("n9_shots", episode) or {}).get("timing_plan") or []:
        if not isinstance(r, dict):
            continue
        sid = str(r.get("shot_id") or "")
        s, e = r.get("start"), r.get("end")
        if sid and isinstance(s, (int, float)) and isinstance(e, (int, float)):
            out[sid] = (float(s), float(e))
    return out


def check_packing(pj: Project, stage_id: str, out: dict, params: dict,
                  episode: str, log: Callable = print) -> None:
    """第十环节装箱：**第九环节排的镜头，有没有哪个没被装进任何一个容器。**

    这是用户实遇那次全崩的**起点**，而它一路没人说话：

      第十环节漏装了 SH_EP01_004（时间线上 30.0-38.0 秒）
        → 第十一、十二环节照跑，它们只看装出来的 SEG，看不出少了一个镜头
        → 到第十三环节，模型自己算出来了，在 time_budget_check 里写
          「SH_EP01_004 的 8 秒执行窗口位于 30.0-38.0 秒，未被 30 秒容器分配
          …因此不生成视频执行计划和可投喂提示词」，然后交了一份空提示词
        → 我们收下了（那时候校验只查键在不在），十七个环节全崩

    所以要在**装完箱就查**。查的是算术，不是判断：镜头在不在某个箱子里，
    这件事没有第二种解释，不存在「这部剧就是这样」的例外。

    两种漏法都要认：
      · 压根没写进任何 `included_shots`
      · 写进了，但同一个镜头被两个 SEG 都装了（那是重复付费 + 内容重复）

    **主动砍掉的不算漏。** 模板允许边界校正时砍东西，只要在
    `boundary_adjustments` 里交代了 —— 交代过的是决定，没交代的是窟窿。
    """
    if stage_id != "n10":
        return
    windows = _shot_windows(pj, episode)
    if not windows:
        return                      # 第九环节没排时间线，是另一回事

    used: dict = {}
    for sg in (out or {}).get("segs") or []:
        if not isinstance(sg, dict):
            continue
        for sh in (sg.get("included_shots") or []):
            used.setdefault(str(sh), []).append(str(sg.get("seg_id") or "?"))

    # 交代过要砍的，从「漏」里除掉
    said = ""
    for adj in (out or {}).get("boundary_adjustments") or []:
        if isinstance(adj, dict):
            said += str(adj.get("what_was_cut") or "") + " " + str(adj.get("action") or "")

    lost = [s for s in windows if s not in used and s not in said]
    dup = {s: v for s, v in used.items() if len(v) > 1}
    if not lost and not dup:
        return

    clip = int(params.get("duration") or 15) or 15
    lines = []
    for s in sorted(lost, key=lambda x: windows[x][0])[:8]:
        a, b = windows[s]
        lines.append(f"  · {s} 在时间线的 {a:g}-{b:g} 秒，没有任何容器装它")
    for s, segs in list(dup.items())[:5]:
        lines.append(f"  · {s} 被 {'、'.join(segs)} 重复装了 {len(segs)} 次")
    more = ("" if len(lost) <= 8 else f"（还有 {len(lost) - 8} 个没列）")

    msg = ("第九环节排了 {n} 个镜头，第十环节装箱之后 {bad} 个没有落到容器里"
           "{more}：\n{lines}\n"
           "一个 SEG 容器 {clip} 秒，本集的镜头铺到第 {last:g} 秒 —— "
           "漏的那几个多半在最后那截，容器没装够。\n"
           "**这不是提醒，是往下每一步都建在错的前提上。** "
           "第十一、十二环节只看装出来的 SEG，看不出少了镜头；"
           "要到第十三环节模型自己算出来、写一句「未被容器分配…因此不生成"
           "视频执行计划」然后交一份空提示词 —— 中间那几个环节的钱全白花。\n"
           "去改：重跑第十环节（多装一两箱把尾巴装进去）。"
           "如果漏的那几个镜头本来就该砍，让它在 boundary_adjustments 的 "
           "what_was_cut 里写清楚砍了什么 —— 交代过的就不算漏。"
           ).format(n=len(windows), bad=len(lost) + len(dup), more=more,
                    lines="\n".join(lines), clip=clip,
                    last=max(b for _a, b in windows.values()))

    diagnose.record(pj.root, diagnose.warn(
        "SEG_SHOT_UNPACKED", msg,
        stage=f"stage:{stage_id}", target=episode or "全剧"))
    raise RuntimeError(msg)


def _plan_seconds(out: dict) -> float:
    """时间计划铺到了第几秒。取最大的 end —— 不假设它是按顺序写的。"""
    ends = [float(t["end"]) for t in (out or {}).get("timing_plan") or []
            if isinstance(t, dict) and isinstance(t.get("end"), (int, float))]
    return max(ends) if ends else 0.0


def _split_episodes(pj: Project, out: dict, params: dict, log: Callable) -> None:
    """环节1 一跑完立刻切集 —— 边界由它判断，切割由代码按锚点做。"""
    res = _eps.build(pj, params.get("script", ""), out)
    eps = res.get("episodes", [])
    log(f"识别出 {len(eps)} 集")
    for e in eps[:60]:
        log(f"  {e['episode']}  {e['chars']:>6} 字  {(e.get('title') or '')[:30]}")
    for it in res.get("issues", []):
        log(f"  {'⚠️' if it.get('level') == 'warn' else '❌'} {it['episode']}：{it['reason']}")


def run_segment_stage(pj: Project, stage_id: str, *, llm, params: dict,
                      episode: str, log: Callable = print,
                      cancel: Optional[Callable] = None,
                      seg_concurrency: int = 1,
                      on_item: Optional[Callable] = None) -> tuple:
    """逐段跑：一段一次调用、每段存盘、一段失败不毒掉整集、天然可续跑。

    整集一次调用的问题是输出太长（一集十几段的故事板包就几十万字节），
    中途失败整批白跑。段与段之间没有数据依赖，拆开是安全的。
    """
    if V.scope_of(stage_id) != "segment":
        raise ValueError(f"{stage_id} 不是逐段环节")
    tpl_name, _, required = V.LLM_SPEC[stage_id]
    miss = missing_deps(pj, stage_id, episode)
    if miss:
        raise RuntimeError(f"{stage_id} 的前置还没跑：{'、'.join(miss)}")

    segs = segments_of(pj, episode)
    if not segs:
        raise RuntimeError(f"{episode} 还没有段落。先把第十环节跑通。")
    key = _result_key(stage_id)
    prev = pj.stage_data(tpl_name, episode) or {key: []}
    by_id = {c.get("seg_id") or c.get("id"): c for c in prev.get(key, [])
             if c.get("seg_id") or c.get("id")}
    todo = [s for s in segs if s["seg_id"] not in by_id]
    # **逐段的上游只做到哪几段，这一步就只能做到哪几段。**
    #
    # missing_deps 只查「上游产物存不存在」，查不出「它有没有覆盖这一段」。
    # 实遇：第十二环节 5 段里成了 3 段就结束了，产物是存在的（3 条），
    # 于是第十三环节照样把 5 段全做了 —— 那 2 段拿到的输入里没有自己的故事板，
    # 模型会自己编一个，**而且不报错**。
    #
    # 这不是「就绪即派」的事：那一套只管出图出片。文字环节之间靠的是
    # 这里的逐段对账。
    blocked: dict = {}
    _by_out = {s["out"]: s for s in V.STAGES if s.get("out")}
    for dep in V.LLM_SPEC[stage_id][1]:
        up = _by_out.get(dep)
        if not up or V.scope_of(up["id"]) != "segment":
            continue                       # 全剧级/逐集级的上游由 missing_deps 管
        have = done_segments(pj, up["id"], episode)
        for s in todo:
            if s["seg_id"] not in have:
                blocked.setdefault(s["seg_id"], []).append(
                    f"第{up['no']}环节「{up['name']}」")
    if blocked:
        todo = [s for s in todo if s["seg_id"] not in blocked]
        rows = "；".join(f"{k}（缺 {'、'.join(v)}）"
                        for k, v in list(blocked.items())[:5])
        log(f"⚠️ 有 {len(blocked)} 段的上游还没做出来，这一步跳过它们：{rows}"
            + ("…" if len(blocked) > 5 else "")
            + "。**不硬做** —— 硬做的话这几段拿到的输入里没有自己那一份，"
              "模型会自己编一个，而且不报错。先把上游那几段补出来。")
        diagnose.record(pj.root, diagnose.warn(
            "SEG_UPSTREAM_MISSING",
            f"{episode} 有 {len(blocked)} 段因为上游没做出来而跳过了"
            f"第{V.by_id()[stage_id]['no']}环节：" + rows
            + ("…" if len(blocked) > 5 else "")
            + "。重跑上游那个环节把缺的段补上，再跑这一步 —— "
              "这一步会自动只补跳过的那几段。",
            stage=f"stage:{stage_id}", target=episode))
    log(f"{episode} {stage_id} 共 {len(segs)} 段，已完成 {len(by_id)} 段，"
        f"本次做 {len(todo)} 段")

    failed: list = []
    cancelled: list = []
    lock = threading.Lock()
    n = len(todo)

    def one(i: int, seg: dict) -> None:
        if (cancel and cancel()) or cancelled:
            return
        sid = seg["seg_id"]
        log(f"[{i}/{n}] {sid}")
        try:
            # 把 log 递进去：按段裁剪接不上时要说一声，不然那一段悄悄
            # 按整集发（输入翻几倍、更容易撞 524），日志上看不出发生过什么
            user = build_user(pj, stage_id, params, episode, sid,
                              extra={"_log": lambda m, _s=sid: log(f"  {_s}{m}")})
            with LLM_GATE.slot():
                out = llm.json_call(
                    system_prompt(pj, params), user, required=required,
                    log=lambda m, _s=sid: log(f"    {_s}: {m}"), cancel=cancel,
                    on_usage=_usage(pj, stage_id, episode, sid),
                    on_partial=keep_partial(pj, stage_id, episode, sid, llm=llm))
            item = (out.get(key) or [{}])[0]
            # **盖之前先看它写的是哪一段。** 原来这里是无条件盖成 sid ——
            # 模型跑偏、写的是另一段的内容时，
            # 我们把它的段号改成我们要的那一段，**把唯一的证据擦掉了**。
            # 用户实遇：SCST_EP01_SC01_01 的提示词正文写着 EP01-SEG09，
            # 而字段是 EP01-SEG01（我们盖的）—— 于是第一段拿到第九段的
            # 世界状态去出图，画面和剧情没关系，而一路不报错。
            drift = seg_drift(item, sid)
            if drift:
                raise RuntimeError(drift)
            item["seg_id"] = sid
            if on_item:
                on_item(sid, item)
            # 每段都存盘：中途中断不丢已完成的。并发下读-改-写必须串行，
            # 否则两段同时保存，后写的会把先写的挤掉。
            with lock:
                by_id[sid] = item
                pj.save_stage(tpl_name, {key: _ordered(by_id, segs)}, episode)
            diagnose.clear(pj.root, f"stage:{stage_id}", sid)
        except LLMCancelled:
            with lock:
                cancelled.append(sid)
        except Exception as exc:                        # noqa: BLE001
            d = diagnose.build(exc, stage=f"stage:{stage_id}", target=sid,
                               model=getattr(llm, "model", ""))
            diagnose.record(pj.root, d)
            with lock:
                failed.append(sid)
            log(f"    {diagnose.one_line(d)}")

    workers = max(1, min(int(seg_concurrency or 1), n or 1))
    if workers > 1 and n > 1:
        log(f"{episode} {stage_id} 段内并发 {workers}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda a: one(*a), list(enumerate(todo, 1))))
    else:
        for a in enumerate(todo, 1):
            one(*a)

    result = {key: _ordered(by_id, segs)}
    pj.save_stage(tpl_name, result, episode)
    # **十一有十二的**：第十一环节跑完记一条「这几段没有场景状态图，
    # 第十二环节会走原子资产」。**只记不停** —— 第十二环节有完整的替代路径，
    # 停在这儿等于凭空造一个要人工介入的点。
    # 只在这一集**全段都做完**时查：还有段没做的时候查会误报。
    if stage_id == "n11" and not failed and not cancelled:
        check_scstate_coverage(pj, episode, log)
    return result, failed, cancelled


# 段号长什么样：EP01-SEG09。**只认这个形状** —— 场次号（SC01）和镜头号
# （SH_EP01_004）都不是段号，混进来会天天误报。
_SEG_ID_RE = re.compile(r"\bEP\d{2,3}-SEG\d{1,3}\b")

# 正文里提到别的段号，多数时候是合法的（「承接上一段的…」）。
# 所以只在**一次都没提到本段**、却提到了别的段的时候才算跑偏 ——
# 那时候它整条都在写另一段。
_SEG_REF_FIELDS = ("prompt", "storyboard_prompt", "video_prompt")


def seg_drift(item: dict, want: str) -> str:
    """这一条产物写的是不是**另一段**。是就返回该说的话，不是返回空串。

    两道，都便宜：

      ① 它自己填的段号和我们要的不是同一个 —— 这条没有第二种解释
      ② 段号它没填（或填对了），但正文里**一次都没提本段**、
         只提别的段 —— 那是整条跑到别段去了

    第二道刻意收得很紧：正文提到相邻段是正常的（转场、承接），
    所以只有「完全没提本段」才算。
    """
    got = str(item.get("seg_id") or "").strip()
    if got and got != want:
        return (f"这一条要的是 {want}，模型返回的是 {got} —— 它写的是**另一段**的内容。\n"
                f"以前这里会把段号直接改成 {want} 存下来，于是 {want} 拿到 {got} 的"
                f"世界状态去出图，画面和这一段的剧情没有关系，而一路不报错。\n"
                f"重跑这一段。还是偏的话，看这一段的第十环节装箱是不是有问题"
                f"（段号错位常常是上一环节的段落表本身就乱了）。")

    text = " ".join(str(item.get(f) or "") for f in _SEG_REF_FIELDS)
    if not text.strip():
        return ""
    others = {m for m in _SEG_ID_RE.findall(text) if m != want}
    if others and want not in text:
        return (f"这一条要的是 {want}，可它的正文里一次都没提 {want}，"
                f"提的是 {'、'.join(sorted(others)[:3])} —— 整条写到别段去了。\n"
                f"重跑这一段。")
    return ""


def _ordered(by_id: dict, segs: list) -> list:
    return [by_id[s["seg_id"]] for s in segs if s["seg_id"] in by_id]


def _result_key(stage_id: str) -> str:
    """逐段环节的结果数组叫什么。跟模板 schema 里的顶层键保持一致。"""
    return {"n11": "scstates", "n12": "sbpkg", "n13": "video_plan"}[stage_id]


def done_segments(pj: Project, stage_id: str, episode: str) -> set:
    """这一集哪几段已经做完了 —— 以磁盘为准，重启也认。"""
    tpl_name, _, _ = V.LLM_SPEC[stage_id]
    key = _result_key(stage_id)
    got = (pj.stage_data(tpl_name, episode) or {}).get(key, [])
    return {c.get("seg_id") for c in got if c.get("seg_id")}


# ---------------------------------------------------------------- 第 0 章：能力冻结

# 目标视频模型一次能不能出多镜头。分四档，决定后面允许用哪些转场机制。
# 不冻结的话，第九环节会照着「六类机制随便挑」写，而模型做不出来 ——
# 表现是转场糊掉或者干脆变成一个长镜头，不报错。
CAPABILITY = ("RELIABLE", "LIMITED", "UNSUPPORTED", "UNKNOWN")

# 已知支持一次生成多镜头的模型。认不出的一律 UNKNOWN，按 LIMITED 策略走 ——
# 不假设模型有多镜头能力，是这一层的默认立场。
_MULTISHOT = {
    # 片段匹配，所以写不带档位号的前缀就能覆盖 seedance2.5-4-1-720p /
    # -00-720p / -26-480p 等一整族。
    # ⚠ 2026-08-19 修正：以前写的是 "seedance-2.5"（带连字符），
    # 而真实模型名是 "seedance2.5-…" —— 一个都匹配不上，
    # 于是这档能力一直是 UNKNOWN，多镜头按 LIMITED 走了，**且不报错**。
    "seedance2.5": "RELIABLE",      # 鹤 Seedance 2.5：4-30 秒，实测能多镜头
    "sd2.5-ultra": "RELIABLE",
    "paisiodance-2.5": "RELIABLE",
}


def detect_capability(model: str) -> str:
    m = (model or "").lower()
    for frag, level in _MULTISHOT.items():
        if frag in m:
            return level
    return "UNKNOWN"


def freeze_capability(pj: Project, params: dict, video_model: str = "",
                      log: Callable = print) -> dict:
    """第 0 章：把这次生产的执行模式和模型能力档位冻结进项目。

    冻结而不是每次现算：中途换模型会让前后两段用不同的转场策略，
    接起来就是断的。要换模型就显式改这份配置，别让它随手漂。
    """
    meta = pj.meta() or {}
    frozen = dict(meta.get("capability") or {})
    level = frozen.get("native_multishot_support") or detect_capability(video_model)
    cap = {
        "target_video_model": video_model or frozen.get("target_video_model", ""),
        "seg_duration": int(params.get("duration", 15)),
        "aspect_ratio": params.get("ratio", "9:16"),
        "transition_execution_mode": "MODEL_NATIVE_ONLY",
        "external_transition_editing": "FORBIDDEN",
        "external_shot_assembly": "FORBIDDEN",
        "native_multishot_support": level,
        "allowed_mechanisms": allowed_mechanisms(level),
        "frozen_at": frozen.get("frozen_at") or _now(),
    }
    pj.save_meta(dict(meta, capability=cap))
    log(f"能力冻结：{cap['target_video_model'] or '（未指定模型）'} → "
        f"多镜头 {level}；允许的转场机制 {len(cap['allowed_mechanisms'])} 类")
    if level in ("UNSUPPORTED", "UNKNOWN"):
        log("  这一档只用最稳的几类转场。要放开，去项目参数里把 "
            "native_multishot_support 改成 RELIABLE —— 但先拿一段试出来再改。")
    return cap


def allowed_mechanisms(level: str) -> list:
    """按能力档位决定允许哪些原生转场机制。

    降级只往「更稳」的方向走，绝不往「改用外部剪辑」走 ——
    那是项目配置层面的事，不能在这里静默切换。
    """
    cut = ["NATIVE_CUT"]
    safe = cut + ["SHIELDED_OCCLUSION", "OPTICAL_COVER"]
    return {
        "RELIABLE": safe + ["MOTION_BRIDGE", "NATIVE_DISSOLVE",
                            "VFX_THREAD_TRANSITION"],
        "LIMITED": safe,
        # 不支持多镜头：只能一个镜头连续拍，或者在同一次生成里用完整遮挡
        "UNSUPPORTED": cut,
        "UNKNOWN": safe,        # 不假设它行，按 LIMITED 策略
    }[level if level in CAPABILITY else "UNKNOWN"]


def capability_of(pj: Project) -> dict:
    return (pj.meta() or {}).get("capability") or {}


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def preview_prompt(pj: Project, stage_id: str, params: dict,
                   episode: str = "", segment: str = "") -> dict:
    """跑之前先看看这一步到底会发出去什么。**不调模型、不写盘。**

    刻意和真跑共用同一个 build_user —— 分开写迟早飘，那样预览就成了安慰剂。
    返回的形状和 V6.1 那套一致，前端不用分两套渲染。
    """
    from .llm import rough_tokens
    if stage_id not in V.LLM_SPEC:
        raise ValueError(f"环节 {stage_id} 不是 LLM 环节，没有提示词可预览")
    tpl_name, _, required = V.LLM_SPEC[stage_id]
    scope = V.scope_of(stage_id)
    out = {"stage": stage_id, "template": tpl_name, "episode": "", "segment": "",
           "segments": [], "required_fields": required, "missing": [], "note": "",
           "layers": list(prompt_files(tpl_name, pj)), "system": "", "user": "",
           "chars": 0, "tokens": 0, "unfilled": []}

    if scope != "series":
        avail = _eps.ids(pj)
        if not avail:
            out["missing"] = ["第1环节「源解析与故事真相」（还没切集）"]
            return out
        episode = episode if episode in avail else avail[0]
    else:
        episode = ""
    out["episode"] = episode

    miss = missing_deps(pj, stage_id, episode)
    if miss:
        out["missing"] = miss
        return out

    if scope == "segment":
        segs = [s["seg_id"] for s in segments_of(pj, episode)]
        out["segments"] = segs
        if not segs:
            out["missing"] = ["第10环节「SEG 包装」（段落表是空的）"]
            return out
        segment = segment if segment in segs else segs[0]
        out["segment"] = segment
        out["note"] = (f"这一集共 {len(segs)} 段，每段一次调用；"
                       f"下面是 {segment} 这一段的。")
    else:
        segment = ""

    user = build_user(pj, stage_id, params, episode, segment)
    out["system"] = system_prompt(pj, params)
    out["user"] = user
    out["chars"] = len(user)
    out["tokens"] = rough_tokens(user)
    # 填不上的占位符会原样发给模型，模型看到大括号通常会假装那里有内容继续编
    out["unfilled"] = sorted(set(re.findall(r"\{\{(\w+)\}\}", user)))
    return out


# ---------------------------------------------------------------- 跨集共享资产

# 注：这里原来有一整套「跨集资产去重」—— known_asset_ids / assets_to_write /
# 资产认领表 / 进程锁。资产表和资产提示词改成全剧级之后**全部不需要了**：
# 一张表、一遍提示词，同一个角色天然只有一份定义。
# 它防的三个坑（重复编写、C001_PROMPT.txt 互相覆盖、并发抢同一个资产）
# 也随之消失 —— 那些坑本来就是「全剧级的活拆到逐集去做」造出来的。

# ---------------------------------------------------------------- 任务装配

def _rel(kind: str, name: str) -> str:
    return {"asset": f"03_提示词/资产生产提示词/{name}",
            "scstate": f"03_提示词/场景状态提示词/{name}",
            "storyboard": f"03_提示词/故事板提示词/{name}",
            "video": f"03_提示词/视频提示词/{name}"}[kind]


def _asset_out(a: dict, revision: int = 1) -> str:
    """资产图落在哪。按家族分目录，人看文件夹时能对上。

    文件名带版本号：内容要改时建新文件而不是原地覆盖 —— 已经引用过它的
    故事板还指着旧那张，覆盖了就查不出「当时用的是哪一版人脸」。
    """
    sub = {"CHAR": "人物身份资产", "PH": "人物身份资产", "LOOK": "人物造型资产",
           "CT": "连续状态资产", "COST": "服饰资产", "LOC": "场景资产",
           "PROP_SPEC": "道具资产", "PROP_INSTANCE": "道具资产",
           "VEH": "载具资产", "CRE": "生物资产", "GRP": "群体资产",
           "VFX": "特效资产"}.get(a.get("family", ""), "其它资产")
    return f"02_固定资产/{sub}/{a['asset_id']}_R{int(revision):02d}.png"


def asset_out(pj: Project, a: dict) -> str:
    """这个资产**当前版本**的图在哪。"""
    from . import registry_v34 as REG
    return _asset_out(a, REG.current_revision(pj, str(a.get("asset_id") or "")))


# n11 那个物化判定**只写在提示词正文里** —— 输出 schema 里原来没有这一栏，
# 所以程序读不到，照样给它派了出图任务。用户实遇 SCST_EP01_SC01_01：
# 正文第一行写着「当前判定：LOGICAL_ONLY … 候选图片角色：无；本条不生成图片」，
# 而任务声明了 8 张参考图，出图前那道校验报「提示词里没有 Image N 的映射」——
# 报错指向映射，真正的毛病是**这条压根不该是出图任务**（它是一份文字合同）。
#
# 结构化字段已经补进模板（`decision`），但**已经跑完的项目里没有那一栏**，
# 所以这里同时认正文里的原话。正则只咬模板自己规定的那两种写法，
# 不去扫别的词 —— 免得把提到 LOGICAL_ONLY 的说明文字也误判成判定。
# 实遇的三种写法（都出自同一个模型，同一个模板）：
#     当前判定：LOGICAL_ONLY。候选图片角色：无；本条不生成图片。
#     决定：LOGICAL_ONLY
#     本条判定为LOGICAL_ONLY，不派发图片任务，因此没有reference_assets
#
# 第三种以前认不出 —— 我要求了「判定」后面有冒号。少认一种的后果是任务照建，
# 然后停在「提示词里没有 Image N 的映射」上（一份文字合同当然没有映射）。
#
# 判据仍然只咬**判词**，不咬内容名词：「LOGICAL_ONLY」「不派发图片任务」
# 「不生成图片」是模板规定的词，不会出现在剧情描写里。
_SCST_NO_IMAGE_RE = re.compile(
    r"(?:决定|判定|判断)\s*(?:为|是)?\s*[:：]?\s*(?:LOGICAL_ONLY|DEFER_TO_VIDEO)"
    r"|不派发图片任务|不生成图片|不出图片")


# 「模型明确拒绝产出」的识别搬去了 llm.py —— 那儿是校验发生的地方，
# 而 llm 是本模块的上游，放这儿会让 llm 反向 import 成环。


def scstate_no_image(sc: dict) -> str:
    """这条场景状态**要不要出图**。返回不出图的理由；出图就返回空串。

    先看结构化字段，没有再看正文 —— 字段是权威，正文是给老项目兜底的。
    """
    d = str(sc.get("decision") or sc.get("materialization") or "").strip().lower()
    if d:
        return d if d in NO_IMAGE_DECISIONS else ""
    m = _SCST_NO_IMAGE_RE.search(str(sc.get("prompt") or ""))
    return "正文里写着不生成图片" if m else ""


def sb_sheets(pkg: dict, seg: str) -> list:
    """一个 SBPKG 的**有序** Sheet 清单。V6.2 的核心结构改动。

    V6.1 的产物把 `storyboard_prompt` / `filename` / `reference_order` 挂在
    **包一级** —— 于是一个 SEG 只出一张图。而模板早就写着「更多关键时刻用
    有序续页 SHEET_A/B/C」，两边对不上：模型只有一个输出位置，只能把 6 格
    挤进一张（实遇 EP01-SEG06 的提示词就是「一张六格」），
    而那和「每张最多 3 格」的上限直接冲突。

    V6.2 第 19 章把这件事定死：每个 SEG 必须有覆盖**完整关键时间推进**的
    骨架，载体可以是有序多张 Sheet，也可以是有序独立 KF 锚点。
    所以那三样下移到 sheet 一级，这里把两种形状都归一成同一个清单。

    **老项目照旧能跑**：sheets 里没有提示词时退回包一级那份，当成单张 ——
    产出和以前完全一样，不会因为升级把已经出好的图判成缺失。
    """
    rows = [s for s in (pkg.get("sheets") or []) if isinstance(s, dict)]
    fresh = [s for s in rows if (s.get("storyboard_prompt") or "").strip()]
    if not fresh:
        # 老形状：整包一份提示词 → 一张图。sheet_id 取第一张声明的，没有就 SHEET_A。
        if not (pkg.get("storyboard_prompt") or "").strip():
            return []
        sid = str((rows[0].get("sheet_id") if rows else "") or "SHEET_A")
        return [{"sheet_id": sid, "order": 1,
                 "prompt": pkg["storyboard_prompt"],
                 "reference_order": pkg.get("reference_order") or [],
                 "size": pkg.get("size") or "",
                 "kf_range": (rows[0].get("kf_range") if rows else "") or "",
                 "spine_role": "", "legacy": True}]
    out = []
    for i, s in enumerate(fresh, 1):
        out.append({
            "sheet_id": str(s.get("sheet_id") or f"SHEET_{i}"),
            # order 决定**上传给视频模型的顺序**。缺了就按出现顺序 ——
            # 顺序错了模型会把后段当前段，而画面看着都对。
            "order": int(s.get("order") or i),
            "prompt": s["storyboard_prompt"],
            "reference_order": s.get("reference_order") or [],
            "size": s.get("size") or "",
            "kf_range": str(s.get("kf_range") or ""),
            "spine_role": str(s.get("spine_role") or ""),
            "legacy": False,
        })
    out.sort(key=lambda x: x["order"])
    return out


def sb_prompt_name(seg: str, sheet: dict) -> str:
    """这一张故事板的提示词 txt 叫什么。单张（老项目）保持原名。"""
    if sheet.get("legacy"):
        return f"{seg}_STORYBOARD_PROMPT.txt"
    return f"{seg}_{sheet['sheet_id']}_PROMPT.txt"


def sb_file(code: str, seg: str, sheet: dict) -> str:
    """这一张故事板落在哪。

    单张（老项目）保持原来的路径，**一个字都不能改** ——
    改了等于把已经出好的几百张判成没出，重跑一遍全部重新花钱。
    """
    if sheet.get("legacy"):
        return f"04_故事板/{code}_{seg}_STORYBOARD.png"
    return f"04_故事板/{code}_{seg}_{sheet['sheet_id']}.png"


def _no_image_reason(a, aid: str, prompts) -> dict:
    """这个资产会不会有图。空字典 = 会有（或者判断不了，交给别处报）。

    **两种成因，性质完全不同，所以话要分开说：**

      · 档位判了不出图  —— 设计如此（skill 第七章那张表），照常跑
      · 缺生产提示词    —— 这是个窟窿。环节判它要出，写提示词那一步
                            漏了，于是它不会进出图任务，等于永远没有图

    第二种以前只有一条 `ASSET_NO_PROMPT` 提醒（原话就写着「引用到它们的
    故事板会因为缺参考图停下」）—— 说对了，然后就真的停在那儿。
    """
    if a is None:
        return {}       # 资产表里都没有 → 认不出，留着让出图那层报「指不到文件」
    d = str(a.get("decision") or "")
    if d in NO_IMAGE_DECISIONS:
        return {"decision": d, "reason": str(a.get("decision_reason") or "")}
    if prompts is not None and aid not in prompts:
        return {"decision": "缺生产提示词",
                "reason": "环节4 判它要出、环节4b 没给它写提示词，"
                          "所以它不会进出图任务 —— 这不是设计如此，"
                          "是个窟窿，重跑 n4b 把它补上"}
    return {}


def split_refs(pj: Project, amap: dict, ids, *, prompts=None,
               resolve=None) -> tuple:
    """把一条 `reference_assets` 拆成「能上传的图」和「按 skill 不出图的」。

    实遇：`CST002` 被 n4 判成 `logical_only`（「普通成年日常服装，未命中关键
    服装物化触发器，**使用文字合同即可**」），所以它从来没有、也不该有图；
    可 n4b 把它写进了 `LK002` 的 `reference_assets`。装配这一层照单全收，
    给 LK002 派了一张 `CST002_R01.png` 的参考图 —— 那个文件永远不会出现，
    LK002 于是永久卡在「参考图不存在或者是个空文件」。

    排查时最费劲的一点是：CST002 **不是失败，是压根没被派过任务**，
    所以失败记录里一个字都没有，看不出它为什么缺。

    skill 第七章那张表把这件事写得很清楚 —— `logical_only` /
    `defer_to_video` / `existing_canonical` / `skip` 这几档「出图：否」。
    不出图的东西不能当参考图，它的约束本来就写在文字里
    （服装是 `costume_contracts`）。所以这里把它挑出去。

    **编号不重排。** 提示词正文里那份 `Image N = 资产ID` 是模型写的，
    挑掉一张就把后面的号往前挪，会和正文对不上 —— 那是把一个报错换成
    另一个报错。留着原来的号，正文说的还是实话，只是少了一张附件；
    少的那一张由 `no_image_refs` 记着，出图前会照名字和档位报出来。
    """
    keep, no_image = [], []
    for i, row in enumerate(ids or []):
        # 两种写法都收：资产那边是一串 id，故事板 / 视频那边是带 image_n 的字典。
        if isinstance(row, dict):
            rid = str(row.get("asset_id") or "").strip()
            n = row.get("image_n") or i + 1
        else:
            rid, n = str(row or "").strip(), i + 1
        if not rid:
            continue
        # 场景状态图那类不在资产表里，但确实会出 —— 先给它机会认领，
        # 否则会被下面当成「资产表里没有」而误判。
        pre = resolve(rid) if resolve else ""
        if pre:
            keep.append({"image_n": n, "asset_id": rid, "file_ref": pre})
            continue
        why = _no_image_reason(amap.get(rid), rid, prompts)
        if why:
            no_image.append({"image_n": n, "asset_id": rid, **why})
            continue
        # 认不出的 ID **留在列表里、file_ref 留空** —— 见 build_tasks 的说明。
        a = amap.get(rid)
        keep.append({"image_n": n, "asset_id": rid,
                     "file_ref": asset_out(pj, a) if a else ""})
    return keep, no_image


def build_tasks(pj: Project, params: dict) -> dict:
    """把各环节的产物装配成 tasks.json —— 出图出片那一层唯一读的东西。

    四类任务。相对 V6.1 多出来的是 scstate 那一类：故事板不再直接拿一堆
    原子资产当参考，而是先合成一张场景状态图再参考它。

    资产是全剧共享的，所以要把所有集的产物合起来装配，按 asset_id 去重；
    故事板和视频逐集逐段展开。

    参考图里认不出的 ID **留在列表里、file_ref 留空**，不要悄悄删掉 ——
    删了数量看着是对的，反而看不出少了一张，而出图那一层会因为
    「声明了几张就必须解析出几张」停下来并报清楚缺哪张。
    """
    code = params.get("project_code", "PROJ-001")
    eps = _eps.ids(pj) or [params.get("episode", "EP01")]
    size = params.get("image_size", "1024x1536")

    # ---- 资产：全剧一份，直接读 ----
    # 以前是「逐集产出、这里按 asset_id 合并去重」。资产表和资产提示词
    # 改成全剧级之后不需要合并了 —— 一张表、一遍提示词，同一个角色
    # 天然只有一份定义。跨集去重那套（认领表、known_asset_ids）连同
    # 它防的那些坑一起没了：重复编写、文件互相覆盖、并发抢同一个资产。
    amap = {a["asset_id"]: a
            for a in (pj.stage_data("n4_assets", "") or {}).get("assets", [])
            if a.get("asset_id")}
    prompts = {ap["asset_id"]: ap
               for ap in (pj.stage_data("n4b_asset_prompts", "") or {}).get(
                   "asset_prompts", []) if ap.get("asset_id")}

    from . import registry_v34 as REG
    REG.sync(pj, list(amap.values()))   # 先登记，版本号才查得到

    asset_tasks = []
    for aid, a in amap.items():
        if a.get("decision") in NO_IMAGE_DECISIONS or aid not in prompts:
            continue
        ap = prompts[aid]
        refs, no_img = split_refs(pj, amap, ap.get("reference_assets"),
                                  prompts=prompts)
        asset_tasks.append({
            "key": aid,
            "episodes": sorted({str(s).split("-")[0]
                                for s in (a.get("used_by_segs") or [])
                                if str(s).startswith("EP")}),
            "prompt_ref": _rel("asset", ap.get("filename") or f"{aid}_PROMPT.txt"),
            "reference_images": refs,
            "no_image_refs": no_img,
            "params": {"size": ap.get("size") or size},
            "output": asset_out(pj, a),
        })

    # ---- 场景状态图 / 故事板 / 视频：逐集逐段 ----
    scstate_tasks, sb_tasks, vd_tasks = [], [], []
    # 判成不出图的场景状态：{编号: 为什么}。**必须带编号** ——
    # 下面要拿它和故事板的参考图对一遍，两个环节对同一条的判断可能打架。
    scst_skipped: dict = {}
    sb_conflict: list = []        # 故事板引了「判成不出图」的场景状态
    sb_noprompt: list = []        # 环节12 一张提示词都没写的段 —— 那一段没有骨架
    # 参考图超上限的段：(段号, 骨架张数, 补图张数, 上限)。
    # 骨架不许为了凑额度被裁 —— 所以骨架和补图要分开算。
    vd_over: list = []
    ref_limit = int(params.get("ref_limit") or 0)
    for ep in eps:
        for sc in (pj.stage_data("n11_scstate", ep) or {}).get("scstates", []):
            sid = sc.get("scstate_id")
            # 场景状态图按**状态**去重，不按段。V3.4 里 SCSTATE 编号不含段号：
            # 同一场戏跨几段而世界状态没变时，本来就该复用同一张。
            # 不去重的话同一张图付几次钱，而且几条任务写同一个文件、
            # 后一条覆盖前一条 —— 不报错，只是白花钱。
            if not sid or any(x["key"] == sid for x in scstate_tasks):
                continue
            # **判成不出图的不派出图任务。** 它的产物是那份文字合同（照旧落盘），
            # 不是 png。派了的话出图前那道校验会报「没有 Image N 的映射」——
            # 而一份文字合同本来就不该有参考图映射，报错指错了地方。
            why = scstate_no_image(sc)
            if why:
                scst_skipped[sid] = why
                continue
            refs, no_img = split_refs(pj, amap, sc.get("reference_assets"),
                                      prompts=prompts)
            scstate_tasks.append({
                "key": sid, "episode": ep, "segment": sc.get("seg_id", ""),
                "prompt_ref": _rel("scstate", f"{sid}_PROMPT.txt"),
                "reference_images": refs,
                "no_image_refs": no_img,
                "params": {"size": size},
                "output": f"03b_场景状态图/{code}_{sid}.png",
            })

        scst_out = {t["key"]: t["output"] for t in scstate_tasks}
        for pkg in (pj.stage_data("n12_storyboard", ep) or {}).get("sbpkg", []):
            seg = pkg.get("seg_id")
            if not seg:
                continue
            # V6.2：一个 SEG 是 1..N 张**有序** Sheet（或有序独立 KF 锚点），
            # 不再是一张。老项目退回单张，路径一个字不变。
            sheets = sb_sheets(pkg, seg)
            if not sheets:
                sb_noprompt.append(seg)
                continue
            for sh in sheets:
                # 故事板的参考图里也会出现「永远不会有图」的资产 —— 用户实遇
                # EP01-SEG06 等一张 S003.png。所以和资产那边走同一个筛子。
                refs, no_img = split_refs(pj, amap, sh["reference_order"],
                                          prompts=prompts, resolve=scst_out.get)
                # **两个环节对同一条的判断打架了。**
                # 第十一环节判这条 SCSTATE 不出图（只留文字合同），
                # 第十二环节又把它当参考图 —— 而故事板结构上需要一个主参考。
                #
                # 这一条**不能像 CST002 那样挑掉**：挑掉之后故事板没有主参考了。
                # 也不能悄悄放过：出图那步只会说「参考图不存在」，
                # 看不出是两个环节各自都对、凑起来做不出来。
                for r in refs:
                    rid = str(r.get("asset_id") or "")
                    if rid in scst_skipped:
                        sb_conflict.append((seg, sh["sheet_id"], rid))
                sb_tasks.append({
                    "key": (seg if sh.get("legacy")
                            else f"{seg}_{sh['sheet_id']}"),
                    "episode": ep, "segment": seg,
                    # 骨架里的第几张 —— 视频那边按这个顺序上传。
                    "sheet_id": sh["sheet_id"], "order": sh["order"],
                    "spine_role": sh["spine_role"], "kf_range": sh["kf_range"],
                    "prompt_ref": _rel("storyboard", sb_prompt_name(seg, sh)),
                    "reference_images": refs,
                    "no_image_refs": no_img,
                    "params": {"size": sh["size"] or size},
                    "output": sb_file(code, seg, sh),
                })

        # 一个段落的**有序**骨架清单。视频那一步要整条，不是一张。
        sb_by_seg: dict = {}
        for t in sb_tasks:
            sb_by_seg.setdefault(t["segment"], []).append(t)
        for v in sb_by_seg.values():
            v.sort(key=lambda t: t.get("order") or 0)
        for vp in (pj.stage_data("n13_video", ep) or {}).get("video_plan", []):
            seg = vp.get("seg_id")
            if not seg:
                continue
            # **`reference_order` 里骨架那几张要剔掉，别的一张都不许剔。**
            #
            # n13 的 `reference_order` 是**整条上传顺序**，骨架排在前面、补图
            # 接着排。而骨架已经通过 `storyboard_refs` 单独交给出片那一层了 ——
            # 两边都留着就是同一张传两次：18 张里 9 张是重的，而 `image_n`
            # 是按上传顺序算的，于是后面每一条描述都套到别的图上。
            # （2026-08-26 实遇：面板上「参 0/18」，一排全是「缺」。）
            #
            # 但**不能像原来那样按 amap 一刀切**。原来这里写的是
            # `if asset_id in amap`，顺手把两件事一起做了：剔骨架（对）、
            # 以及静默扔掉资产表认不出的补图（错 —— 那正是「提示词里映射了
            # 5 张、实际只传了 1 张」的来处）。所以只按骨架剔：
            # 认不出的补图照旧留在列表里、file_ref 空着，
            # 由出片前 `_check_video_ref_map` 硬停并报清楚缺哪张。
            spine_ids = {str(s.get("sheet_id") or "")
                         for s in sb_by_seg.get(seg, [])}
            spine_ids.discard("")
            vd_rows, vd_dupes = [], []
            for r in (vp.get("reference_order") or []):
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("asset_id") or "").strip()
                if not rid:
                    continue
                # 骨架的 sheet_id 和 reference_order 里的写法可能一头长一头短
                #（`PRJ__SBSHEET_EP01_SEG01_A_R01` vs `SBSHEET_EP01_SEG01_A_R01`），
                # 所以两头都认后缀 —— 只比全等会漏，漏了就还是传两遍。
                if any(rid == s or (len(rid) > 6 and s.endswith(rid))
                       or (len(s) > 6 and rid.endswith(s)) for s in spine_ids):
                    vd_dupes.append(rid)
                    continue
                vd_rows.append(r)
            # 去掉几条不用报：`reference_order` 带着骨架是 n13 的正常输出
            # （它给的是整条上传顺序），不是异常。数目记进任务里，
            # 排查时看得到；**这里没有 log 可用**（build_tasks 不收 log ——
            # 顺手调一个不存在的名字就是又一个 NameError，刚踩过）。
            vd_refs, vd_no_img = split_refs(pj, amap, vd_rows, prompts=prompts)
            vd_tasks.append({
                "key": seg, "episode": ep, "segment": seg,
                "prompt_ref": _rel("video", f"{seg}_VIDEO_PROMPT.txt"),
                # V6.2：视频必须带覆盖完整关键时间推进的**有序**故事板骨架。
                # storyboard_ref 保留成第一张，只为了老产物和老页面还能读；
                # 真正发给模型的是 storyboard_refs 整条。
                "storyboard_ref": (sb_by_seg.get(seg) or [{}])[0].get("output", ""),
                "storyboard_refs": [
                    {"order": s["order"], "sheet_id": s["sheet_id"],
                     "spine_role": s["spine_role"], "file_ref": s["output"]}
                    for s in sb_by_seg.get(seg, [])],
                # 视频的补充参考图（首次显露覆盖用）。骨架那几张已经剔掉了，
                # 认不出的留着、file_ref 空着 —— 见上面那段。
                "reference_images": vd_refs,
                "no_image_refs": vd_no_img,
                # 剔掉了几条骨架重复 —— 排查「参考图数目对不对」时要这个数
                "spine_deduped": len(vd_dupes),
                "params": {"duration": params.get("duration", 15),
                           "ratio": params.get("ratio", "9:16")},
                "output": f"05_分段视频/{code}_{seg}.mp4",
            })
            if ref_limit:
                spine_n = len(sb_by_seg.get(seg, []))
                if spine_n + len(vd_refs) > ref_limit:
                    vd_over.append((seg, spine_n, len(vd_refs), ref_limit))

    if vd_over:
        # **故事板给了 N 张，视频只用得上 M 张 —— 这一条以前一个字都不说。**
        #
        # 用户原话：「故事板给了 2 个，视频只用一个故事板被锁死了」。
        # 出片那一层按上限截断（或者服务商直接拒），而被截掉的正是骨架
        # 后面那几张 —— 于是模型只看见前半段，后半段自己编。
        #
        # 这里分两种，改法完全不同：
        #   · 骨架本身就超上限 → 结构和模型对不上，得让第十二环节合并
        #     承载颗粒度，或者换一个吃得下的模型。**不许砍骨架。**
        #   · 骨架装得下、加上补图才超 → 砍补图就行，第十三环节自己能做
        rows = "；".join(
            f"{seg}（骨架 {sp} 张" + (f" + 补图 {ex} 张" if ex else "") + f"，上限 {lim} 张）"
            for seg, sp, ex, lim in vd_over[:6])
        spine_alone = [x for x in vd_over if x[1] > x[3]]
        diagnose.record(pj.root, diagnose.warn(
            "VIDEO_REF_OVER_LIMIT",
            f"有 {len(vd_over)} 段的视频参考图超过了本次模型的上限："
            + rows + ("…" if len(vd_over) > 6 else "")
            + "。\n出片那一层会按上限截掉多的（或者服务商直接拒），"
              "而被截掉的正是骨架后面那几张 —— 模型只看见前半段，"
              "后半段自己编，**画面出得来，和剧情没关系，不报错**。\n"
            + (f"其中 {len(spine_alone)} 段是**骨架本身就超上限**"
               f"（{'、'.join(x[0] for x in spine_alone[:5])}）："
               f"这是这一段的结构和这个模型对不上，"
               f"**不许砍骨架** —— 让第十二环节把这一段的 Sheet 合并到"
               f"上限以内（合并的是承载颗粒度，不是时间覆盖），"
               f"或者换一个一次吃得下更多参考图的视频模型。\n"
               if spine_alone else "")
            + "其余几段是骨架装得下、加上补图才超的：重跑第十三环节，"
              "让它把补图减到额度以内（补图只补骨架表达不了的局部覆盖）。",
            stage="video", target="build_tasks"))

    if sb_noprompt:
        # V6.2 定死每个 SEG 必须有覆盖完整关键时间推进的故事板骨架。
        # 一张提示词都没写的段，出图任务是 0 —— 而 0 个任务和「本来就不用出」
        # 长得一模一样，所以必须说出来。
        diagnose.record(pj.root, diagnose.warn(
            "VIDEO_STORYBOARD_SPINE_MISSING",
            f"有 {len(sb_noprompt)} 段的第十二环节没写出任何故事板提示词，"
            f"所以这几段没有故事板出图任务："
            + "、".join(sb_noprompt[:8]) + ("…" if len(sb_noprompt) > 8 else "")
            + "。V6.2 第 19 章要求每个段落都有覆盖完整关键时间推进的故事板骨架，"
            "没有骨架的段落出不了片（视频那一步会因为缺故事板停下）。"
            "重跑这几段的第十二环节。",
            stage="storyboard", target="build_tasks"))

    if sb_conflict:
        # 放在 scst_skipped 那条**前面**报：它更要紧，而且能解释那一条。
        rows = "；".join(f"{seg} 的 {sheet} 引了 {rid}"
                         for seg, sheet, rid in sb_conflict[:5])
        diagnose.record(pj.root, diagnose.warn(
            "SCSTATE_STORYBOARD_CONFLICT",
            f"有 {len(sb_conflict)} 处故事板引了「第十一环节判成不出图」的场景状态："
            + rows + ("…" if len(sb_conflict) > 5 else "")
            + "。两个环节各自都没错，凑起来做不出来：第十一环节按物化门控判这条"
              "只留文字合同（不出图），第十二环节又把它当参考图，"
              "而那张 png 永远不会存在。\n"
              "**这一条不能靠挑掉参考图解决** —— 挑掉之后故事板就没有主参考了，"
              "出来的画面和这一段没有关系。两条出路选一条：\n"
              "① 让它出图：改那条场景状态的判定（第十一环节的物化门控七条触发器，"
              "跨 SEG 入口和首次显露都算），然后重跑第十一、十二环节；\n"
              "② 让故事板改引原子资产：Image 1 换成本段人物的当前造型资产，"
              "再补场景环境资产和关键道具 —— 位置、支撑、朝向以那份文字合同为准。"
              "第十二环节的模板里已经写了这一条怎么做，重跑第十二环节即可。",
            stage="storyboard", target="(判定打架)"))

    if scst_skipped:
        # 「少了几张场景状态图」看着和「本来就只有这几张」一模一样 ——
        # 不记一笔的话，人只能靠数数发现。
        diagnose.record(pj.root, diagnose.warn(
            "SCSTATE_LOGICAL_ONLY",
            f"有 {len(scst_skipped)} 条场景状态被第十一环节判成不出图，"
            f"所以没有给它们派出图任务："
            + "、".join(f"{k}（{v}）" for k, v in list(scst_skipped.items())[:6])
            + ("…" if len(scst_skipped) > 6 else "")
            + "。这是按 skill 来的（判成 LOGICAL_ONLY / DEFER_TO_VIDEO 只出文字合同，"
            "合同照旧落盘在 03_提示词/场景状态提示词/）。要是你认为其中某一条该出图，"
            "去改它的判定再重跑第十一环节。",
            stage="scstate", target="build_tasks"))

    tasks = {"system": "v34", "project_code": code, "episodes": eps,
             "asset_tasks": asset_tasks, "scstate_tasks": scstate_tasks,
             "storyboard_tasks": sb_tasks, "video_tasks": vd_tasks}
    pj.save_tasks(tasks)
    return tasks


def with_identity_map(ap: dict) -> str:
    """提示词正文里缺 `Image N = 资产ID` 那几行时，**从结构化字段补出来**。

    n4b 的模板两样都要求：结构化的 `reference_role_map`，和正文里逐项分行的
    `Image N = <asset_id> <名称>` + 六个字段。模型经常写了前者、漏了后者 ——
    实跑一次里 7 个资产都这样（PI008、PSET001、PH006/007/010/011、PI009）。
    出图那一层于是硬停：「要传 2 张参考图，却没说哪张是谁」。

    **但那两样是同一份信息。** role_map 里每一项都带着 image_n、asset_id、
    名称和六个字段 —— 正文里那几行就是它的文字形式。既然数据在手上，
    补出来是确定性的，不用再花一次调用去问模型「请把你已经写过的东西再写一遍」。

    只在正文里**一行都没有**的时候补。写了一部分说明模型有自己的排版，
    我们插进去只会打乱它 —— 那种情况交给出图前的校验去报。
    """
    from .produce import _IMAGE_MAP      # 同一个正则，不另写一份
    # 正文字段和结构化字段各环节叫法不同 —— 认全，别只认资产那一套。
    #
    # 这就是「回填只覆盖两类」的原因：这个函数原来只认 `prompt` +
    # `reference_role_map`，而故事板叫 `storyboard_prompt` + `reference_order`、
    # 视频叫 `video_prompt` + `reference_order`。于是那两类一行都补不上，
    # 缺映射时直接硬停在出图/出片之前 ——「要传 N 张参考图，却没说哪张是谁」。
    prompt = str(ap.get("prompt") or ap.get("storyboard_prompt")
                 or ap.get("video_prompt") or "")
    rows = [r for r in (ap.get("reference_role_map")
                        or ap.get("reference_order") or [])
            if isinstance(r, dict)]
    if not rows or _IMAGE_MAP.search(prompt):
        return prompt
    lines = []
    for i, r in enumerate(rows, 1):
        aid = str(r.get("asset_id") or "").strip()
        if not aid:
            continue                    # 说不出是谁的那一项补了也没用
        n = r.get("image_n") or i
        lines.append(f"Image {n} = {aid} {r.get('asset_name') or ''}".rstrip())
        for label, key in (("是谁/是什么 + 画面可见内容", "who_what_visible"),
                           ("故事时间 / 当前状态", "story_time_state"),
                           ("有权控制", "must_preserve"),
                           ("无权控制", "must_not_copy"),
                           ("适用范围", "applicable_scope")):
            v = str(r.get(key) or "").strip()
            # **没数据就不写这一行。** 写个「（未填）」等于骗过校验：
            # 那一项看起来填了，实际什么都没说。缺项让校验报个提醒是对的 ——
            # 提醒不挡生产，而假装填了会把真问题藏起来。
            if v:
                lines.append(f"  {label}：{v}")
    if not lines:
        return prompt
    return "【参考图身份映射】\n" + "\n".join(lines) + "\n\n" + prompt


def write_prompt_files(pj: Project, episode: str) -> int:
    """把各环节写好的提示词正文落成 txt —— 出图那一层是按路径读文件的。

    落盘而不是塞进 tasks.json：人要能在页面上直接改这一条，
    改完立刻生效不用重跑文字环节。
    """
    from .produce import write_prompt_txt
    n = 0
    # 资产提示词是**全剧一份**的，不跟着集走。按集读的话，
    # 一是每集都会把同一批文件重写一遍，二是 40 集里只有一集读得到
    # （产物在项目根下），另外 39 集写 0 个文件而且不报错。
    for ap in (pj.stage_data("n4b_asset_prompts", "") or {}).get("asset_prompts", []):
        if ap.get("prompt"):
            write_prompt_txt(pj, _rel("asset", ap.get("filename")
                                      or f"{ap['asset_id']}_PROMPT.txt"),
                             with_identity_map(ap))
            n += 1
    for sc in (pj.stage_data("n11_scstate", episode) or {}).get("scstates", []):
        if sc.get("prompt") and sc.get("scstate_id"):
            # **场景状态图也要回填身份映射。** 以前只有资产提示词享受这个 ——
            # 而 n11 的 schema 里有一模一样的 `reference_role_map`，
            # 模型照旧经常写了结构化字段、漏了正文里那几行。
            # 于是「要传 8 张参考图，却没说哪张是谁」，整条硬停在出图之前。
            # 数据就在手上，补出来是确定性的，不用再花一次调用去问。
            write_prompt_txt(pj, _rel("scstate", f"{sc['scstate_id']}_PROMPT.txt"),
                             with_identity_map(sc))
            n += 1
    for pkg in (pj.stage_data("n12_storyboard", episode) or {}).get("sbpkg", []):
        seg = pkg.get("seg_id")
        if not seg:
            continue
        # V6.2：**逐张**落盘。整包一份的老形状由 sb_sheets 归一成单张，
        # 文件名一个字不变 —— 改了等于把已经出好的几百张判成没出。
        for sh in sb_sheets(pkg, seg):
            # **故事板也要回填身份映射。** 以前只有资产和场景状态图享受这个，
            # 而 sheet 一级同样有 `reference_order`（带 image_n / asset_id /
            # 名称 / 六个字段）—— 模型照旧经常写了结构化字段、漏了正文那几行。
            # 缺了就硬停在出图之前，而数据一直在手上。
            write_prompt_txt(pj, _rel("storyboard", sb_prompt_name(seg, sh)),
                             with_identity_map(
                                 {"prompt": sh["prompt"],
                                  "reference_order": sh.get("reference_order")}))
            n += 1
    for vp in (pj.stage_data("n13_video", episode) or {}).get("video_plan", []):
        if vp.get("video_prompt") and vp.get("seg_id"):
            # 视频同理。它的 `reference_order` 里骨架那几张排在前面
            # （image_n 1..N），补图接在后面 —— 和出片时的上传顺序一致，
            # 所以直接按 image_n 补出来编号就是对的。
            write_prompt_txt(pj, _rel("video", f"{vp['seg_id']}_VIDEO_PROMPT.txt"),
                             with_identity_map(vp))
            n += 1
    return n


# ---------------------------------------------------------------- 交付

def build_review_checklist(pj: Project, episode: str = "") -> dict:
    """人工复核清单：程序不判定内容好坏，只把该看的点摆出来。

    和第十四环节的审计是两件事：审计查**结构性矛盾**（程序和模型能查的），
    这里列的是**只有人能判**的（脸像不像、演得对不对、转场看着糊不糊）。
    """
    eps = [episode] if episode else (_eps.ids(pj) or [""])
    rows = []
    for ep in eps:
        segs = segments_of(pj, ep)
        plans = {v.get("seg_id"): v for v in
                 (pj.stage_data("n13_video", ep) or {}).get("video_plan", [])}
        cvs = {c.get("cvs_id"): c for c in
               (pj.stage_data("n8_cvs", ep) or {}).get("cvs", [])}
        vids = {v.get("id"): v for v in pj.registry("video")}
        audit = {f.get("affected_segs") and f["affected_segs"][0]: f
                 for f in (pj.stage_data("n14_audit", ep) or {}).get("findings", [])}
        for s in segs:
            sid = s["seg_id"]
            plan = plans.get(sid) or {}
            exit_cvs = cvs.get(s.get("exit_cvs")) or {}
            rows.append({
                "episode": ep, "segment_id": sid,
                "video": vids.get(sid, {}).get("file_ref", "（未生成）"),
                "check_layers": {
                    "技术状态": "文件能播、时长和画幅对",
                    "人物身份一致性": "对照资产的 identity_anchors，脸有没有变",
                    "当前状态连续性": f"进入={s.get('entry_cvs', '')} / "
                                      f"退出={s.get('exit_cvs', '')}",
                    "转场执行": "、".join(s.get("model_native_transition_ids") or [])
                                or "（这一段没有转场）",
                    "转场是不是模型一次出的": "有没有看起来像后期拼的痕迹",
                    "空间与走位": "人物位置、朝向、与固定物的关系有没有跳",
                    "首次显露": "镜头拉远/转身时，下半身、背面、鞋有没有变样",
                    "动作没有重演": "签字、跌倒、拔针这类完成过的动作有没有又来一次",
                },
                "forbidden_future": exit_cvs.get("forbidden_state", []),
                "transition_failures": [
                    w.get("failure_signature", "")
                    for w in (plan.get("transition_windows") or [])
                    if w.get("failure_signature")],
                "audit_finding": audit.get(sid, {}).get("what", ""),
                "verdict": "",      # 人工填：pass / L1 / L2 / L3
                "note": "",
            })
    out = {"system": "v34", "episodes": eps,
           "levels": {
               "L1 可接受偏差": "背景人物少量变化、非核心褶皱、轻微机位偏移 → 直接固定",
               "L2 可定向修订": "节奏、动作方向、道具短暂消失、局部口型、转场略糊 → "
                                "只改出错的那个时间窗口，重出这一段",
               "L3 结构性错误": "身份错误、关键节点缺失、因果改变、不可逆状态被恢复、"
                                "提前剧透、转场跨段 → 按依赖链回溯重做"},
           "rows": rows}
    pj.save_stage("d1_review", out, episode)
    return out


def assemble(pj: Project, params: dict, log: Callable = print,
             episode: str = "") -> dict:
    """拼接成片。拼接本身（排序、concat、流拷贝失败退回重编码）是体系无关的，
    直接复用，只把成片文件名换成这套体系的。"""
    from .stages import assemble as _assemble
    return _assemble(pj, params, log, episode,
                     master_name=lambda code, ep: f"{code}_{ep}_MASTER.mp4",
                     # 该有几段按第十环节装的箱子算 —— 那是这套体系里
                     # 「一集有哪些段」唯一的出处。
                     expect_segs=lambda p, ep: [s["seg_id"]
                                                for s in segments_of(p, ep)])


def _usage(pj: Project, stage_id: str, episode: str, target: str = "") -> Callable:
    def rec(u: dict) -> None:
        ledger.record(pj.root, kind="llm", stage=stage_id, episode=episode,
                      target=target, provider="llm", model=u.get("model", ""),
                      prompt_tokens=u.get("prompt_tokens") or 0,
                      cached_tokens=(u.get("prompt_tokens_details") or {})
                      .get("cached_tokens", 0),
                      completion_tokens=u.get("completion_tokens") or 0,
                      seconds=u.get("seconds") or 0,
                      estimated=bool(u.get("estimated")))
    return rec
