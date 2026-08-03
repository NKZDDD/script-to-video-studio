# -*- coding: utf-8 -*-
"""12 环节确定性编排。

环节 1-8：调 LLM（固定模板 + JSON 校验），编排逻辑是代码，不是模型自由发挥。
环节 5b/9/10：出图出片，走 providers + 线程池。
环节 11：产出人工复核清单（不自动判定质量）。
环节 12：ffmpeg 硬切拼接 + 生产包归档。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Callable, Optional

from . import diagnose, probe
from .llm import LLM
from .providers import ImageTask, VideoTask, build as build_provider
from .apiutil import resolve_ref
from .store import Project, read_text, write_text

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_DIR = os.path.join(os.path.dirname(HERE), "prompts")

# 环节定义：驱动前端流程图与执行按钮
STAGES = [
    {"id": "s1", "no": 1, "name": "整剧全局解析", "kind": "llm", "out": "s1_global"},
    {"id": "s2", "no": 2, "name": "节奏驱动段落划分", "kind": "llm", "out": "s2_segments"},
    {"id": "s3", "no": 3, "name": "状态时间线管理", "kind": "llm", "out": "s3_states"},
    {"id": "s4", "no": 4, "name": "资产提取与生产判断", "kind": "llm", "out": "s4_assets"},
    {"id": "s5", "no": 5, "name": "资产生产提示词编译", "kind": "llm", "out": "s5_asset_prompts"},
    {"id": "s5b", "no": 5, "name": "资产图生产（出图）", "kind": "image", "out": ""},
    {"id": "s6", "no": 6, "name": "段落资产绑定", "kind": "llm", "out": "s6_binding"},
    {"id": "s7", "no": 7, "name": "高密度正式分镜", "kind": "llm", "out": "s7_shots"},
    {"id": "s8", "no": 8, "name": "故事板与视频提示词编译", "kind": "llm", "out": "s8_compile"},
    {"id": "s9", "no": 9, "name": "故事板生成与固定", "kind": "image", "out": ""},
    {"id": "s10", "no": 10, "name": "视频执行生成", "kind": "video", "out": ""},
    {"id": "s11", "no": 11, "name": "结果检查清单（人工）", "kind": "local", "out": ""},
    {"id": "s12", "no": 12, "name": "排序拼接与交付", "kind": "local", "out": ""},
]


def load_prompt(name: str) -> str:
    return read_text(os.path.join(PROMPT_DIR, f"{name}.md"))


def render(tpl: str, mapping: dict) -> str:
    for k, v in mapping.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def jd(obj: Any, limit: int = 0) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return s[:limit] if limit and len(s) > limit else s


# ====================================================================== LLM 环节

_LLM_SPEC = {
    # stage_id: (模板名, 依赖的已存产物, 必需字段)
    "s1": ("s1_global", [], ["project_name", "characters[]", "visual_tone"]),
    "s2": ("s2_segments", ["s1_global"], ["segments[]", "segments[].id", "segments[].exit_state"]),
    "s3": ("s3_states", ["s1_global", "s2_segments"], ["segment_states[]"]),
    "s4": ("s4_assets", ["s1_global", "s2_segments", "s3_states"], ["assets[]", "assets[].asset_id"]),
    "s5": ("s5_asset_prompts", ["s1_global", "s4_assets"], ["asset_prompts[]", "asset_prompts[].prompt"]),
    "s6": ("s6_binding", ["s2_segments", "s3_states", "s4_assets"], ["bindings[]"]),
    "s7": ("s7_shots", ["s2_segments", "s3_states", "s6_binding"], ["shots[]"]),
    "s8": ("s8_compile", ["s1_global", "s2_segments", "s3_states", "s4_assets", "s6_binding", "s7_shots"],
           ["compiled[]", "compiled[].storyboard_prompt", "compiled[].video_prompt"]),
}


_STAGE_OF_OUT = {s["out"]: s for s in STAGES if s.get("out")}


def run_llm_stage(pj: Project, stage_id: str, llm: LLM, params: dict, log: Callable = print) -> dict:
    tpl_name, deps, required = _LLM_SPEC[stage_id]
    data = {d: pj.stage_data(d) for d in deps}
    missing = [d for d, v in data.items() if v is None]
    if missing:
        names = "、".join(f"环节{_STAGE_OF_OUT[m]['no']}「{_STAGE_OF_OUT[m]['name']}」"
                          for m in missing if m in _STAGE_OF_OUT)
        raise RuntimeError(f"缺少前置产物，请先跑：{names or missing}")

    if stage_id == "s8":                       # s8 分段编译，天然可续跑
        return run_s8_incremental(pj, llm, params, data, log)

    g = data.get("s1_global") or {}
    tone = (g.get("visual_tone") or {})
    mapping = {
        "PARAMS": jd(params),
        "SCRIPT": params.get("script", ""),
        "DURATION": params.get("duration", 15),
        "EPISODE_MINUTES": params.get("episode_minutes", 3),
        "IMAGE_SIZE": params.get("image_size", "1024x1536"),
        "SHOTS_MIN": params.get("shots_min", 5), "SHOTS_MAX": params.get("shots_max", 8),
        "FRAMES_MIN": params.get("frames_min", 4), "FRAMES_MAX": params.get("frames_max", 6),
        "FRAMES": params.get("frames", 4),
        "TONE": jd({"compressed": tone.get("compressed", ""),
                    "variants": tone.get("compressed_variants", [])}),
        "GLOBAL": jd(data.get("s1_global", {})),
        "SEGMENTS": jd(data.get("s2_segments", {})),
        "STATES": jd(data.get("s3_states", {})),
        "ASSETS": jd(data.get("s4_assets", {})),
        "BINDINGS": jd(data.get("s6_binding", {})),
        "SHOTS": jd(data.get("s7_shots", {})),
    }
    system = load_prompt("_common")
    user = render(load_prompt(tpl_name), mapping)
    log(f"提示词 {len(user)} 字，调用 {llm.model}")
    out = llm.json_call(system, user, required=required, log=log)
    pj.save_stage(_LLM_SPEC[stage_id][0], out)

    # s5 额外把提示词正文落成 txt，便于人工查看与执行器读取
    if stage_id == "s5":
        for ap in out.get("asset_prompts", []):
            write_text(pj.p("03_提示词", "资产生产提示词", ap.get("filename") or f"{ap['asset_id']}_PROMPT.txt"),
                       ap.get("prompt", ""))
    diagnose.clear(pj.root, f"stage:{stage_id}")
    return out


def s8_done_segments(pj: Project) -> set:
    """已经编好两份提示词的段落（磁盘为准，重启也认）。"""
    done = set()
    sb_dir, vd_dir = pj.p("03_提示词", "故事板提示词"), pj.p("03_提示词", "视频提示词")
    if not os.path.isdir(sb_dir):
        return done
    for f in os.listdir(sb_dir):
        if f.endswith("_STORYBOARD_PROMPT.txt"):
            sid = f[: -len("_STORYBOARD_PROMPT.txt")]
            if os.path.isfile(os.path.join(vd_dir, f"{sid}_VIDEO_PROMPT.txt")):
                done.add(sid)
    return done


def run_s8_incremental(pj: Project, llm: LLM, params: dict, data: dict,
                       log: Callable = print) -> dict:
    """环节8 逐段编译：一段一次 LLM 调用。

    整集一次调用的问题：17 段 × 2 份提示词输出太长，中途失败整批白跑。
    逐段调用后，已完成的段落跳过，失败只影响那一段 —— 天然支持续跑。
    """
    segs = (data.get("s2_segments") or {}).get("segments", [])
    if not segs:
        raise RuntimeError("段落表为空，请先跑环节2")

    tone = ((data.get("s1_global") or {}).get("visual_tone") or {})
    states = {s["id"]: s for s in (data.get("s3_states") or {}).get("segment_states", [])}
    binds = {b["id"]: b for b in (data.get("s6_binding") or {}).get("bindings", [])}
    shots = {s["id"]: s for s in (data.get("s7_shots") or {}).get("shots", [])}
    assets = (data.get("s4_assets") or {}).get("assets", [])

    prev = pj.stage_data("s8_compile") or {"compiled": []}
    by_id = {c["id"]: c for c in prev.get("compiled", [])}
    done = s8_done_segments(pj)

    system = load_prompt("_common")
    tpl = load_prompt("s8_compile")
    todo = [s for s in segs if s["id"] not in done]
    log(f"共 {len(segs)} 段，已完成 {len(done)} 段，本次编译 {len(todo)} 段")

    failed = []
    for i, seg in enumerate(todo, 1):
        sid = seg["id"]
        used = set((binds.get(sid, {}).get("reference_images") or [])
                   and [r.get("asset_id") for r in binds[sid]["reference_images"]] or [])
        seg_assets = [a for a in assets if a["asset_id"] in used] or assets
        user = render(tpl, {
            "DURATION": params.get("duration", 15),
            "FRAMES": params.get("frames", 4),
            "TONE": jd({"compressed": tone.get("compressed", ""),
                        "variants": tone.get("compressed_variants", [])}),
            "SEGMENTS": jd(seg),
            "STATES": jd(states.get(sid, {})),
            "ASSETS": jd(seg_assets),
            "BINDINGS": jd(binds.get(sid, {})),
            "SHOTS": jd(shots.get(sid, {})),
        }) + f"\n\n【只编译这一段】{sid}，compiled 数组只放这一段。"
        log(f"[{i}/{len(todo)}] 编译 {sid}")
        try:
            out = llm.json_call(system, user,
                                required=["compiled[]", "compiled[].storyboard_prompt",
                                          "compiled[].video_prompt"],
                                log=lambda m: log(f"    {m}"))
            c = out["compiled"][0]
            c["id"] = sid
            by_id[sid] = c
            write_text(pj.p("03_提示词", "故事板提示词", f"{sid}_STORYBOARD_PROMPT.txt"),
                       c.get("storyboard_prompt", ""))
            write_text(pj.p("03_提示词", "视频提示词", f"{sid}_VIDEO_PROMPT.txt"),
                       c.get("video_prompt", ""))
            # 每段都存盘：中途中断也不丢已完成的
            pj.save_stage("s8_compile", {"compiled": [by_id[s["id"]] for s in segs if s["id"] in by_id]})
            diagnose.clear(pj.root, "stage:s8", sid)
        except Exception as exc:                            # noqa: BLE001
            d = diagnose.build(exc, stage="stage:s8", target=sid, model=llm.model)
            diagnose.record(pj.root, d)
            failed.append(sid)
            log(f"    {diagnose.one_line(d)}")

    result = {"compiled": [by_id[s["id"]] for s in segs if s["id"] in by_id]}
    pj.save_stage("s8_compile", result)
    build_tasks(pj, params)

    if failed:
        raise RuntimeError(f"{len(failed)} 段编译失败：{'、'.join(failed[:8])}"
                           f"{'…' if len(failed) > 8 else ''}。"
                           f"已完成的 {len(result['compiled'])} 段已保存，重跑环节8只会补失败的这些段。")
    log(f"全部 {len(result['compiled'])} 段编译完成，tasks.json 已装配")
    return result


# ====================================================================== 任务装配

_CAT_DIR = {
    "identity": "人物身份资产", "environment": "场景资产", "prop": "道具资产",
    "state": "连续状态资产", "group": "群体资产", "creature": "生物资产",
}


def asset_output_rel(asset: dict) -> str:
    d = _CAT_DIR.get(asset.get("category", ""), "人物身份资产")
    return f"02_固定资产/{d}/{asset['asset_id']}.png"


def build_tasks(pj: Project, params: dict) -> dict:
    """把 s4/s5/s6/s8 的产物装配成 tasks.json（执行器消费的机器可读清单）。"""
    code = params.get("project_code", "PROJ-001")
    ep = params.get("episode", "EP01")
    assets = (pj.stage_data("s4_assets") or {}).get("assets", [])
    aprompts = {a["asset_id"]: a for a in (pj.stage_data("s5_asset_prompts") or {}).get("asset_prompts", [])}
    bindings = {b["id"]: b for b in (pj.stage_data("s6_binding") or {}).get("bindings", [])}
    compiled = (pj.stage_data("s8_compile") or {}).get("compiled", [])
    amap = {a["asset_id"]: a for a in assets}

    asset_tasks = []
    for a in assets:
        if a.get("decision") == "skip" or a["asset_id"] not in aprompts:
            continue
        ap = aprompts[a["asset_id"]]
        asset_tasks.append({
            "key": a["asset_id"],
            "prompt_ref": f"03_提示词/资产生产提示词/{ap.get('filename') or a['asset_id'] + '_PROMPT.txt'}",
            "reference_images": [
                {"image_n": i + 1, "asset_id": rid, "file_ref": asset_output_rel(amap[rid])}
                for i, rid in enumerate(ap.get("reference_assets", [])) if rid in amap
            ],
            "params": {"size": ap.get("size") or params.get("image_size", "1024x1536")},
            "output": asset_output_rel(a),
        })

    sb_tasks, vd_tasks = [], []
    for c in compiled:
        sid = c["id"]
        seg = sid.split("-")[-1]
        b = bindings.get(sid, {})
        refs = c.get("reference_order") or b.get("reference_images") or []
        sb_out = f"04_故事板/{code}_{ep}_{seg}_STORYBOARD_V01_FIXED.png"
        sb_tasks.append({
            "key": sid,
            "prompt_ref": f"03_提示词/故事板提示词/{sid}_STORYBOARD_PROMPT.txt",
            "reference_images": [
                {"image_n": r.get("image_n", i + 1), "asset_id": r.get("asset_id", ""),
                 "file_ref": asset_output_rel(amap[r["asset_id"]]) if r.get("asset_id") in amap else ""}
                for i, r in enumerate(refs)
            ],
            "params": {"size": params.get("image_size", "1024x1536"),
                       "frames": params.get("frames", 4)},
            "output": sb_out,
        })
        aux = c.get("aux_reference_asset_id") or ""
        vd_tasks.append({
            "key": sid,
            "prompt_ref": f"03_提示词/视频提示词/{sid}_VIDEO_PROMPT.txt",
            "storyboard_ref": sb_out,
            "aux_reference": asset_output_rel(amap[aux]) if aux in amap else None,
            "params": {"duration": params.get("duration", 15),
                       "ratio": params.get("ratio", "9:16")},
            "output": f"05_分段视频/{code}_{ep}_{seg}_VIDEO_V01_FIXED.mp4",
        })

    tasks = {"project_code": code, "episode": ep,
             "asset_tasks": asset_tasks, "storyboard_tasks": sb_tasks, "video_tasks": vd_tasks}
    pj.save_tasks(tasks)
    return tasks


# ====================================================================== 出图/出片 worker

def _ratio_warn(pj: Project, path: str, want: str, stage: str, key: str,
                provider_cfg: dict, model: str, media: str):
    """出完东西量一下真实尺寸，比例不对就挂一条提醒。

    这类问题接口不会报错——200、文件也正常下载，只是画面躺倒了。
    不主动量，就得等人工验收才发现，那时候钱早花完了。
    量不到（缺 ffprobe、格式不认识）就当没这回事，不能反过来卡住主流程。
    """
    try:
        bad = probe.check(path, want, kind=media)
    except Exception:                                  # noqa: BLE001
        return None
    if not bad:
        return None
    flip = ("你要的是竖屏，出来的是横屏。"
            if bad["portrait_wanted"] and not bad["portrait_got"] else
            "你要的是横屏，出来的是竖屏。"
            if not bad["portrait_wanted"] and bad["portrait_got"] else "")
    pj.log_event({"stage": stage, "id": key, "result": "ratio_mismatch", **bad})
    return diagnose.warn(
        "WRONG_RATIO",
        f"要的是 {bad['want']}，实际出来 {bad['got']}（约 {bad['got_ratio']}）。{flip}",
        stage=stage, target=key, provider=provider_cfg.get("provider", ""), model=model,
        extra_fix=[f"这个文件在：{pj.rel(path)}"])


def make_image_worker(pj: Project, provider_cfg: dict, kind: str) -> Callable:
    """kind: asset | storyboard。返回 worker(task, log, cancel)。"""
    prov = build_provider(provider_cfg["provider"], provider_cfg["api_key"],
                          provider_cfg.get("base_url", ""), provider_cfg.get("proxy", ""))
    model = provider_cfg.get("model", "")
    interval = int(provider_cfg.get("poll_interval", 5))
    timeout = int(provider_cfg.get("poll_timeout", 900))
    ref_side = int(provider_cfg.get("ref_max_side", 1024))

    def worker(task: dict, log: Callable, cancel: Callable) -> dict:
        out = pj.p(*task["output"].split("/"))
        want = (task.get("params") or {}).get("size", "1024x1536")
        if os.path.isfile(out):
            # 跳过也要重新量一遍：不然「比例不对」的提醒会被这次跳过悄悄清掉，
            # 而文件其实还是那个躺倒的文件。以磁盘上的东西为准。
            return {"skipped": True, "msg": "已经有了，跳过（做出来的就不再动）",
                    "warn": _ratio_warn(pj, out, want, kind, task["key"],
                                        provider_cfg, model, "image")}
        prompt = read_text(pj.p(*task["prompt_ref"].split("/")))
        refs = []
        for r in sorted(task.get("reference_images", []), key=lambda x: x.get("image_n", 0)):
            src = r.get("url") or r.get("file_ref") or ""
            if src:
                refs.append(resolve_ref(src, pj.root, max_side=ref_side))
        log(f"参考图×{len(refs)}")
        meta = prov.generate_image(
            ImageTask(prompt=prompt, refs=refs, size=want, model=model),
            out, log=log, cancel=cancel, poll_interval=interval, poll_timeout=timeout)
        pj.upsert_registry(kind, {"id": task["key"], "file_ref": task["output"],
                                  "status": "generated", **meta})
        pj.log_event({"stage": kind, "id": task["key"], "result": "ok", **meta})
        return {"output": task["output"],
                "warn": _ratio_warn(pj, out, want, kind, task["key"],
                                    provider_cfg, model, "image")}

    return worker


def make_video_worker(pj: Project, provider_cfg: dict) -> Callable:
    prov = build_provider(provider_cfg["provider"], provider_cfg["api_key"],
                          provider_cfg.get("base_url", ""), provider_cfg.get("proxy", ""))
    model = provider_cfg.get("model", "")
    interval = int(provider_cfg.get("poll_interval", 10))
    timeout = int(provider_cfg.get("poll_timeout", 2400))
    ref_side = int(provider_cfg.get("ref_max_side", 1024))

    def worker(task: dict, log: Callable, cancel: Callable) -> dict:
        out = pj.p(*task["output"].split("/"))
        p = task.get("params") or {}
        want = p.get("ratio", "9:16")
        if os.path.isfile(out):
            # 同上：跳过时也重新量，别把「比例不对」的提醒清没了
            return {"skipped": True, "msg": "已经有了，跳过",
                    "warn": _ratio_warn(pj, out, want, "video", task["key"],
                                        provider_cfg, model, "video")}
        sb = task["storyboard_ref"]
        if not (sb.startswith("http") or os.path.isfile(pj.p(*sb.split("/")))):
            raise RuntimeError(f"固定故事板不存在，请先跑环节9: {sb}")
        prompt = read_text(pj.p(*task["prompt_ref"].split("/")))
        refs = [resolve_ref(sb, pj.root, max_side=ref_side)]
        if task.get("aux_reference"):
            refs.append(resolve_ref(task["aux_reference"], pj.root, max_side=ref_side))
        log(f"model={model} {p.get('duration', 15)}s {want} 参考图×{len(refs)}")
        meta = prov.generate_video(
            VideoTask(prompt=prompt, refs=refs, duration=int(p.get("duration", 15)),
                      ratio=want, model=model,
                      resolution=provider_cfg.get("resolution", "")),
            out, log=log, cancel=cancel, poll_interval=interval, poll_timeout=timeout)
        pj.upsert_registry("video", {"id": task["key"], "file_ref": task["output"],
                                     "storyboard_ref": sb, "status": "generated", **meta})
        pj.log_event({"stage": "video", "id": task["key"], "result": "ok", **meta})
        return {"output": task["output"],
                "warn": _ratio_warn(pj, out, want, "video", task["key"],
                                    provider_cfg, model, "video")}

    return worker


# ====================================================================== 环节 11 / 12

def build_review_checklist(pj: Project) -> dict:
    """环节11：产出人工复核清单。程序不判定内容质量，只把该看的点摆出来。"""
    segs = (pj.stage_data("s2_segments") or {}).get("segments", [])
    states = {s["id"]: s for s in (pj.stage_data("s3_states") or {}).get("segment_states", [])}
    videos = {v.get("id"): v for v in pj.registry("video")}
    rows = []
    for s in segs:
        sid = s["id"]
        st = states.get(sid, {})
        rows.append({
            "segment_id": sid,
            "video": videos.get(sid, {}).get("file_ref", "（未生成）"),
            "check_layers": {
                "技术状态": "文件可播放、时长/画幅正确",
                "剧情完整性": s.get("story_task", ""),
                "人物身份一致性": "对照资产 identity_anchors",
                "状态连续性": f"进入={st.get('entry', '')} / 退出={st.get('exit', '')}",
                "动作因果": s.get("turn", ""),
                "空间轴线": "180度轴线、左右阵营、运动方向",
                "进入与退出状态": s.get("exit_state", ""),
                "跨段衔接": s.get("next_anchor", ""),
            },
            "forbidden_future": s.get("forbidden_future", []),
            "irreversible": st.get("irreversible", []),
            "verdict": "",           # 人工填：pass / L1 / L2 / L3
            "note": "",
        })
    out = {"episode": (pj.stage_data("s2_segments") or {}).get("episode", ""),
           "levels": {
               "L1 可接受偏差": "背景人物少量变化、非核心褶皱、特效形态、轻微机位偏移 → 直接固定",
               "L2 可定向修订": "节奏过慢、动作方向错、道具短暂消失、局部口型、短暂变脸 → 视频提示词V02，只改出错时间区间",
               "L3 结构性错误": "身份错误、关键节点缺失、因果改变、不可逆状态被恢复、退出状态错误、提前剧透 → 按依赖链回溯重做"},
           "rows": rows}
    pj.save_stage("s11_review", out)
    return out


def health(pj: Project) -> dict:
    """项目体检：每个环节做到哪、卡在哪、下一步该干什么。

    完全以磁盘为准 —— 重启服务、换电脑都能算出同样的结果。
    """
    steps = []
    tasks = pj.tasks()
    seg_total = len((pj.stage_data("s2_segments") or {}).get("segments", []))

    for st in STAGES:
        item = {"id": st["id"], "no": st["no"], "name": st["name"], "kind": st["kind"],
                "done": 0, "total": 0, "state": "pending", "action": ""}
        if st["kind"] == "llm":
            if st["id"] == "s8":
                item["total"] = seg_total
                item["done"] = len(s8_done_segments(pj))
            else:
                ok = pj.stage_data(st["out"]) is not None
                item["total"], item["done"] = 1, (1 if ok else 0)
            item["action"] = "流程页 → 执行"
        elif st["kind"] in ("image", "video"):
            key = {"s5b": "asset_tasks", "s9": "storyboard_tasks", "s10": "video_tasks"}[st["id"]]
            items = tasks.get(key, [])
            item["total"] = len(items)
            item["done"] = sum(1 for t in items
                               if os.path.isfile(pj.p(*t["output"].split("/"))))
            item["action"] = "生产页 → 开始"
        else:
            if st["id"] == "s11":
                item["total"] = 1
                item["done"] = 1 if pj.stage_data("s11_review") else 0
                item["action"] = "产物页 → 生成人工复核清单"
            else:
                masters = []
                d = pj.p("06_成片")
                if os.path.isdir(d):
                    masters = [f for f in os.listdir(d) if f.lower().endswith(".mp4")]
                item["total"] = 1
                item["done"] = 1 if masters else 0
                item["action"] = "产物页 → 硬切拼接成片"

        if item["total"] == 0:
            item["state"] = "blocked" if st["kind"] != "llm" else "pending"
        elif item["done"] >= item["total"]:
            item["state"] = "done"
        elif item["done"] > 0:
            item["state"] = "partial"
        else:
            item["state"] = "pending"
        steps.append(item)

    fails = diagnose.summary(pj.root)
    nxt = next((s for s in steps if s["state"] in ("partial", "pending") and s["total"] > 0), None)
    if nxt is None:
        nxt = next((s for s in steps if s["state"] == "blocked"), None)

    # 「下一步做什么」永远要说出来。报错只是插在前面提醒你先处理，
    # 不能把下一步顶掉——否则用户处理完报错，又不知道接着干嘛了。
    if nxt:
        remain = nxt["total"] - nxt["done"]
        nxt_tip = (f"下一步：环节{nxt['no']}「{nxt['name']}」"
                   + (f"（还差 {remain}/{nxt['total']}）" if nxt["total"] > 1 else "")
                   + f" —— {nxt['action']}")
    else:
        nxt_tip = "所有环节都跑完了"

    if fails.get("errors"):
        tip = f"有 {fails['errors']} 条没做出来。先照下面的说明处理，再点「开始」补上。{nxt_tip}"
    elif fails.get("warns"):
        tip = f"{nxt_tip}（另外有 {fails['warns']} 条做出来了但可能不对，看下面，不着急处理也行）"
    else:
        tip = nxt_tip

    return {"steps": steps, "failures": fails, "next": nxt, "tip": tip,
            "resumable": True}


def find_ffmpeg() -> str:
    return probe.find_ffmpeg()          # 只保留一份查找逻辑，见 core/probe.py


def assemble(pj: Project, params: dict, log: Callable = print) -> dict:
    """环节12：按段号排序、硬切拼接、生成成片。"""
    code = params.get("project_code", "PROJ-001")
    ep = params.get("episode", "EP01")
    vids = sorted(
        (v for v in pj.registry("video") if v.get("file_ref")),
        key=lambda v: v.get("id", ""))
    exist = [v for v in vids if os.path.isfile(pj.p(*v["file_ref"].split("/")))]
    if not exist:
        raise RuntimeError("没有可拼接的分段视频")
    lines = ["file '" + os.path.relpath(pj.p(*v["file_ref"].split("/")),
                                        pj.p("06_成片")).replace("\\", "/") + "'" for v in exist]
    concat = pj.p("06_成片", f"{ep}_concat.txt")
    os.makedirs(os.path.dirname(concat), exist_ok=True)
    with open(concat, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    master = pj.p("06_成片", f"{code}_{ep}_MASTER_V01_FIXED.mp4")
    ff = find_ffmpeg()
    if not ff:
        return {"concat": pj.rel(concat), "master": "", "count": len(exist),
                "msg": "未找到 ffmpeg，已生成 concat 清单，请手动拼接或 pip install imageio-ffmpeg"}
    cmd = [ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", master]
    log("流拷贝拼接…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(master):
        log("流拷贝失败，重编码兜底")
        cmd = [ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat,
               "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac", master]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 拼接失败: {r.stderr[:300]}")
    size = os.path.getsize(master)
    pj.log_event({"stage": "assemble", "result": "ok", "count": len(exist), "size": size})
    return {"concat": pj.rel(concat), "master": pj.rel(master),
            "count": len(exist), "size": size}
