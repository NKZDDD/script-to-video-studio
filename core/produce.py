# -*- coding: utf-8 -*-
"""出图出片的执行层 —— **不认哪一套生产体系**。

这里只有「拿到一条任务，怎么把它做出来」：参考图变成能发出去的形式、
按依赖分层派单、调服务商、量一下出来的东西对不对、把手改过的提示词护住。
任务长什么样由体系层（core/stages.py）装配，这一层只消费固定的几个字段：

    key / prompt_ref / reference_images[] / params / output

所以换一套生产体系（环节表、资产分类、提示词模板全变）时，这个文件不用动。
之所以要单独放：这里是踩坑最密集的地方（参考图形式声明错会静默丢图、
缺图不能凑合出、比例不对接口不报错），修复必须能干净地同步到别的体系分支上。
"""

from __future__ import annotations

import os
import re
from typing import Callable

from . import diagnose, ledger, probe, uploader
from .apiutil import resolve_ref
from .providers import ImageTask, VideoTask, build as build_provider
from .store import Project, read_text, write_text


def write_prompt_txt(pj: Project, rel: str, text: str, log=None) -> None:
    """环节5/8 落盘一份提示词 txt。

    走这里而不是直接 write_text，是为了在覆盖**手改过**的那份之前先备份 +
    说一声。人在页面上改好一条、隔天重跑一次环节8 就被悄悄盖掉 ——
    这种事不报出来，等出图不对劲再回头找，原文已经没了。
    """
    from . import promptfile
    try:
        promptfile.guard_overwrite(pj, rel, text, log)
    except Exception:                                   # noqa: BLE001
        pass                                            # 备份失败不该挡住主流程
    write_text(pj.p(*rel.split("/")), text)


def _ordered_asset_refs(*groups, exclude: str = "") -> list:
    """按首次出现顺序合并资产 ID。"""
    out, seen = [], set()
    for group in groups:
        if isinstance(group, str):
            group = [group]
        for raw in (group or []):
            rid = str(raw or "").strip()
            if not rid or rid == exclude or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
    return out


class AssetDependencyCycleError(RuntimeError):
    """资产参考图形成闭环，任何一个成员都不可能成为第一张。"""


def asset_dependency_cycles(items: list) -> list:
    """返回资产/任务依赖图里的强连通分量；每一组都是一个真实循环。

    同时接受环节4的资产结构（asset_id/reference_assets）和 tasks.json 的任务结构
    （key/reference_images），便于分析阶段与生产阶段使用同一套判断。
    """
    by_key = {}
    order = {}
    for item in items or []:
        key = str(item.get("key") or item.get("asset_id") or "").strip()
        if key and key not in by_key:
            order[key] = len(order)
            by_key[key] = item
    graph = {}
    for key, item in by_key.items():
        if "key" in item:
            refs = [r.get("asset_id") for r in (item.get("reference_images") or [])]
        else:
            refs = _ordered_asset_refs([item.get("parent_asset_id")],
                                       item.get("reference_assets") or [])
        graph[key] = [str(r) for r in refs if str(r) in by_key]

    index = 0
    stack, on_stack = [], set()
    indexes, lowlinks, cycles = {}, {}, []

    def visit(node):
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for dep in graph[node]:
            if dep not in indexes:
                visit(dep)
                lowlinks[node] = min(lowlinks[node], lowlinks[dep])
            elif dep in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[dep])
        if lowlinks[node] != indexes[node]:
            return
        component = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or (component and component[0] in graph[component[0]]):
            cycles.append(sorted(component, key=lambda x: order[x]))

    for node in graph:
        if node not in indexes:
            visit(node)
    return sorted(cycles, key=lambda c: min(order[x] for x in c))


def asset_layers(tasks: list) -> list:
    """把资产任务按参考图依赖分层：第 0 层没有参考图，第 N 层的参考图都在前面几层。

    为什么必须分层：状态资产 ST007 的父资产或来源图是 ST006，而任务是按环节4 的输出顺序
    排的、并发跑的。ST006 还在生成时 ST007 就被派出去，读不到 ST006.png 就直接
    失败（实测：ST007 重试两次全报「参考图文件不存在」）。
    分层之后层内并发、层间串行，来源资产必然先出完，竞态从根上没了。

    成环时必须在派任务前立即失败并报出成员。把循环组塞进同一层并发并没有消除依赖，
    只会让它们同时读取尚不存在的参考图，看起来像互相等待。
    """
    cycles = asset_dependency_cycles(tasks)
    if cycles:
        detail = "；".join(" ↔ ".join(group) for group in cycles)
        raise AssetDependencyCycleError(
            f"资产循环依赖：{detail}。这些资产互相把对方当参考图，没有任何一个能先生产。"
            "请回到环节4调整 dependency_order/reference_assets：同级资产不得互相引用，"
            "每条引用只能指向更早完成的基础资产或状态资产。")
    by_key = {t["key"]: t for t in tasks}
    layers, placed = [], set()
    rest = list(tasks)
    while rest:
        cur = [t for t in rest
               if all(r.get("asset_id") in placed or r.get("asset_id") not in by_key
                      for r in (t.get("reference_images") or []))]
        if not cur:  # 理论上已被上面的强连通分量检查覆盖；保留防御，绝不静默并发。
            blocked = "、".join(t["key"] for t in rest)
            raise AssetDependencyCycleError(f"资产依赖无法继续分层：{blocked}")
        layers.append(cur)
        placed.update(t["key"] for t in cur)
        rest = [t for t in rest if t["key"] not in placed]
    return layers


