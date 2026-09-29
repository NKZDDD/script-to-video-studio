"""拼接清单（去段、改顺序、插外部视频）与重新抽卡。"""
import copy
import json
from pathlib import Path
import subprocess

import pytest

from core import assemble, probe
from core import agent_projects as A
from core.store import Project, read_json

FF = probe.find_ffmpeg()
pytestmark = pytest.mark.skipif(not FF, reason="需要 ffmpeg")


def clip(path, size="320x240", audio=True, seconds=1):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def pj(tmp_path):
    pj = Project(str(tmp_path))
    pj.init_dirs()
    pj.save_meta({"system": "v61"})
    pj.save_tasks({"video_tasks": [
        {"key": f"EP01-SEG0{i}", "episode": "EP01", "output": f"05_分段视频/EP01-SEG0{i}.mp4",
         "prompt_ref": "03_提示词/p.txt", "params": {"ratio": "4:3", "duration": 1}} for i in (1, 2, 3)]})
    for i in (1, 2):
        clip(pj.p("05_分段视频", f"EP01-SEG0{i}.mp4"))
    return pj


def test_plan_lists_segments_in_script_order_and_marks_missing(pj):
    d = assemble.plan(pj, "EP01")
    assert [r["key"] for r in d["rows"]] == ["EP01-SEG01", "EP01-SEG02", "EP01-SEG03"]
    assert [r["ok"] for r in d["rows"]] == [True, True, False] and d["episodes"] == ["EP01"]


def test_default_still_refuses_a_master_with_missing_segments(pj):
    with pytest.raises(ValueError, match="尚未完整生成"):
        assemble.build(pj, "EP01")


