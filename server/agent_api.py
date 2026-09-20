"""HTTP surface for the Agent workspace. Legacy analysis APIs are not exposed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

from core import agent_projects as A, agent_runtime as R, diagnose
from core.store import Project, list_projects, read_json, write_json, write_text


def projects(cfg):
    bases = [cfg["projects_dir"]] + list(cfg.get("agent_project_dirs") or [])
    found = {}
    for base in bases:
        for row in list_projects(base):
            meta = Project(row["root"]).meta()
            row.update(workflow=meta.get("workflow", "legacy"), archived=bool(meta.get("archived")),
                       tags=meta.get("tags") or [])
            active = read_json(Project(row["root"]).p("07_检查与记录", "active-plan.json"), {}) or {}
            row["state"] = "已归档" if row["archived"] else ("待 Agent 产出" if row["workflow"] == "agent" and not active else "可查看生产")
            if not row["archived"] and diagnose.load(row["root"]):
                row["state"] = "有失败"
            if active:
                row["updated"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(active["activated_at"]))
            found[os.path.normcase(os.path.abspath(row["root"]))] = row
    return list(found.values())


def project(root, cfg):
    key = os.path.normcase(os.path.abspath(root or ""))
    if key not in {os.path.normcase(os.path.abspath(r["root"])) for r in projects(cfg)}:
        raise ValueError("项目未登记，请从项目列表打开")
    return Project(root)


def bootstrap(app, cfg):
    old = app.api_get("/api/bootstrap", {})
    caps = old["capabilities"]
    for cap in caps:
        cap.pop("llm", None)
        cap["supports"] = [x for x in cap.get("supports", []) if x in ("image", "video")]
        if "key_fields" in cap:
            cap["key_fields"] = [x for x in cap["key_fields"] if "llm" not in str(x)]
    return {"projects": projects(cfg), "projects_dir": cfg["projects_dir"], "capabilities": caps,
            "providers_public": old["providers_public"], "provider_accounts": old["provider_accounts"],
            "provider_keys_configured": {k: {s: v for s, v in slots.items() if "llm" not in s}
                                         for k, slots in old["provider_keys_configured"].items()},
            "features": cfg.get("agent_features", {}), "feature_labels": A.FEATURES,
            "limits": cfg["limits"], "defaults": A.DEFAULTS, "kinds": A.KINDS,
            "semantics": A.SEMANTICS, "upload": old["config"].get("upload", {}),
            "paths": old["paths"]}


def get(app, path, q):
    cfg = app.load_config()
    if path == "/api/agent/bootstrap":
        return bootstrap(app, cfg)
    if path == "/api/agent/projects":
        return {"projects": projects(cfg)}
    if path == "/api/agent/runtime":
        return R.runtime(app.JOBS)
    pj = project((q.get("root") or [""])[0], cfg)
    if path == "/api/agent/project":
        data = A.view(pj)
        data["production"] = R.settings(pj, app.caps_of(cfg), cfg)
        for t in data["tasks"]:
            t["done"] = R.valid_output(str(A.inside(pj.root, t["output"])))
        data["failures"] = diagnose.load(pj.root)
        data["jobs"] = app.JOBS.list(project_root=pj.root)
        known = {j["id"] for j in data["jobs"]}
        for folder in sorted(Path(pj.p("07_检查与记录", "jobs")).glob("job*"), reverse=True)[:30]:
            if folder.name in known:
                continue
            saved = read_json(str(folder / "result.json"))
            if saved:
                data["jobs"].append(saved)
            else:
                snap = read_json(str(folder / "snapshot.json"), {})
                if snap:
                    data["jobs"].append({"id":folder.name,"kind":"production","status":"interrupted",
                                         "finished":0,"total":len(snap.get("tasks",[])),"elapsed":0,
                                         "items":{},"logs":["上次进程结束前未保存完成回执，请核查已有产物与服务商状态后补产。"]})
        return data
    if path == "/api/agent/job":
        job = app.JOBS.get((q.get("id") or [""])[0])
        if not job:
            folder = A.inside(pj.root, "07_检查与记录/jobs/" + (q.get("id") or [""])[0])
            saved = read_json(str(folder / "result.json"))
            if saved:
                return saved
            if (folder / "snapshot.json").exists():
                return {"status":"interrupted","items":{"上次运行":{"state":"interrupted","msg":"进程已退出，核查已有产物和服务商状态后再补产。"}},"logs":[]}
            raise ValueError("任务不存在")
        if job.project_root != pj.root:
            raise ValueError("任务不存在")
        return job.snapshot()
    if path == "/api/agent/prompt":
        from core.store import read_text
        return {"text": read_text(str(A.inside(pj.root, (q.get("rel") or [""])[0])))}
    if path == "/api/agent/post-options":
        from core import agent_post, subtitle
        if not cfg.get("agent_features", {}).get("post"):
            raise ValueError("字幕与后期功能包未开启")
        return {"options": agent_post.options(pj), "styles": subtitle.list_styles(),
                "files": [pj.rel(str(f)) for f in sorted(Path(pj.p("06_成片")).glob("*.mp4")) if "_SUB" not in f.stem]}
    if path == "/api/agent/files":
        if (q.get("sub") or [""])[0]:
            A.inside(pj.root, q["sub"][0])
        return app.api_get("/api/files", q)
    raise ValueError("未知的 Agent 接口")


def post(app, path, body):
    cfg = app.load_config()
    caps = app.caps_of(cfg)
    if path == "/api/agent/create":
        base = str(body.get("base") or cfg["projects_dir"])
        if not os.path.isabs(base):
            raise ValueError("生产父目录必须是绝对路径")
        pj = A.create_project(base, body, caps)
        if os.path.normcase(os.path.abspath(base)) != os.path.normcase(os.path.abspath(cfg["projects_dir"])):
            cfg["agent_project_dirs"] = sorted(set(cfg.get("agent_project_dirs", []) + [os.path.abspath(base)]))
            app.save_config(cfg)
        return {"ok": True, "root": pj.root}
    if path == "/api/agent/settings":
        allowed = {"providers", "limits", "upload", "agent_features"}
        if set(body) - allowed:
            raise ValueError("设置只接受生产服务商、并发、上传和功能包")
        for pid, value in body.get("providers", {}).items():
            if pid not in app.PROVIDER_REGISTRY or any("llm" in k for k in value):
                raise ValueError("无效的生产服务商设置")
        if "limits" in body:
            lim = body["limits"]
            for n in [lim.get("global", 8)] + list(lim.get("per_provider", {}).values()):
                if isinstance(n, bool) or not 1 <= int(n) <= 512:
                    raise ValueError("全局和服务商并发须在 1–512 之间")
        if "agent_features" in body and (set(body["agent_features"]) - set(A.FEATURES) or any(not isinstance(x, bool) for x in body["agent_features"].values())):
            raise ValueError("只有可选功能包可以隐藏")
        return app.api_post("/api/config", body)
    if path == "/api/agent/models/refresh":
        return app.api_post("/api/models/refresh", body)
    if path == "/api/agent/providers/reload":
        return app.api_post("/api/providers/reload", {})
    if path == "/api/agent/parse":
        return app.api_post("/api/script/parse", body)
    pj = project(body.get("root"), cfg)
    if path == "/api/agent/source":
        if pj.meta().get("workflow") != "agent":
            raise ValueError("仅 Agent 项目支持在此更新原文")
        write_text(pj.p("01_剧本与分段", "原始素材.txt"), str(body.get("source", "")))
        return {"ok": True}
    if path == "/api/agent/archive":
        if app.JOBS.list(project_root=pj.root, active_only=True):
            raise ValueError("运行中的项目不能归档")
        meta = pj.meta()
        meta["archived"] = bool(body.get("archived"))
        pj.save_meta(meta)
        return {"ok": True}
    if path == "/api/agent/selections":
        A.select(pj, body.get("changes") or {})
        return {"ok": True}
    if path == "/api/agent/dependencies":
        data = A.view(pj)
        bykey = {t["key"]: t for t in data["tasks"]}
        changes = {}
        def add(k):
            if k in changes or R.valid_output(str(A.inside(pj.root, bykey[k]["output"]))):
                return
            changes[k] = True
            for d in bykey[k]["dependencies"]:
                add(d)
        for t in data["tasks"]:
            if t["selected"]:
                add(t["key"])
        A.select(pj, changes)
        return {"ok": True}
    if path == "/api/agent/production-settings":
        value = R.validate_settings(body.get("settings") or {}, caps)
        saved = R.settings(pj, caps, cfg)
        saved.update(value)
        write_json(pj.p("07_检查与记录", "production-settings.json"), saved)
        return {"ok": True}
    if path in ("/api/agent/preview", "/api/agent/start"):
        if pj.meta().get("archived"):
            raise ValueError("请先恢复归档项目")
        if path.endswith("preview"):
            return R.public_preview(R.preview(pj, cfg, caps, app.resolve_provider_cfg, body.get("only")))
        return R.start(pj, cfg, caps, app.resolve_provider_cfg, app.JOBS, body.get("only"), body.get("revision"))
    if path == "/api/agent/cancel":
        job = app.JOBS.get(body.get("id"))
        if not job or job.project_root != pj.root:
            raise ValueError("任务不存在")
        job.cancel()
        return {"ok": True}
    if path == "/api/agent/revision":
        return dict(A.revision_request(pj, body.get("keys") or [], str(body.get("note", ""))), ok=True)
    if path == "/api/agent/manual":
        if app.JOBS.list(project_root=pj.root, active_only=True):
            raise ValueError("请先停止生产再替换参考图")
        import base64
        from io import BytesIO
        from PIL import Image
        from core.apiutil import _atomic_output, _check_saved
        row = next((t for t in A.task_rows(pj.tasks(), legacy=True) if t["key"] == body.get("key") and t["kind"] != "video"), None)
        if not row:
            raise ValueError("出图任务不存在，请刷新")
        dest = A.inside(pj.root, row["output"])
        if dest.exists():
            raise ValueError("此位置已有文件，请让 Agent 发布使用新输出路径的修订，避免改变已被引用的成品")
        raw = base64.b64decode(body.get("content_b64", ""), validate=True)
        if not raw or len(raw) > 40 * 1024 * 1024:
            raise ValueError("图片不能为空或超过 40MB")
        with Image.open(BytesIO(raw)) as image:
            image.verify()
        fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(dest.suffix.lower())
        if not fmt:
            raise ValueError("不支持的目标图片格式")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _atomic_output(str(dest)) as temporary:
            with Image.open(BytesIO(raw)) as image:
                image.load()
                image.convert("RGB" if fmt == "JPEG" else "RGBA").save(temporary, format=fmt)
            _check_saved(temporary, "用户手动放图", extension=dest.suffix.lower())
        pj.log_event({"stage": "manual_image", "id": row["key"], "result": "ok", "file": row["output"]})
        return {"ok": True, "file": row["output"]}
    if path == "/api/agent/import":
        if not cfg.get("agent_features", {}).get("legacy_import") or pj.meta().get("workflow") == "agent":
            raise ValueError("材料导入只对开启该功能包的旧项目提供；Agent 项目自动接收发布")
        return app.api_post("/api/material/import", body)
    if path == "/api/agent/assemble":
        if not cfg.get("agent_features", {}).get("post"):
            raise ValueError("请先开启成片合成功能包")
        from core import probe
        import subprocess
        ep = str(body.get("episode") or "")
        tasks = [t for t in A.task_rows(pj.tasks(), legacy=True) if t["kind"] == "video" and t.get("episode", "") == ep]
        if not tasks or any(not R.valid_output(str(A.inside(pj.root, t["output"]))) for t in tasks):
            raise ValueError("这一集的视频尚未完整生成，不能合成缺段成片")
        # Contract order is the timeline order; do not infer it from filenames.
        safe_ep = "".join(c for c in ep if c.isalnum() or c in "-_") or "EP01"
        rel = "06_成片/" + safe_ep + time.strftime("_MASTER_%Y%m%d_%H%M%S_") + str(time.time_ns())[-6:] + ".mp4"
        concat = pj.p("07_检查与记录", "concat-" + str(time.time_ns()) + ".txt")
        lines = ["file '" + str(A.inside(pj.root, t["output"])).replace("\\", "/").replace("'", "'\\''") + "'" for t in tasks]
        write_text(concat, "\n".join(lines))
        output = A.inside(pj.root, rel)
        output.parent.mkdir(parents=True, exist_ok=True)
        ff = probe.find_ffmpeg()
        if not ff:
            raise ValueError("未找到 ffmpeg")
        r = subprocess.run([ff, "-nostdin", "-v", "error", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", str(output)],
                           capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode:
            raise ValueError("成片合成失败：" + r.stderr.decode("utf-8", errors="replace")[-1000:])
        return {"ok": True, "rel": rel}
    if path == "/api/agent/subtitle":
        if not cfg.get("agent_features", {}).get("post"):
            raise ValueError("字幕与后期功能包未开启")
        from core import agent_post
        return agent_post.run(pj, body, app.JOBS)
    raise ValueError("未知的 Agent 接口")


def watch(app, stop):
    """Scan on startup and periodically, even while no browser page is open."""
    while not stop.is_set():
        try:
            for row in projects(app.load_config()):
                if row["workflow"] == "agent" and not row["archived"]:
                    A.scan(Project(row["root"]))
        except Exception as exc:
            print(f"[Agent 发布扫描] {exc}")
        stop.wait(5)
