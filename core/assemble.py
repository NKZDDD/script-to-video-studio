"""按集合成成片，以及分段视频的重新抽卡。

拼接清单由用户定：用哪些段、什么顺序、插哪些外部视频。只有本集分段视频时先直接拼接
（不重新编码，快、无损）；有外部视频或直接拼接失败时，统一成本集分段的分辨率和帧率重新编码 ——
各段编码参数不一致时直接拼接要么报错，要么拼出花屏、音画错位的文件。
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from . import agent_projects as A, agent_runtime as R, probe, submitted
from .store import write_json

BACKUP_DIR = "05_分段视频/_抽卡备份"
INSERT_DIR = "06_成片/插入素材"
VIDEO_EXT = (".mp4", ".mov", ".webm")
_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def plan(pj, episode: str) -> dict:
    videos = _videos(pj)
    episodes = sorted({t.get("episode", "") for t in videos})
    rows = [{"rel": t["output"], "key": t["key"], "task": True,
             "ok": R.valid_output(str(A.inside(pj.root, t["output"])))}
            for t in videos if t.get("episode", "") == episode]
    return {"episodes": episodes, "rows": rows, "extras": _extras(pj)}


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


def build(pj, episode: str, items=None) -> dict:
    videos = [t for t in _videos(pj) if t.get("episode", "") == episode]
    outputs = {t["output"] for t in _videos(pj)}
    if items is None:
        if not videos or any(not R.valid_output(str(A.inside(pj.root, t["output"]))) for t in videos):
            raise ValueError("这一集的视频尚未完整生成，不能合成缺段成片")
        items = [t["output"] for t in videos]
    if not items:
        raise ValueError("拼接清单是空的，至少保留一段")
    allowed = (BACKUP_DIR + "/", INSERT_DIR + "/")
    paths = []
    for rel in items:
        if rel not in outputs and not str(rel).startswith(allowed):
            raise ValueError(f"只能拼接本项目的分段视频、抽卡备份或插入素材：{rel}")
        path = A.inside(pj.root, rel)
        if path.suffix.lower() not in VIDEO_EXT or not R.valid_output(str(path)):
            raise ValueError(f"{rel} 不存在或不是有效的视频，先生成或从清单里去掉")
        paths.append(str(path))
    ff = probe.find_ffmpeg()
    if not ff:
        raise ValueError("未找到 ffmpeg")
    safe_ep = "".join(c for c in episode if c.isalnum() or c in "-_") or "EP01"
    rel = "06_成片/" + safe_ep + time.strftime("_MASTER_%Y%m%d_%H%M%S_") + str(time.time_ns())[-6:] + ".mp4"
    output = A.inside(pj.root, rel)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = A.inside(pj.root, f"07_检查与记录/assemble-{time.time_ns()}")
    work.mkdir(parents=True)
    try:
        partial = work / "master.mp4"
        mode, error = "直接拼接", ""
        if all(r in outputs for r in items):
            error = _copy(ff, paths, work, partial)
        if not partial.exists() or error or not R.valid_output(str(partial)):
            mode = "统一规格重新编码"
            partial.unlink(missing_ok=True)
            ref = next((i for i, r in enumerate(items) if r in outputs), 0)
            error = _encode(ff, paths, partial, ref)
            if error or not R.valid_output(str(partial)):
                raise ValueError("成片合成失败：" + (error or "输出文件未通过校验"))
        os.replace(partial, output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(str(output.with_suffix(".json")), {"episode": episode, "items": list(items), "mode": mode,
                                                  "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return {"ok": True, "rel": rel, "mode": mode, "count": len(items)}


def _copy(ff, paths, work, partial) -> str:
    listing = work / "concat.txt"
    listing.write_text("\n".join("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'" for p in paths),
                       encoding="utf-8")
    r = subprocess.run([ff, "-nostdin", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-c", "copy", str(partial)], capture_output=True, timeout=600, creationflags=_WIN)
    return r.stderr.decode("utf-8", errors="replace")[-1000:] if r.returncode else ""


def _encode(ff, paths, partial, ref=0) -> str:
    """以第 ref 段（清单里第一段本集分段）的分辨率和帧率为准，外部素材去适配它。"""
    infos = [probe.media_info(p) for p in paths]
    if any(i is None for i in infos):
        bad = [p for p, i in zip(paths, infos) if i is None]
        return "读不到这些视频的流信息：" + "、".join(Path(p).name for p in bad)
    base = infos[ref]
    w, h = (base["width"] // 2) * 2, (base["height"] // 2) * 2
    fps = base["fps"] if 1 <= base["fps"] <= 60 else 30
    inputs, filters, pairs, idx = [], [], [], 0
    for i, (path, info) in enumerate(zip(paths, infos)):
        inputs += ["-i", path]
        video, idx = idx, idx + 1
        if info["audio"]:
            audio = f"{video}:a:0"
        else:
            # 没有音轨的片段补等长静音，concat 要求每段都有同样的音视频流。
            inputs += ["-f", "lavfi", "-t", f"{max(info['duration'], 0.1):.3f}",
                       "-i", "anullsrc=r=48000:cl=stereo"]
            audio, idx = f"{idx}:a:0", idx + 1
        filters.append(f"[{video}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                       f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p[v{i}]")
        filters.append(f"[{audio}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        pairs.append(f"[v{i}][a{i}]")
    filters.append("".join(pairs) + f"concat=n={len(paths)}:v=1:a=1[v][a]")
    r = subprocess.run([ff, "-nostdin", "-v", "error", *inputs, "-filter_complex", ";".join(filters),
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(partial)],
                       capture_output=True, timeout=3600, creationflags=_WIN)
    return r.stderr.decode("utf-8", errors="replace")[-1000:] if r.returncode else ""


def reroll(pj, key: str) -> dict:
    """重新抽卡：现在这条挪进备份，这一段回到待生产并勾选；提示词、参考图、输出路径都不变。"""
    task = next((t for t in _videos(pj) if t["key"] == key), None)
    if not task:
        raise ValueError("只有分段视频可以重新抽卡，请刷新后再试")
    current = A.inside(pj.root, task["output"])
    if not R.valid_output(str(current)):
        raise ValueError("这一段还没有有效视频，直接生产即可，不需要重新抽卡")
    folder = A.inside(pj.root, BACKUP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / f"{current.stem}_{time.strftime('%Y%m%d_%H%M%S')}{current.suffix}"
    os.replace(current, backup)
    submitted.clear(pj.root, task["output"])
    A.select(pj, {key: True})
    return {"ok": True, "backup": f"{BACKUP_DIR}/{backup.name}"}