def make_ref_resolver(pj: Project, prov, provider_cfg: dict, model: str,
                      ref_side: int, media: str = "image") -> Callable:
    """把参考图引用变成能发出去的形式。

    配了对象存储 → 一律传上去换公网链接。不只是为了那些只收 URL 的接口：
    能吃 data URI 的家，请求体也从几 MB 的 base64 缩成一行链接，快且稳。
    没配 → 转 data URI；碰上只收 URL 的模型就明确报错说去哪配。

    **例外必须按服务商的声明来，不能一刀切上传**（这是踩过的坑）：
      · needs_bytes 的家（multipart）→ 给本机路径
      · accepts_url 为假的家（把参考图内联进某字段、只认裸 base64）→ 给 data URI
    给错形式的后果不是报错，是**参考图被丢掉照样出图** —— 状态资产没了父资产或来源
    参考，脸就不是本人，而且任务标 ok 没人知道。
    """
    up = provider_cfg.get("upload") or {}
    configured = uploader.configured(up) and up.get("mode", "always") != "when_required"
    need_url = prov.needs_url(model, media)
    need_bytes = prov.needs_bytes(model)
    can_url = prov.accepts_url(model, media)
    use_url = need_url or (configured and can_url)

    def resolve(src: str, log: Callable = print) -> str:
        src = (src or "").strip()
        if not src or src.startswith("http"):
            return src
        if need_bytes:
            # 本机绝对路径，provider 自己读字节塞 multipart
            return src if os.path.isabs(src) else os.path.join(pj.root, src)
        if not use_url:
            return resolve_ref(src, pj.root, max_side=ref_side)
        path = src if os.path.isabs(src) else os.path.join(pj.root, src)
        return uploader.to_url(path, up, project_root=pj.root,
                               max_side=ref_side, log=log)

    return resolve


# 提示词里 `Image 1 = C001 名称` 这种映射行。全角冒号/等号都认。
_IMAGE_MAP = re.compile(r"[Ii]mage\s*(\d+)\s*[=＝:：]\s*([A-Za-z0-9_\-]+)")


def check_image_map(prompt: str, want_refs: list) -> tuple:
    """核对提示词里的 Image 编号和程序实际上传顺序是否一致。

    出图模型收到的是 N 张**没有标签**的图，它只知道第 1 张、第 2 张。
    它不认识 `C001` 这个编号 —— 所以提示词必须逐张写 `Image 1 = C001 ...`，
    而且编号顺序必须等于上传顺序（reference_images 按 image_n 排的那个顺序）。

    编号写错的后果不是报错，是**每张参考图都被错误归属**：模型拿病房那张图
    去沿用人脸。图照出、任务标 ok，只能靠肉眼在几百张里发现。

    返回 (硬错误, 提醒)。分两级是因为轻重差很远：
      · 写了映射但对不上 → 硬停。已经确定是错的，出图只会浪费钱。
      · 两张以上却没写映射 → 硬停。模型不可能知道哪张是哪张。
      · 只有一张且没写映射 → 只提醒。顺序上不存在歧义，但仍该补。
    """
    want = [(r.get("image_n") or i + 1, str(r.get("asset_id") or ""))
            for i, r in enumerate(want_refs)]
    if not want:
        return "", ""
    got = _IMAGE_MAP.findall(prompt or "")
    if not got:
        head = ("提示词里没有 `Image N = 资产ID` 的参考图映射，"
                f"但这一条要传 {len(want)} 张参考图（{'、'.join(a for _, a in want)}）。")
        if len(want) >= 2:
            return (head + "两张以上参考图却不说哪张是谁，模型只能猜，"
                    "必然把其中一张的身份用到别处 —— 所以这里停下。"
                    "去「任务明细」改这一条的提示词，逐张写 `Image 1 = 资产ID 名称`，"
                    "顺序和上面括号里一致；或者重跑对应的文字环节。"), ""
        return "", head + "只有一张、顺序上没有歧义，先照常出图，但建议补上。"

    seen = {}
    for n, aid in got:
        seen.setdefault(int(n), aid)        # 同一编号重复出现时取第一次
    problems = []
    for n, aid in want:
        claim = seen.get(n)
        if claim is None:
            problems.append(f"Image {n} 应该是 {aid}，提示词里没提这个编号")
        elif claim != aid:
            problems.append(f"Image {n} 实际传的是 {aid}，提示词里却写成 {claim}")
    extra = sorted(set(seen) - {n for n, _ in want})
    if extra:
        problems.append("提示词里多写了 Image "
                        + "、".join(str(n) for n in extra)
                        + f"（这一条只有 {len(want)} 张）")
    if problems:
        return ("参考图编号和实际上传顺序对不上：" + "；".join(problems)
                + f"。实际上传顺序是 {'、'.join(f'Image {n}={a}' for n, a in want)}。"
                  "编号错位等于每张参考图都被错误归属（拿场景图去沿用人脸），"
                  "出来的东西看着正常但全是错的，所以这里停下。"
                  "去「任务明细」按实际顺序改这一条的映射。"), ""
    return "", ""


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
    to_ref = make_ref_resolver(pj, prov, provider_cfg, model, ref_side, media="image")

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
        want_refs = sorted(task.get("reference_images", []),
                           key=lambda x: x.get("image_n", 0))
        # 先把整批都点一遍再动手解析。一是别为注定失败的任务白传几 MB 上对象存储，
        # 二是要一次说清缺哪几张，而不是缺一张报一张。
        srcs = [(r, r.get("url") or r.get("file_ref") or "") for r in want_refs]
        missing = [r.get("asset_id") or f"第{r.get('image_n', '?')}张"
                   for r, s in srcs if not s]
        if missing:
            # 声明了要这张参考图，却指不到任何文件。以前是 `if src` 跳过 ——
            # 于是「声明 1 张、一张没传」照样出图，出来的脸不是本人，任务还标 ok。
            # 这类静默降级只能靠肉眼在几百张里发现，是最坏的一类错。
            raise RuntimeError(
                f"参考图指不到文件：{'、'.join(missing)}。"
                f"声明了 {len(want_refs)} 张，只解析出 {len(want_refs) - len(missing)} 张 —— "
                f"少一张就出图，脸和场景都会跑掉，所以这里停下。"
                f"多半是环节8 把不存在的东西写进了参考图顺序"
                f"（比如把本段故事板自己写进去），去「任务明细」看这一条的参考图那栏。")
        bad_map, map_warn = check_image_map(prompt, want_refs)
        if bad_map:
            raise RuntimeError(bad_map)
        if map_warn:
            log(f"⚠️ {map_warn}")
        refs = [to_ref(s, log) for _, s in srcs]
        log(f"参考图×{len(refs)}")
        meta = prov.generate_image(
            ImageTask(prompt=prompt, refs=refs, size=want, model=model),
            out, log=log, cancel=cancel, poll_interval=interval, poll_timeout=timeout)
        pj.upsert_registry(kind, {"id": task["key"], "file_ref": task["output"],
                                  "status": "generated", **meta})
        pj.log_event({"stage": kind, "id": task["key"], "result": "ok", **meta})
        # 记一次出图。出图出片是按次计费的，钱主要花在这里，必须入账。
        ledger.record(pj.root, kind="image", stage=kind, target=task["key"],
                      episode=task.get("episode", ""),
                      provider=meta.get("provider", provider_cfg.get("provider", "")),
                      model=meta.get("model", model), count=1, size=want)
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
    to_ref = make_ref_resolver(pj, prov, provider_cfg, model, ref_side, media="video")

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
        refs = [to_ref(sb, log)]
        if task.get("aux_reference"):
            refs.append(to_ref(task["aux_reference"], log))
        log(f"model={model} {p.get('duration', 15)}s {want} 参考图×{len(refs)}")
        meta = prov.generate_video(
            VideoTask(prompt=prompt, refs=refs, duration=int(p.get("duration", 15)),
                      ratio=want, model=model,
                      resolution=provider_cfg.get("resolution", "")),
            out, log=log, cancel=cancel, poll_interval=interval, poll_timeout=timeout)
        pj.upsert_registry("video", {"id": task["key"], "file_ref": task["output"],
                                     "storyboard_ref": sb, "status": "generated", **meta})
        pj.log_event({"stage": "video", "id": task["key"], "result": "ok", **meta})
        # 出片是最贵的一步，必须入账。带上时长——按秒计价的家要用
        ledger.record(pj.root, kind="video", stage="video", target=task["key"],
                      episode=task.get("episode", ""),
                      provider=meta.get("provider", provider_cfg.get("provider", "")),
                      model=meta.get("model", model), count=1,
                      duration=int(p.get("duration", 15)), ratio=want)
        return {"output": task["output"],
                "warn": _ratio_warn(pj, out, want, "video", task["key"],
                                    provider_cfg, model, "video")}

    return worker
