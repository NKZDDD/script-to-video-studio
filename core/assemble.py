"""按集合成成片（选几集就每集各出一个成片），以及分段视频的重新抽卡。

拼接清单由用户定：用哪些段、什么顺序、插哪些外部视频。画面码流原样拷贝，不重新压缩。
每一段的声音裁 / 补到这一段画面的长度再接 —— 各段音轨长短、采样率不一致时，
首尾直接相接会一路音画错位，而且不报错。
分辨率或编码和本集分段对不上的外部视频，只重编码那一段去适配；分段本身仍然原样。
分段之间规格不同就停下来说明，不把已经生成的画面压一遍。
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from . import agent_projects as A, agent_runtime as R, probe, submitted
from .store import write_json

BACKUP_DIR = "05_分段视频/_抽卡备份"
INSERT_DIR = "06_成片/插入素材"
VIDEO_EXT = (".mp4", ".mov", ".webm")
_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_START_LOCK = threading.Lock()


def _videos(pj):
    return [t for t in A.task_rows(pj.tasks(), legacy=True) if t["kind"] == "video"]


def _extras(pj) -> list:
    """可以插进清单的外部视频：抽卡备份和插入素材两个目录里的文件，新的在前。"""
    rows = []
    for folder in (BACKUP_DIR, INSERT_DIR):
        base = A.inside(pj.root, folder)
        for p in base.iterdir() if base.is_dir() else []:
            if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                rows.append({"rel": f"{folder}/{p.name}", "name": p.name,
                             "from": "抽卡备份" if folder == BACKUP_DIR else "插入素材",
                             "mtime": p.stat().st_mtime})
    return sorted(rows, key=lambda r: -r["mtime"])


def _chosen(pj, episodes) -> list:
    """要合成的集，按集号顺序。一集时传字符串，多集 / 全集传列表。"""
    wanted = [episodes] if isinstance(episodes, str) else list(episodes or [])
    known = sorted({t.get("episode", "") for t in _videos(pj)})
    unknown = [e for e in wanted if e not in known]
    if unknown:
        raise ValueError("这些集没有分段视频：" + "、".join(unknown))
    return [e for e in known if e in wanted]


def _in_order(pj, episodes) -> list:
    """所选各集的分段，先按集、集内按剧本顺序。"""
    rank = {e: i for i, e in enumerate(episodes)}
    videos = [t for t in _videos(pj) if t.get("episode", "") in rank]
    return sorted(videos, key=lambda t: rank[t.get("episode", "")])


def plan(pj, episodes) -> dict:
    videos = _videos(pj)
    chosen = _chosen(pj, episodes)
    rows = [{"rel": t["output"], "key": t["key"], "task": True, "episode": t.get("episode", ""),
             "ok": R.valid_output(str(A.inside(pj.root, t["output"])))}
            for t in _in_order(pj, chosen)]
    return {"episodes": sorted({t.get("episode", "") for t in videos}), "chosen": chosen,
            "rows": rows, "extras": _extras(pj)}


def save_insert(pj, filename: str, raw: bytes) -> dict:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(filename or "")).strip(" .")
    if Path(name).suffix.lower() not in VIDEO_EXT:
        raise ValueError("只能插入 mp4 / mov / webm 视频")
    folder = A.inside(pj.root, INSERT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.exists():
        target = folder / f"{target.stem}_{time.strftime('%Y%m%d_%H%M%S')}{target.suffix}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(target)
    if not R.valid_output(str(target)):
        target.unlink()
        raise ValueError("文件不是有效的视频")
    return {"rel": f"{INSERT_DIR}/{target.name}", "name": target.name}


def _split(pj, chosen: list, items: list) -> dict:
    """清单按集分开，每集一个成片。

    分段视频归它自己那一集；插进来的外部视频（抽卡备份、插入素材）归它**上面最近的那一段**
    所在的集，排在最前面的归它下面第一段的集 —— 和人在清单里看到的位置一致。
    """
    owner = {t["output"]: t.get("episode", "") for t in _videos(pj)}
    marks = [owner.get(rel) for rel in items]
    groups = {e: [] for e in chosen}
    for i, rel in enumerate(items):
        ep = marks[i]
        if ep is None:
            before = [m for m in marks[:i] if m is not None]
            after = [m for m in marks[i + 1:] if m is not None]
            ep = before[-1] if before else (after[0] if after else chosen[0])
        if ep not in groups:
            raise ValueError(f"{rel} 属于 {ep or '未指定集'}，不在这次选的集里，刷新清单后再试")
        groups[ep].append(rel)
    empty = [e for e in chosen if not groups[e]]
    if empty:
        raise ValueError(f"{'、'.join(empty)} 一段都没勾，要么勾上几段，要么取消选这一集")
    return groups


def _prepare(pj, episodes, items) -> tuple:
    """把所有集的清单核对完：(所选集, 每集清单, 本项目分段输出, ffmpeg)。只读，秒级。"""
    chosen = _chosen(pj, episodes)
    if not chosen:
        raise ValueError("先选要合成的集")
    outputs = {t["output"] for t in _videos(pj)}
    if items is None:
        groups = {}
        for ep in chosen:
            videos = _in_order(pj, [ep])
            if not videos or any(not R.valid_output(str(A.inside(pj.root, t["output"]))) for t in videos):
                raise ValueError(f"{ep} 的视频尚未完整生成，不能合成缺段成片")
            groups[ep] = [t["output"] for t in videos]
    else:
        if not items:
            raise ValueError("拼接清单是空的，至少保留一段")
        groups = _split(pj, chosen, list(items))
    allowed = (BACKUP_DIR + "/", INSERT_DIR + "/")
    for rel in (r for rels in groups.values() for r in rels):
        if rel not in outputs and not str(rel).startswith(allowed):
            raise ValueError(f"只能拼接本项目的分段视频、抽卡备份或插入素材：{rel}")
        path = A.inside(pj.root, rel)
        if path.suffix.lower() not in VIDEO_EXT or not R.valid_output(str(path)):
            raise ValueError(f"{rel} 不存在或不是有效的视频，先生成或从清单里去掉")
    ff = probe.find_ffmpeg()
    if not ff:
        raise ValueError("未找到 ffmpeg")
    return chosen, groups, outputs, ff


def build(pj, episodes, items=None) -> dict:
    """每一集各出一个成片，当场合成完再返回。先把所有集的清单核对完，再逐集合成 —— 不会合出一半才报错。"""
    chosen, groups, outputs, ff = _prepare(pj, episodes, items)
    masters = [_build_one(pj, ff, ep, groups[ep], outputs) for ep in chosen]
    first = masters[0]
    return {"ok": True, "masters": masters, "episodes": chosen,
            "rel": first["rel"], "mode": first["mode"], "count": sum(m["count"] for m in masters)}


def _assembling(jobs, root: str) -> set:
    """本项目正在后台合成、还没合完的集。"""
    out = set()
    for job in list(jobs.jobs.values()):
        if job.project_root == root and job.kind == "assemble" and job.status == "running":
            out |= {k for k, v in list(job.items.items()) if v.get("state") in ("pending", "running")}
    return out


def start(pj, episodes, items, jobs) -> dict:
    """后台合成：清单当场核对，编码在后台逐集跑，页面可以接着做别的事。

    几十集一次合要一两个小时，放在请求里同步跑的话页面一直被挡着。
    每一集是任务明细里的一行，做完一集就能打开那一集的成片；停止会打断正在跑的那一集。
    """
    chosen, groups, outputs, ff = _prepare(pj, episodes, items)
    with _START_LOCK:
        running = sorted(_assembling(jobs, pj.root) & set(chosen))
        if running:
            raise ValueError(f"{'、'.join(running)} 正在合成，等它合完或先停止那一批")
        job = jobs.create("assemble", len(chosen), 1, project_root=pj.root,
                          project_name=pj.meta().get("title", ""), provider="ffmpeg")
        for ep in chosen:
            job.set_item(ep, state="pending", msg=f"{len(groups[ep])} 段，排队中")

    def go():
        try:
            for ep in chosen:
                if job.cancelled:
                    job.set_item(ep, state="cancelled", msg="已停止，这一集没合")
                    continue
                job.set_item(ep, state="running", msg=f"正在合成 {len(groups[ep])} 段")
                try:
                    m = _build_one(pj, ff, ep, groups[ep], outputs, cancel=lambda: job.cancelled)
                    job.set_item(ep, state="ok", output=m["rel"], msg=f"{m['mode']}，共 {m['count']} 段")
                except Exception as exc:                    # noqa: BLE001
                    job.set_item(ep, state="cancelled" if job.cancelled else "failed", msg=str(exc)[:500])
                    job.log(ep, str(exc)[:500])
            counts = job.counts()
            job.status = "cancelled" if job.cancelled else ("error" if counts.get("failed") else "done")
        finally:
            job.finished_at = time.time()
            write_json(str(A.inside(pj.root, f"07_检查与记录/jobs/{job.id}/result.json")), job.snapshot())
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True, "job_id": job.id, "episodes": chosen, "count": sum(len(g) for g in groups.values())}


def _build_one(pj, ff, episode: str, items: list, outputs: set, cancel=None) -> dict:
    paths = [str(A.inside(pj.root, rel)) for rel in items]
    safe_ep = "".join(c for c in episode if c.isalnum() or c in "-_") or "EP01"
    rel = "06_成片/" + safe_ep + time.strftime("_MASTER_%Y%m%d_%H%M%S_") + str(time.time_ns())[-6:] + ".mp4"
    output = A.inside(pj.root, rel)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = A.inside(pj.root, f"07_检查与记录/assemble-{time.time_ns()}")
    work.mkdir(parents=True)
    try:
        partial = work / "master.mp4"
        # 画面原样拷贝。音轨仍要按每一段画面的长度重写：AI 出的分段音轨常比画面长一点或短一点、
        # 采样率也各家不一，直接首尾相接会把后面所有段的声音往后推，而且 ffmpeg 不报错。
        ref = next((i for i, r in enumerate(items) if r in outputs), 0)
        keep = {i for i, r in enumerate(items) if r in outputs}
        error, mode = _join(ff, paths, partial, ref, keep, cancel=cancel)
        if error or not R.valid_output(str(partial)):
            raise ValueError(f"{episode} 成片合成失败：" + (error or "输出文件未通过校验"))
        os.replace(partial, output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(str(output.with_suffix(".json")), {"episode": episode, "items": list(items), "mode": mode,
                                                  "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return {"episode": episode, "rel": rel, "mode": mode, "count": len(items)}


def _picture(info) -> tuple:
    return (info.get("codec") or "", info["width"], info["height"], info.get("pix_fmt") or "")


def _picture_text(info) -> str:
    codec, w, h, pix = _picture(info)
    return f"{w}x{h} {codec or '未知编码'} {pix or '未知像素格式'}"


def _join(ff, paths, partial, ref=0, keep=(), cancel=None):
    """画面原样拼接，返回 (错误, 方式)。错误为空表示成功。

    每一段先做成临时文件：视频轨 `-c copy`，声音裁 / 补到这一段画面的长度。
    再把临时文件无损接起来，最后只把声音收成一条 AAC —— 垫在每段末尾的 AAC 帧
    会把下一段的声音往后推，所以不能逐段压 AAC 再拷贝。

    `keep` 是本项目分段的下标。它们规格和基准段不同就停下，不重编码。
    外部视频规格不同时，只重编码那一段去适配基准段。
    """
    infos = [probe.media_info(p) for p in paths]
    if any(i is None for i in infos):
        bad = [p for p, i in zip(paths, infos) if i is None]
        return "读不到这些视频的流信息：" + "、".join(Path(p).name for p in bad), ""
    base = infos[ref]
    keep = set(keep or ())
    fitted = 0
    parts = []
    deadline = time.monotonic() + 3600
    for i, (path, info) in enumerate(zip(paths, infos)):
        if cancel and cancel():
            return "已停止", ""
        dest = partial.parent / f"part_{i}.mkv"
        if _picture(info) == _picture(base):
            error = _copy_part(ff, path, info, dest, cancel, deadline)
        elif i in keep:
            return (f"{Path(path).name} 的画面是 {_picture_text(info)}，本集用来对齐的分段是 {_picture_text(base)}。"
                    "原画质拼接不会重编码分段视频，请换成同一分辨率和编码后再合成", "")
        else:
            error = _fit_part(ff, path, info, base, dest, cancel, deadline)
            if not error:
                fitted += 1
        if error:
            return error, ""
        parts.append(dest)
    script = partial.parent / "list.txt"
    lines = ["file '" + p.resolve().as_posix().replace("'", r"'\''") + "'" for p in parts]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    error = _run([ff, "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(script),
                  "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", str(partial)], cancel, deadline)
    if error:
        return error, ""
    if fitted:
        mode = (f"原画质拼接（{len(paths) - fitted} 段画面原样，{fitted} 段规格不同已单独适配，声音对齐画面）")
    else:
        mode = "原画质拼接（画面不重编码，声音对齐画面）"
    return "", mode


def _span(path, info) -> float:
    # 每一段的声音都裁 / 补到**这一段画面**的长度。concat 按每段最长的那条流接下一段：
    # 声音比画面长 0.5 秒，下一段的声音就晚 0.5 秒出来，而且一段段往后累加。
    return max(probe.video_duration(path, info["fps"]), 0.1)


def _copy_part(ff, path, info, dest, cancel, deadline) -> str:
    span = f"{_span(path, info):.3f}"
    audio = (f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
             f"asetpts=PTS-STARTPTS,apad,atrim=end={span}")
    if info["audio"]:
        cmd = [ff, "-nostdin", "-y", "-v", "error", "-i", path, "-map", "0:v:0", "-map", "0:a:0",
               "-c:v", "copy", "-af", audio, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(dest)]
    else:
        cmd = [ff, "-nostdin", "-y", "-v", "error", "-i", path, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
               "-filter_complex", f"[1:a]atrim=end={span},asetpts=PTS-STARTPTS[a]",
               "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "pcm_s16le", str(dest)]
    return _run(cmd, cancel, deadline)


def _fit_part(ff, path, info, base, dest, cancel, deadline) -> str:
    """外部视频和分段规格不同：只重编码这一段，缩放到分段的宽高。"""
    name = Path(path).name
    w, h = base["width"], base["height"]
    if base.get("codec") != "h264" or base.get("pix_fmt") != "yuv420p" or w % 2 or h % 2:
        return (f"{name} 的画面是 {_picture_text(info)}，和本集分段 {_picture_text(base)} 不同。"
                "原画质拼接不能改分段的画面，这一段也无法适配进去，请去掉或先导出成相同规格")
    fps = base["fps"] if 1 <= base["fps"] <= 60 else 30
    span = f"{_span(path, info):.3f}"
    video = (f"[0:v:0]setpts=PTS-STARTPTS,scale={w}:{h}:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p[v]")
    inputs = ["-i", path]
    if info["audio"]:
        graph = (video + f";[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"asetpts=PTS-STARTPTS,apad,atrim=end={span}[a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        inputs += ["-f", "lavfi", "-t", span, "-i", "anullsrc=r=48000:cl=stereo"]
        graph = video
        maps = ["-map", "[v]", "-map", "1:a:0"]
    return _run([ff, "-nostdin", "-y", "-v", "error", *inputs, "-filter_complex", graph, *maps,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                 "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(dest)], cancel, deadline)


def _run(cmd, cancel, deadline) -> str:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=_WIN)
    while True:
        try:
            _, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            stop = "已停止" if cancel and cancel() else "合成超过一小时" if time.monotonic() > deadline else ""
            if stop:
                proc.kill()
                proc.communicate()
                return stop
    return err.decode("utf-8", errors="replace")[-1000:] if proc.returncode else ""


def reroll(pj, key: str) -> dict:
    """图片或视频先备份再重抽；提示词、参考图、输出路径保持不变。"""
    task = next((t for t in A.task_rows(pj.tasks(), legacy=True) if t["key"] == key), None)
    if not task:
        raise ValueError("任务不存在，请刷新后再试")
    current = A.inside(pj.root, task["output"])
    if not R.valid_output(str(current)):
        media = "视频" if task["kind"] == "video" else "图片"
        raise ValueError(f"这一项还没有有效{media}，直接生产即可，不需要重新抽卡")
    backup_dir = (BACKUP_DIR if task["kind"] == "video" else
                  (current.parent.relative_to(Path(pj.root).resolve()) / "_抽卡备份").as_posix())
    folder = A.inside(pj.root, backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / f"{current.stem}_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}{current.suffix}"
    os.replace(current, backup)
    submitted.clear(pj.root, task["output"])
    A.select(pj, {key: True})
    return {"ok": True, "backup": f"{backup_dir}/{backup.name}"}


def undo_reroll(pj, key: str, backup_rel: str) -> None:
    """重新抽卡没能开跑：把原产物放回原处。"""
    task = next((t for t in A.task_rows(pj.tasks(), legacy=True) if t["key"] == key), None)
    backup = A.inside(pj.root, backup_rel)
    if task and backup.is_file():
        current = A.inside(pj.root, task["output"])
        if not current.exists():
            os.replace(backup, current)