def _framehash(path):
    r = subprocess.run([FF, "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framehash", "-hash", "md5", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    return [ln.split(",")[-1].strip() for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]


def test_same_spec_segments_keep_the_source_frames(pj):
    """规格一致时画面一帧不重压。重编码会让解码后的帧哈希对不上。"""
    src = pj.p("05_分段视频", "EP01-SEG01.mp4")
    src_frames = _framehash(src)
    d = assemble.build(pj, "EP01", ["05_分段视频/EP01-SEG01.mp4", "05_分段视频/EP01-SEG02.mp4"])
    assert "画面不重编码" in d["mode"]
    assert _framehash(pj.p(*d["rel"].split("/")))[:len(src_frames)] == src_frames


def test_project_segments_of_different_picture_are_refused(pj):
    clip(pj.p("05_分段视频", "EP01-SEG02.mp4"), size="640x360")
    with pytest.raises(ValueError, match="不会重编码分段视频"):
        assemble.build(pj, "EP01", ["05_分段视频/EP01-SEG01.mp4", "05_分段视频/EP01-SEG02.mp4"])
    assert not list(Path(pj.p("06_成片")).glob("*.mp4"))


def test_chosen_segments_in_chosen_order_are_joined(pj):
    items = ["05_分段视频/EP01-SEG02.mp4", "05_分段视频/EP01-SEG01.mp4"]
    d = assemble.build(pj, "EP01", items)
    assert "声音对齐画面" in d["mode"] and d["count"] == 2
    out = Path(pj.p(*d["rel"].split("/")))
    assert probe.media_info(str(out))["duration"] == pytest.approx(2, abs=0.3)
    assert read_json(str(out.with_suffix(".json")))["items"] == items


def _beep_clip(path, color, audio_seconds, rate):
    """3 秒画面；声音开头一声短提示音，总长 audio_seconds（可以比画面长或短）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=25:duration=3",
                    "-f", "lavfi", "-t", "0.3", "-i", f"sine=frequency=880:sample_rate={rate}",
                    "-f", "lavfi", "-t", str(audio_seconds), "-i", f"anullsrc=r={rate}:cl=stereo",
                    "-filter_complex", f"[1:a]aformat=channel_layouts=stereo[t];[t][2:a]concat=n=2:v=0:a=1,"
                                       f"atrim=end={audio_seconds}[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True)


def _beeps(path):
    import re
    r = subprocess.run([FF, "-hide_banner", "-i", str(path), "-af", "silencedetect=n=-40dB:d=0.1", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [0.0] + [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]


def test_each_segment_sound_starts_with_its_picture(pj):
    """★ 分段的音轨比画面长 / 短、采样率不一，拼完每一段的声音仍从它的画面开始。

    以前直接首尾相接：一段声音比画面长 0.5 秒，后面每一段的声音都晚 0.5 秒，一段段累加；
    采样率不同时声音还按错的速度播。ffmpeg 不报错，片子看着正常、声音全错位。
    """
    specs = [("red", 3.4, 44100), ("blue", 2.7, 48000), ("green", 3.2, 48000), ("yellow", 3.5, 44100)]
    tasks = pj.tasks()
    tasks["video_tasks"] = [{"key": f"EP01-SEG0{i}", "episode": "EP01", "output": f"05_分段视频/EP01-SEG0{i}.mp4",
                             "prompt_ref": "03_提示词/p.txt", "params": {"ratio": "4:3", "duration": 3}}
                            for i in range(1, 5)]
    pj.save_tasks(tasks)
    for i, (color, seconds, rate) in enumerate(specs, 1):
        _beep_clip(pj.p("05_分段视频", f"EP01-SEG0{i}.mp4"), color, seconds, rate)
    d = assemble.build(pj, "EP01")
    out = pj.p(*d["rel"].split("/"))
    starts = _beeps(out)[:4]
    assert starts == pytest.approx([0, 3, 6, 9], abs=0.08), starts
    assert probe.media_info(out)["duration"] == pytest.approx(12, abs=0.15)


def test_external_intro_with_other_size_and_no_audio_is_fitted_to_the_segments(pj):
    ext = clip(pj.p("06_成片", "插入素材", "片头.mp4"), size="640x360", audio=False)
    assert probe.media_info(str(ext))["audio"] is False
    d = assemble.build(pj, "EP01", ["06_成片/插入素材/片头.mp4", "05_分段视频/EP01-SEG01.mp4"])
    info = probe.media_info(pj.p(*d["rel"].split("/")))
    assert "单独适配" in d["mode"] and "画面原样" in d["mode"]
    assert (info["width"], info["height"]) == (320, 240) and info["audio"]
    assert info["duration"] == pytest.approx(2, abs=0.3)


@pytest.mark.parametrize("rel", ["02_固定资产/a.png", "../outside.mp4", "05_分段视频/EP01-SEG03.mp4"])
def test_only_valid_project_videos_can_be_assembled(pj, rel):
    with pytest.raises(ValueError):
        assemble.build(pj, "EP01", [rel])
    assert not list(Path(pj.p("06_成片")).glob("*.mp4"))


def test_reroll_moves_current_take_to_backup_and_selects_the_segment(pj):
    d = assemble.reroll(pj, "EP01-SEG01")
    assert not Path(pj.p("05_分段视频", "EP01-SEG01.mp4")).exists()
    assert Path(pj.p(*d["backup"].split("/"))).is_file()
    assert read_json(pj.p("07_检查与记录", "selections.json"))["EP01-SEG01"] is True
    assert [x["rel"] for x in assemble.plan(pj, "EP01")["extras"]] == [d["backup"]]
    with pytest.raises(ValueError, match="还没有有效视频"):
        assemble.reroll(pj, "EP01-SEG01")
    assemble.build(pj, "EP01", [d["backup"], "05_分段视频/EP01-SEG02.mp4"])


def _add_ep02(pj):
    tasks = pj.tasks()
    tasks["video_tasks"].append({"key": "EP02-SEG01", "episode": "EP02", "output": "05_分段视频/EP02-SEG01.mp4",
                                 "prompt_ref": "03_提示词/p.txt", "params": {"ratio": "4:3", "duration": 1}})
    pj.save_tasks(tasks)
    clip(pj.p("05_分段视频", "EP02-SEG01.mp4"))


def test_several_episodes_each_get_their_own_master(pj):
    """勾 EP01、EP02 就出两个成片，不拼成一个文件。"""
    _add_ep02(pj)
    d = assemble.plan(pj, ["EP02", "EP01"])
    assert d["chosen"] == ["EP01", "EP02"]
    assert [r["key"] for r in d["rows"]] == ["EP01-SEG01", "EP01-SEG02", "EP01-SEG03", "EP02-SEG01"]
    with pytest.raises(ValueError, match="EP01 的视频尚未完整生成"):
        assemble.build(pj, ["EP01", "EP02"])
    out = assemble.build(pj, ["EP01", "EP02"], [r["rel"] for r in d["rows"] if r["ok"]])
    assert [m["episode"] for m in out["masters"]] == ["EP01", "EP02"]
    assert [m["count"] for m in out["masters"]] == [2, 1]
    for m, seconds in zip(out["masters"], (2, 1)):
        master = Path(pj.p(*m["rel"].split("/")))
        assert master.name.startswith(m["episode"] + "_MASTER_") and "声音对齐画面" in m["mode"]
        assert probe.media_info(str(master))["duration"] == pytest.approx(seconds, abs=0.3)
        assert read_json(str(master.with_suffix(".json")))["episode"] == m["episode"]
    assert not list(Path(pj.p("06_成片")).glob("FULL_*")) and not list(Path(pj.p("06_成片")).glob("EP01-EP02*"))
    with pytest.raises(ValueError, match="没有分段视频"):
        assemble.plan(pj, ["EP09"])


def test_an_inserted_video_belongs_to_the_episode_above_it(pj):
    _add_ep02(pj)
    ext = clip(pj.p("06_成片", "插入素材", "片尾.mp4"), size="640x360", audio=False)
    items = ["06_成片/插入素材/片头.mp4", "05_分段视频/EP01-SEG01.mp4", "06_成片/插入素材/片尾.mp4",
             "05_分段视频/EP02-SEG01.mp4"]
    clip(pj.p("06_成片", "插入素材", "片头.mp4"), size="640x360", audio=False)
    assert ext.is_file()
    out = assemble.build(pj, ["EP01", "EP02"], items)
    first, second = out["masters"]
    assert read_json(str(Path(pj.p(*first["rel"].split("/"))).with_suffix(".json")))["items"] == items[:3]
    assert first["count"] == 3 and second["count"] == 1
    with pytest.raises(ValueError, match="EP02 一段都没勾"):
        assemble.build(pj, ["EP01", "EP02"], ["05_分段视频/EP01-SEG01.mp4"])


def test_inserted_upload_must_be_a_video_and_never_overwrites(pj):
    raw = Path(pj.p("05_分段视频", "EP01-SEG01.mp4")).read_bytes()
    a = assemble.save_insert(pj, "片尾.mp4", raw)
    b = assemble.save_insert(pj, "片尾.mp4", raw)
    assert a["rel"] != b["rel"] and all(Path(pj.p(*x["rel"].split("/"))).is_file() for x in (a, b))
    with pytest.raises(ValueError):
        assemble.save_insert(pj, "说明.txt", b"hello")
    with pytest.raises(ValueError, match="有效的视频"):
        assemble.save_insert(pj, "坏的.mp4", b"not a video" * 200)


def _wait(job, seconds=120):
    import time
    until = time.monotonic() + seconds
    while job.status == "running" and time.monotonic() < until:
        time.sleep(0.1)
    assert job.status != "running", job.snapshot()
    return job


def test_assembly_runs_in_the_background_one_episode_per_item(pj):
    """几十集要合一两个小时 —— 请求当场返回，编码在后台逐集跑，每一集是任务明细里的一行。"""
    from core.executor import JobManager
    _add_ep02(pj)
    clip(pj.p("05_分段视频", "EP01-SEG03.mp4"))
    jobs = JobManager()
    d = assemble.start(pj, ["EP01", "EP02"], None, jobs)
    assert d["episodes"] == ["EP01", "EP02"] and d["count"] == 4
    job = jobs.get(d["job_id"])
    assert job.kind == "assemble" and set(job.items) == {"EP01", "EP02"}
    with pytest.raises(ValueError, match="正在合成"):
        if job.status == "running":
            assemble.start(pj, ["EP02"], None, jobs)
        else:
            raise ValueError("正在合成（已经合完，跳过这项检查）")
    _wait(job)
    assert job.status == "done"
    for ep in ("EP01", "EP02"):
        item = job.items[ep]
        assert item["state"] == "ok" and Path(pj.p(*item["output"].split("/"))).is_file()
    saved = read_json(pj.p("07_检查与记录", "jobs", job.id, "result.json"))
    assert saved["kind"] == "assemble" and saved["status"] == "done"


def test_stopping_interrupts_the_episode_being_encoded(pj, monkeypatch):
    import time
    from core.executor import JobManager
    real = assemble._join

    def slow(ff, paths, partial, ref=0, keep=(), cancel=None):
        until = time.monotonic() + 20
        while time.monotonic() < until:
            if cancel and cancel():
                return "已停止", ""
            time.sleep(0.05)
        return real(ff, paths, partial, ref, keep, cancel)
    monkeypatch.setattr(assemble, "_join", slow)
    _add_ep02(pj)
    jobs = JobManager()
    job = jobs.get(assemble.start(pj, ["EP01", "EP02"], [
        "05_分段视频/EP01-SEG01.mp4", "05_分段视频/EP02-SEG01.mp4"], jobs)["job_id"])
    until = time.monotonic() + 5
    while job.items["EP01"]["state"] != "running" and time.monotonic() < until:
        time.sleep(0.05)
    job.cancel()
    until = time.monotonic() + 10
    while job.finished_at is None and time.monotonic() < until:
        time.sleep(0.05)
    assert job.status == "cancelled"
    assert job.items["EP01"]["state"] == "cancelled" and job.items["EP02"]["state"] == "cancelled"
    assert not list(Path(pj.p("06_成片")).glob("EP0*_MASTER_*.mp4"))


def test_routes_through_the_http_layer(pj, monkeypatch):
    import base64, os
    from server import agent_api, app
    cfg = {"projects_dir": os.path.dirname(pj.root), "providers": {}, "agent_features": {"post": True},
           "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: [])
    root = pj.root
    assert len(agent_api.get(app, "/api/agent/assemble-plan", {"root": [root], "episode": ["EP01"]})["rows"]) == 3
    raw = base64.b64encode(Path(pj.p("05_分段视频", "EP01-SEG02.mp4")).read_bytes()).decode()
    up = agent_api.post(app, "/api/agent/assemble/upload", {"root": root, "filename": "x.mp4", "content_b64": raw})
    d = agent_api.post(app, "/api/agent/assemble", {"root": root, "episode": "EP01",
                                                    "items": [up["rel"], "05_分段视频/EP01-SEG01.mp4"]})
    job = _wait(app.JOBS.get(d["job_id"]))
    assert job.status == "done" and "画面不重编码" in job.items["EP01"]["msg"]
    assert len(agent_api.get(app, "/api/agent/assemble-plan", {"root": [root], "episodes": ["EP01"]})["rows"]) == 3
    d = agent_api.post(app, "/api/agent/assemble", {"root": root, "episodes": ["EP01"],
                                                    "items": ["05_分段视频/EP01-SEG01.mp4"]})
    assert d["count"] == 1 and _wait(app.JOBS.get(d["job_id"])).status == "done"
    started = []
    monkeypatch.setattr(agent_api.R, "start", lambda *a, **kw: started.append(a[5]) or {"ok": True, "job_id": "job-x"})
    d = agent_api.post(app, "/api/agent/reroll", {"root": root, "key": "EP01-SEG02"})
    assert d["backup"] and d["job_id"] == "job-x" and started == [["EP01-SEG02"]]


def test_reroll_that_cannot_start_puts_the_video_back(pj, monkeypatch):
    import os
    from server import agent_api, app
    cfg = {"projects_dir": os.path.dirname(pj.root), "providers": {}, "agent_features": {"post": True},
           "limits": {"global": 8, "per_provider": {}}}
    monkeypatch.setattr(app, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, "caps_of", lambda cfg=None: [])
    monkeypatch.setattr(agent_api.R, "start", lambda *a, **kw: {"ok": False, "blocked": [{"key": "EP01-SEG01", "reason": "模型不在当前能力清单"}]})
    before = Path(pj.p("05_分段视频", "EP01-SEG01.mp4")).read_bytes()
    with pytest.raises(ValueError, match="原视频已放回原处：模型不在当前能力清单"):
        agent_api.post(app, "/api/agent/reroll", {"root": pj.root, "key": "EP01-SEG01"})
    assert Path(pj.p("05_分段视频", "EP01-SEG01.mp4")).read_bytes() == before
    assert not list(Path(pj.p("05_分段视频", "_抽卡备份")).glob("*.mp4"))
