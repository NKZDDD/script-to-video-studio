# -*- coding: utf-8 -*-
"""本地后端：stdlib http.server（零额外依赖），REST + 轮询式进度。"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from core import diagnose, docparse, episodes, stages as S
from core.executor import GATE, JobManager, run_batch
from core.llm import LLM
from core.providers import build as build_provider, list_capabilities
from core.store import Project, list_projects, read_json, write_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
CONFIG_PATH = os.path.join(ROOT, "config.json")
DEFAULT_PROJECTS = os.path.abspath(os.path.join(ROOT, "..", "projects"))

JOBS = JobManager()


def load_config() -> dict:
    cfg = read_json(CONFIG_PATH, {}) or {}
    cfg.setdefault("projects_dir", DEFAULT_PROJECTS)
    cfg.setdefault("providers", {})
    cfg.setdefault("llm", {})
    cfg.setdefault("defaults", {
        "duration": 15, "ratio": "9:16", "image_size": "1024x1536",
        "frames": 4, "shots_min": 5, "shots_max": 8,
        "frames_min": 4, "frames_max": 6, "episode_minutes": 3,
        "concurrency": 3, "max_retry": 2,
    })
    # 多剧并行的并发闸门：全局总上限 + 按服务商配额
    cfg.setdefault("limits", {"global": 8, "per_provider": {"lingganya": 4, "paisio": 6}})
    GATE.configure(cfg["limits"].get("global", 8),
                   cfg["limits"].get("per_provider", {}))
    return cfg


def save_config(cfg: dict) -> None:
    write_json(CONFIG_PATH, cfg)


def proj_of(body: dict) -> Project:
    root = body.get("project_root") or ""
    if not root:
        raise ValueError("缺少 project_root")
    return Project(root)


def resolve_provider_cfg(cfg: dict, sel: dict) -> dict:
    """页面选择 + config 里保存的凭据 → 完整服务商配置。"""
    pid = sel.get("provider") or ""
    saved = (cfg.get("providers") or {}).get(pid, {})
    out = dict(saved)
    out.update({k: v for k, v in sel.items() if v not in (None, "")})
    out["provider"] = pid
    if not out.get("api_key"):
        raise ValueError(f"服务商 {pid} 未配置 api_key（在「服务商」页签保存）")
    return out


def build_llm(cfg: dict, override: dict = None) -> LLM:
    c = dict(cfg.get("llm") or {})
    c.update({k: v for k, v in (override or {}).items() if v})
    if not c.get("api_key"):
        pid = c.get("provider") or "paisio"
        c["api_key"] = ((cfg.get("providers") or {}).get(pid, {})).get("api_key", "")
        c.setdefault("base_url", ((cfg.get("providers") or {}).get(pid, {})).get("base_url", ""))
    if not c.get("api_key"):
        raise ValueError("LLM 未配置 api_key（在「分析引擎」页签保存）")
    return LLM(c["api_key"], c.get("base_url", "https://api.paisio.online"),
               c.get("model", "claude-sonnet-5"), timeout=int(c.get("timeout", 900)),
               proxy=c.get("proxy", ""))


# ====================================================================== 路由

def api_get(path: str, q: dict) -> dict:
    cfg = load_config()

    if path == "/api/bootstrap":
        return {"config": {k: v for k, v in cfg.items() if k != "providers"},
                "providers_configured": {k: bool(v.get("api_key"))
                                         for k, v in (cfg.get("providers") or {}).items()},
                "capabilities": list_capabilities(),
                "stages": S.STAGES,
                "projects": list_projects(cfg["projects_dir"]),
                "projects_dir": cfg["projects_dir"]}

    if path == "/api/projects":
        return {"projects": list_projects(cfg["projects_dir"])}

    if path == "/api/project":
        pj = Project(q["root"][0])
        tasks = pj.tasks()
        done = {}
        for kind, key in (("asset_tasks", "asset"), ("storyboard_tasks", "storyboard"),
                          ("video_tasks", "video")):
            items = tasks.get(kind, [])
            done[key] = {
                "total": len(items),
                "done": sum(1 for t in items
                            if os.path.isfile(pj.p(*t["output"].split("/")))),
            }
        ep = (q.get("episode") or [""])[0]
        stage_state = {}
        for st in S.STAGES:
            if st["kind"] == "llm" and st["out"]:
                # 逐集环节看的是「这一集做了没」；不指定集时看第一集，只为渲染流程图
                sub = "" if st["id"] in S.SERIES_STAGES else (ep or (episodes.ids(pj) or [""])[0])
                stage_state[st["id"]] = os.path.isfile(pj.stage_path(st["out"], sub))
        return {"meta": pj.meta(), "tasks_summary": done, "stages_done": stage_state,
                "root": pj.root, "episodes": episodes.summary(pj), "episode": ep}

    if path == "/api/episodes":
        """集清单：环节1 切出来的结果，含每集字数和切分时的问题。"""
        return episodes.summary(Project(q["root"][0]))

    if path == "/api/diagnose":
        """项目体检：环节完成度 + 未解决的失败 + 下一步该干什么。磁盘为准，重启不丢。"""
        pj = Project(q["root"][0])
        h = S.health(pj, (q.get("episode") or [""])[0])
        h["episodes"] = episodes.summary(pj)
        return h

    if path == "/api/failures":
        pj = Project(q["root"][0])
        return {"items": diagnose.load(pj.root), "summary": diagnose.summary(pj.root)}

    if path == "/api/stage_data":
        pj = Project(q["root"][0])
        return {"data": pj.stage_data(q["name"][0], (q.get("episode") or [""])[0])}

    if path == "/api/job":
        job = JOBS.get(q["id"][0]) if q.get("id") else None
        return job.snapshot() if job else {"status": "none"}

    if path == "/api/jobs":
        return {"jobs": JOBS.list(project_root=q.get("root", [""])[0],
                                  active_only=q.get("active", ["0"])[0] == "1"),
                "active": JOBS.active_count(),
                "gate": GATE.snapshot()}

    if path == "/api/models":
        pid = q["provider"][0]
        pc = (cfg.get("providers") or {}).get(pid, {})
        prov = build_provider(pid, pc.get("api_key", ""), pc.get("base_url", ""))
        return {"models": prov.list_models()}

    if path == "/api/files":
        pj = Project(q["root"][0])
        sub = q.get("sub", [""])[0]
        base = pj.p(*sub.split("/")) if sub else pj.root
        out = []
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                full = os.path.join(base, name)
                out.append({"name": name, "dir": os.path.isdir(full),
                            "size": 0 if os.path.isdir(full) else os.path.getsize(full),
                            "rel": pj.rel(full)})
        return {"items": out, "sub": sub}

    raise ValueError(f"未知 GET 路由: {path}")


def api_post(path: str, body: dict) -> dict:
    cfg = load_config()

    if path == "/api/config":
        cur = load_config()
        for k, v in body.items():
            if k == "providers" and isinstance(v, dict):
                cur.setdefault("providers", {})
                for pid, pv in v.items():
                    cur["providers"].setdefault(pid, {}).update(pv)
            elif isinstance(v, dict):
                cur.setdefault(k, {}).update(v)
            else:
                cur[k] = v
        save_config(cur)
        return {"ok": True}

    if path == "/api/script/parse":
        """上传剧本文件（base64）→ 解析为纯文本。支持 txt/md/docx/pdf。"""
        import base64
        name = body.get("filename", "")
        raw = base64.b64decode(body.get("content_b64", ""))
        if len(raw) > 40 * 1024 * 1024:
            raise ValueError("文件超过 40MB")
        text = docparse.parse_bytes(name, raw)
        if not text.strip():
            raise ValueError("解析结果为空（PDF 可能是扫描件，需要 OCR）")
        return {"ok": True, "filename": name, "text": text, **docparse.stats(text)}

    if path == "/api/project/create_batch":
        """批量建项目：一个文件一个项目。返回逐条结果，单条失败不影响其它。"""
        import base64
        base = cfg["projects_dir"]
        defaults = body.get("params") or cfg.get("defaults") or {}
        code_prefix = (body.get("code_prefix") or "PROJ").strip() or "PROJ"
        episode = body.get("episode") or "EP01"
        results = []
        seq = 0
        for it in body.get("files", []):
            seq += 1
            fname = it.get("filename", f"script{seq}")
            stem = re.sub(r'[\\/:*?"<>|]', "_", os.path.splitext(fname)[0]).strip() or f"script{seq}"
            try:
                text = it.get("text")
                if text is None:
                    text = docparse.parse_bytes(fname, base64.b64decode(it.get("content_b64", "")))
                if not text.strip():
                    raise ValueError("解析结果为空（PDF 可能是扫描件）")
                root = os.path.join(base, stem)
                if os.path.exists(root):
                    raise ValueError("同名项目目录已存在")
                pj = Project(root)
                pj.init_dirs()
                pj.save_meta({"title": stem,
                              "project_code": f"{code_prefix}-{seq:03d}",
                              "episode": episode, "params": defaults,
                              "source_file": fname})
                from core.store import write_text
                write_text(pj.p("01_剧本与分段", "原始剧本.txt"), text)
                results.append({"ok": True, "filename": fname, "name": stem,
                                "root": root, **docparse.stats(text)})
            except Exception as exc:                     # noqa: BLE001
                results.append({"ok": False, "filename": fname, "error": str(exc)[:200]})
        return {"ok": True, "results": results,
                "created": sum(1 for r in results if r.get("ok"))}

    if path == "/api/project/create":
        base = cfg["projects_dir"]
        name = body["name"].strip()
        root = os.path.join(base, name)
        pj = Project(root)
        pj.init_dirs()
        meta = {"title": body.get("title") or name,
                "project_code": body.get("project_code", "PROJ-001"),
                "episode": body.get("episode", "EP01"),
                "params": body.get("params", {}),
                "source_file": body.get("source_file", "")}
        pj.save_meta(meta)
        if body.get("script"):
            from core.store import write_text
            write_text(pj.p("01_剧本与分段", "原始剧本.txt"), body["script"])
        return {"ok": True, "root": root}

    if path == "/api/project/script":
        """给已存在的项目更新剧本正文。"""
        pj = proj_of(body)
        from core.store import write_text
        write_text(pj.p("01_剧本与分段", "原始剧本.txt"), body.get("script", ""))
        return {"ok": True, **docparse.stats(body.get("script", ""))}

    if path == "/api/project/params":
        pj = proj_of(body)
        meta = pj.meta()
        meta.setdefault("params", {}).update(body.get("params", {}))
        pj.save_meta(meta)
        return {"ok": True, "meta": meta}

    if path == "/api/stage/run":
        pj = proj_of(body)
        stage_id = body["stage"]
        meta = pj.meta()
        params = dict(cfg.get("defaults") or {})
        params.update(meta.get("params") or {})
        params.update({"project_code": meta.get("project_code", "PROJ-001"),
                       "episode": meta.get("episode", "EP01")})
        script_path = pj.p("01_剧本与分段", "原始剧本.txt")
        if os.path.isfile(script_path):
            from core.store import read_text
            params["script"] = read_text(script_path)

        # 逐集环节可以只跑一集，也可以「全部集依次跑」。
        # 依次跑做成一个 job、每集一个条目：进度看得见，中途失败只影响那一集，
        # 重跑时已完成的集会被跳过（磁盘上有产物就算做过）。
        req_ep = (body.get("episode") or "").strip()
        eps = episodes.ids(pj)
        if not S.is_per_episode(stage_id):
            targets = [""]
        elif body.get("all_episodes") and eps:
            targets = [e for e in eps
                       if body.get("redo") or pj.stage_data(S._LLM_SPEC[stage_id][0], e) is None]
            if not targets:
                return {"ok": True, "skipped": True,
                        "msg": f"全部 {len(eps)} 集都已经跑过这个环节了。"
                               f"想重做的话，去「产物」页删掉对应的产物再来。"}
        else:
            targets = [req_ep]

        job = JOBS.create(f"stage:{stage_id}", len(targets), 1,
                          project_root=pj.root, project_name=os.path.basename(pj.root),
                          provider="llm")

        def go():
            try:
                llm = build_llm(cfg, body.get("llm"))
                job.model = llm.model
                failed = []
                for tgt in targets:
                    key = f"{stage_id}:{tgt}" if tgt else stage_id
                    if job.cancelled:
                        job.set_item(key, state="cancelled", msg="已取消")
                        continue
                    job.set_item(key, state="running")
                    try:
                        S.run_llm_stage(pj, stage_id, llm, params,
                                        log=lambda m, k=key: job.log(k, m), episode=tgt)
                        job.set_item(key, state="ok")
                        diagnose.clear(pj.root, f"stage:{stage_id}", tgt)
                    except Exception as exc:             # noqa: BLE001
                        diag = diagnose.build(exc, stage=f"stage:{stage_id}",
                                              target=tgt or stage_id,
                                              model=getattr(job, "model", ""))
                        diagnose.record(pj.root, diag)
                        job.log(key, diagnose.one_line(diag))
                        job.set_item(key, state="failed",
                                     msg=diagnose.one_line(diag), diag=diag)
                        job.abort_diag = diag
                        failed.append(tgt or stage_id)
                        if diag.get("scope") == "batch":
                            # 余额、密钥这类：后面几十集撞的是同一堵墙，别再发了
                            job.abort_with(diag)
                            break
                job.status = "error" if failed else "done"
            except Exception as exc:                     # noqa: BLE001
                diag = diagnose.build(exc, stage=f"stage:{stage_id}", target=stage_id,
                                      model=getattr(job, "model", ""))
                diagnose.record(pj.root, diag)
                job.log(stage_id, diagnose.one_line(diag))
                job.set_item(stage_id, state="failed", msg=diagnose.one_line(diag), diag=diag)
                job.abort_diag = diag
                job.status = "error"
            finally:
                import time as _t
                job.finished_at = _t.time()

        threading.Thread(target=go, daemon=True).start()
        return {"ok": True, "job_id": job.id}

    if path == "/api/tasks/rebuild":
        pj = proj_of(body)
        meta = pj.meta()
        params = dict(cfg.get("defaults") or {})
        params.update(meta.get("params") or {})
        params.update({"project_code": meta.get("project_code", "PROJ-001"),
                       "episode": meta.get("episode", "EP01")})
        return {"ok": True, "tasks": S.build_tasks(pj, params)}

    if path == "/api/generate":
        pj = proj_of(body)
        kind = body["kind"]                    # asset | storyboard | video
        pcfg = resolve_provider_cfg(cfg, body.get("provider_sel") or {})
        conc = int(body.get("concurrency") or (cfg.get("defaults") or {}).get("concurrency", 3))
        retry = int(body.get("max_retry") or (cfg.get("defaults") or {}).get("max_retry", 2))
        only = set(body.get("only") or [])

        tasks = pj.tasks()
        key_map = {"asset": "asset_tasks", "storyboard": "storyboard_tasks", "video": "video_tasks"}
        items = tasks.get(key_map[kind], [])
        # 按集过滤：40 集的项目里只想出某一集的图/片。
        # 资产任务不带集号（全剧共享），所以不参与过滤。
        ep_sel = (body.get("episode") or "").strip()
        if ep_sel and kind != "asset":
            items = [t for t in items if t.get("episode", "") == ep_sel]
        if only:
            items = [t for t in items if t["key"] in only]
        if body.get("failed_only"):                     # 只补上次失败的
            bad = {f["target"] for f in diagnose.load(pj.root) if f.get("stage") == kind}
            items = [t for t in items if t["key"] in bad]
            if not items:
                return {"ok": False, "msg": "没有记录在案的失败任务"}
        if not items:
            return {"ok": False, "msg": "没有匹配的任务（先跑环节8生成 tasks.json）"}
        # 未完成数（已存在的会在 worker 里跳过，这里只用于提示）
        todo = sum(1 for t in items if not os.path.isfile(pj.p(*t["output"].split("/"))))

        worker = (S.make_video_worker(pj, pcfg) if kind == "video"
                  else S.make_image_worker(pj, pcfg, kind))
        job = JOBS.create(kind, len(items), conc,
                          project_root=pj.root, project_name=os.path.basename(pj.root),
                          provider=pcfg["provider"], model=pcfg.get("model", ""))
        threading.Thread(
            target=run_batch,
            args=(job, items, worker),
            kwargs={"key_of": lambda t: t["key"], "max_retry": retry,
                    "provider": pcfg["provider"]},
            daemon=True).start()
        return {"ok": True, "job_id": job.id, "total": len(items), "todo": todo}

    if path == "/api/failures/clear":
        pj = proj_of(body)
        n = diagnose.clear(pj.root, body.get("stage", ""), body.get("target", ""))
        return {"ok": True, "cleared": n}

    if path == "/api/job/cancel":
        job = JOBS.get(body["id"])
        if job:
            job.cancel()
        return {"ok": bool(job)}

    if path == "/api/review":
        pj = proj_of(body)
        return {"ok": True,
                "review": S.build_review_checklist(pj, (body.get("episode") or "").strip())}

    if path == "/api/assemble":
        pj = proj_of(body)
        meta = pj.meta()
        params = dict(cfg.get("defaults") or {})
        params.update(meta.get("params") or {})
        params.update({"project_code": meta.get("project_code", "PROJ-001"),
                       "episode": meta.get("episode", "EP01")})
        return {"ok": True,
                "result": S.assemble(pj, params, episode=(body.get("episode") or "").strip())}

    if path == "/api/selftest":
        out = {}
        for pid, pc in (cfg.get("providers") or {}).items():
            if not pc.get("api_key"):
                out[pid] = {"ok": False, "msg": "未配置 key"}
                continue
            try:
                models = build_provider(pid, pc["api_key"], pc.get("base_url", "")).list_models()
                out[pid] = {"ok": bool(models), "count": len(models),
                            "msg": f"{len(models)} 个模型" if models else "拉取失败"}
            except Exception as exc:                     # noqa: BLE001
                out[pid] = {"ok": False, "msg": str(exc)[:150]}
        try:
            llm = build_llm(cfg)
            out["llm"] = {"ok": True, "msg": f"{llm.model} @ {llm.base_url}"}
        except Exception as exc:                         # noqa: BLE001
            out["llm"] = {"ok": False, "msg": str(exc)[:150]}
        out["ffmpeg"] = {"ok": bool(S.find_ffmpeg()), "msg": S.find_ffmpeg() or "未找到"}
        return {"ok": True, "result": out}

    raise ValueError(f"未知 POST 路由: {path}")


# ====================================================================== HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):                   # 静音默认日志
        pass

    def _send(self, code: int, payload: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        path, q = unquote(u.path), parse_qs(u.query)
        try:
            if path.startswith("/api/"):
                return self._json(api_get(path, q))
            if path.startswith("/file"):                  # 产物预览
                root, rel = q["root"][0], q["rel"][0]
                full = os.path.join(root, *rel.split("/"))
                if not os.path.abspath(full).startswith(os.path.abspath(root)):
                    return self._json({"error": "越界"}, 403)
                if not os.path.isfile(full):
                    return self._json({"error": "不存在"}, 404)
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                with open(full, "rb") as f:
                    return self._send(200, f.read(), ctype)
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.join(WEB_DIR, name)
            if os.path.isfile(full):
                ctype = mimetypes.guess_type(full)[0] or "text/plain"
                with open(full, "rb") as f:
                    return self._send(200, f.read(), f"{ctype}; charset=utf-8")
            return self._json({"error": "404"}, 404)
        except Exception as exc:                          # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            return self._json(api_post(unquote(u.path), body))
        except Exception as exc:                          # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)


def serve(host: str = "127.0.0.1", port: int = 8770):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv
