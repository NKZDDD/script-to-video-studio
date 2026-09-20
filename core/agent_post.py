"""Optional subtitles: recognition and burn-in only; text revisions belong to Agent."""
from pathlib import Path
import json
import os
import subprocess
import threading
import time

from . import agent_projects as A, agent_runtime as R, captions, probe, subtitle
from .store import LOCK, read_json, write_json, write_text


def options(pj):
    return {"asr":"bijian", "language":"auto", "subtitle_mode":"hard", "style":"",
            **read_json(pj.p("07_检查与记录", "agent-post.json"), {})}


def run(pj, body, jobs):
    rel = str(body.get("file") or "")
    master = A.inside(pj.root, rel)
    if not rel.startswith("06_成片/") or master.suffix.lower() != ".mp4" or not R.valid_output(str(master)):
        raise ValueError("选择成片目录中有效的 MP4 文件")
    chosen = {k: body.get(k, v) for k, v in options(pj).items() if k in ("asr", "language", "subtitle_mode", "style")}
    if chosen["asr"] not in ("bijian", "jianying", "faster-whisper", "whisper-cpp") or chosen["subtitle_mode"] not in ("hard", "soft"):
        raise ValueError("选择支持的字幕识别引擎与模式")
    st = dict(subtitle.DEFAULTS, **chosen)
    st.update(enabled=True, optimize=False, translate=False, llm_api_key="", llm_api_base="", llm_model="")
    cli = subtitle.find_cli()
    if not cli:
        raise ValueError("VideoCaptioner 未安装，请使用包含字幕依赖的发行包")
    captions.install()
    captions.ensure_ffmpeg()
    bad = subtitle.config_problems(st)
    if bad:
        raise ValueError("；".join(bad))
    srt = master.with_suffix(".srt")
    out = master.with_name(master.stem + "_SUB.mp4")
    if out.exists():
        raise ValueError("带字幕成片已存在；修改字幕或样式时请使用新的成片版本")
    with LOCK:
        if jobs.list(project_root=pj.root, active_only=True):
            raise ValueError("本项目已有任务在运行，请等待完成或停止")
        job = jobs.create("字幕后期", 1, 1, project_root=pj.root, project_name=pj.meta().get("title", ""))
        write_json(pj.p("07_检查与记录", "agent-post.json"), chosen)
        work = A.inside(pj.root, f"07_检查与记录/jobs/{job.id}")
        work.mkdir(parents=True, exist_ok=True)
        cfg = str(work / "caption.toml")
        write_text(cfg, subtitle.build_config(st))
        write_json(str(work / "snapshot.json"), {"created_at": time.time(), "tasks": [{"key":rel}], "settings":chosen})
        job.set_item(rel, state="pending")

    def execute(args):
        logfile = work / ("process-" + str(time.time_ns()) + ".log")
        with logfile.open("wb") as stdout:
            proc = subprocess.Popen(cli + ["--config", cfg] + args, stdout=stdout, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 3600
            while proc.poll() is None:
                if job.cancelled or time.monotonic() >= deadline:
                    proc.kill();proc.wait()
                    raise RuntimeError("字幕任务已停止" if job.cancelled else "字幕处理超过一小时")
                time.sleep(.2)
        if proc.returncode:
            raise RuntimeError(logfile.read_text(encoding="utf-8", errors="replace")[-1800:])

    def go():
        try:
            job.set_item(rel, state="running")
            if not srt.exists() or not srt.stat().st_size:
                job.log(rel, "识别字幕中；不执行文字优化或翻译")
                pending_srt = work / "recognized.srt"
                execute(["transcribe", str(master), "-o", str(pending_srt)])
                if not pending_srt.exists() or not pending_srt.stat().st_size:
                    raise RuntimeError("未返回有效字幕文件")
                os.replace(pending_srt, srt)
            if job.cancelled:
                raise RuntimeError("字幕任务已停止")
            extra = []
            if chosen["style"] and chosen["subtitle_mode"] == "hard":
                w, h = probe.video_size(str(master)) or (0, 0)
                override = subtitle.style_override(chosen["style"], w, h, {})
                if override:
                    extra = ["--style-override", json.dumps(override, ensure_ascii=False)]
            # Isolate incomplete output; cancellation must not create a fake completed master.
            partial = work / "subtitled.mp4"
            job.log(rel, "合成带字幕版本；原成片保留")
            execute(["synthesize", str(master), "-s", str(srt), "-o", str(partial)] + extra)
            if not R.valid_output(str(partial)):
                raise RuntimeError("带字幕视频未通过文件校验")
            os.replace(partial, out)
            job.set_item(rel, state="ok", output=pj.rel(str(out)))
            job.status="done"
        except Exception as exc:
            job.set_item(rel,state="cancelled" if job.cancelled else "failed",msg=str(exc))
            job.status="cancelled" if job.cancelled else "error"
            job.log(rel,str(exc))
        finally:
            job.finished_at=time.time()
            write_json(str(work/"result.json"),job.snapshot())
    threading.Thread(target=go,daemon=True).start()
    return {"ok":True,"job_id":job.id}
