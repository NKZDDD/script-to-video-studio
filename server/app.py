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
from core.executor import GATE, LLM_GATE, JobManager, run_batch, run_chain
from core.llm import LLM
from core.providers import (REGISTRY as PROVIDER_REGISTRY, build as build_provider,
                            list_capabilities, resolve_id as resolve_provider_id)
from core import paths
from core.store import Project, list_projects, read_json, write_json

ROOT = paths.PROGRAM_DIR
WEB_DIR = paths.res("web")      # 打包后是解压出来的临时目录，只读
# 配置和产物都不在程序目录里（除了老装法），这样更新/换机器时整个覆盖程序目录
# 也不会丢 key 和产物。位置怎么定见 core/paths.py。
# 用函数而不是模块级常量：--data 是启动时才知道的。

# 按服务商的默认并发配额。只列实测确认能扛的；没列的走兜底 4。
# 用户在设置页改过的值优先，这里只补缺项。
PROVIDER_QUOTA = {"paisio": 6, "lingganya": 4}

JOBS = JobManager()


def load_config() -> dict:
    cfg = read_json(paths.config_path(), {}) or {}
    cfg.setdefault("projects_dir", paths.default_projects_dir())
    cfg.setdefault("providers", {})
    cfg.setdefault("llm", {})
    # 出图出片的服务商优先级：一次配好，之后每次跑都按这个顺序，
    # 首选挂了自动换下一家。空的话「设置」页会提示去配。
    cfg.setdefault("chains", {"asset": [], "storyboard": [], "video": []})
    # 计价表（可空）。键可以是 "服务商/模型"、"模型" 或 "服务商"，由细到粗匹配。
    # LLM：{"in": 每 per 个输入 token 的价, "out": ..., "cached_in": ..., "per": 1000000}
    # 出图出片：{"per_call": 每次的价}
    # 不填就只统计用量、不算钱 —— 各家计价方式差别太大，猜一个假数字更糟。
    cfg.setdefault("prices", {})
    # 参考图上传：给只收公网链接的接口用（零视 SD2、seedance 系都是这类）。
    # 不配也能跑，只是那类模型用不了。
    # mode: always=配了就全部走链接（推荐，请求体小）｜when_required=只在模型必须时传
    # 逐项 setdefault：老 config.json 里已有 upload 时，新加的字段也能补上
    up = cfg.setdefault("upload", {})
    for k, v in (("endpoint", ""), ("region", "auto"), ("bucket", ""),
                 ("access_key", ""), ("secret_key", ""), ("public_base_url", ""),
                 ("prefix", "respect"), ("public_acl", False), ("mode", "always")):
        up.setdefault(k, v)
    cfg.setdefault("defaults", {
        "duration": 15, "ratio": "9:16", "image_size": "1024x1536",
        "frames": 4, "shots_min": 5, "shots_max": 8,
        "frames_min": 4, "frames_max": 6, "episode_minutes": 3,
        "concurrency": 3, "max_retry": 2,
    })
    # 多剧并行的并发闸门：全局总上限 + 按服务商配额。
    # 缺项从注册表补齐——新接一家服务商就自动有配额，不用手动改 config.json。
    # 实测给过更高配额的那几家保留原值（paisio 是最稳的出片通道），
    # 没数的一律给 4：宁可慢一点，也别一上来就把新通道打出限流。
    lim = cfg.setdefault("limits", {})
    lim.setdefault("global", 8)
    per = lim.setdefault("per_provider", {})
    for pid in PROVIDER_REGISTRY:
        per.setdefault(pid, PROVIDER_QUOTA.get(pid, 4))
    GATE.configure(lim.get("global", 8), per)
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(paths.config_path()), exist_ok=True)
    write_json(paths.config_path(), cfg)


def proj_of(body: dict) -> Project:
    root = body.get("project_root") or ""
    if not root:
        raise ValueError("缺少 project_root")
    return Project(root)


def resolve_chain(cfg: dict, kind: str, override=None) -> list:
    """某一类活（asset/storyboard/video）按优先级用哪几家。

    先用本次请求给的 override，否则用「设置」页存下来的 chains[kind]。
    只保留已经填了 key 的那些家 —— 没配 key 的排在链里毫无意义，
    真跑到它才报「未配置」等于白等一轮。
    """
    raw = override or ((cfg.get("chains") or {}).get(kind) or [])
    if isinstance(raw, dict):
        raw = [raw]
    out, skipped = [], []
    for sel in raw:
        pid = resolve_provider_id((sel or {}).get("provider") or "")
        if not pid:
            continue
        if not ((cfg.get("providers") or {}).get(pid, {}) or {}).get("api_key"):
            skipped.append(pid)
            continue
        out.append(resolve_provider_cfg(cfg, sel))
    if not out:
        extra = f"（{'、'.join(skipped)} 没填 key，已跳过）" if skipped else ""
        raise ValueError(f"「{kind}」还没有可用的服务商{extra}。"
                         f"去「设置 → 出图出片优先级」把这一类要用哪几家排好，"
                         f"并确认对应的 key 都填了。")
    return out


def preflight_models(chains: dict) -> list:
    """开跑前核对每条链里的模型在对应服务商那边真的存在。

    这一步很值：各家的模型命名不统一，同一个模型在不同家写法不同 ——
    paisio 是 gpt-image-2-1k（带分辨率后缀）、灵感鸭是 gpt-image-2（不带）；
    nano banana 一家用下划线一家用连字符；veo 也是。照抄另一家的写法就会
    在跑到那一步时报「找不到这个模型」，而那可能是几百步之后的事。
    只拉模型列表，零生成费用。
    """
    problems = []
    cache: dict = {}
    for kind, chain in (chains or {}).items():
        for i, pcfg in enumerate(chain):
            pid, model = pcfg.get("provider", ""), pcfg.get("model", "")
            if not model:
                continue
            if pid not in cache:
                try:
                    cache[pid] = set(build_provider(
                        pid, pcfg.get("api_key", ""), pcfg.get("base_url", ""),
                        pcfg.get("proxy", "")).list_models())
                except Exception:                       # noqa: BLE001
                    cache[pid] = set()                  # 拉不到就不判，别误伤
            avail = cache[pid]
            if not avail or model in avail:
                continue
            # 给出最接近的几个，多半就是后缀或连字符/下划线的差别
            base = re.sub(r"[-_]", "", model.lower())
            near = [m for m in sorted(avail)
                    if base[:8] and base[:8] in re.sub(r"[-_]", "", m.lower())][:5]
            problems.append({
                "kind": kind, "slot": "首选" if i == 0 else f"备选{i}",
                "provider": pid, "model": model, "near": near,
                "msg": f"「{kind}」的{'首选' if i == 0 else f'备选{i}'} "
                       f"{pid}/{model} 在这家不存在。"
                       + (f"是不是想用：{'、'.join(near)}？" if near
                          else "去「设置 → 出图出片优先级」换一个。")})
    return problems


def resolve_provider_cfg(cfg: dict, sel: dict) -> dict:
    """页面选择 + config 里保存的凭据 → 完整服务商配置。"""
    # 别名归一：同一家常有几个叫法（鹤 / 派系 / pis 都是 api.paisio.online），
    # 老配置里写的可能是别名，认了才不会报「未知服务商」。
    pid = resolve_provider_id(sel.get("provider") or "")
    saved = ((cfg.get("providers") or {}).get(pid)
             or (cfg.get("providers") or {}).get(sel.get("provider") or "", {}))
    out = dict(saved)
    out.update({k: v for k, v in sel.items() if v not in (None, "")})
    out["provider"] = pid
    if not out.get("api_key"):
        raise ValueError(f"服务商 {pid} 未配置 api_key（在「服务商」页签保存）")
    # 参考图上传配置是全局共用的（一个对象存储服务所有服务商），
    # 但某家自己的上传端点能不能用是按家配的
    out["upload"] = dict(cfg.get("upload") or {})   # 上传配置全局共用一份
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
               proxy=c.get("proxy", ""), max_tokens=int(c.get("max_tokens", 16000)))


# ====================================================================== 路由

def api_get(path: str, q: dict) -> dict:
    cfg = load_config()

    if path == "/api/bootstrap":
        # 凭据一律不回前端：providers 整块剔掉，upload 里的密钥只回「配了没」
        pub = {k: v for k, v in cfg.items() if k != "providers"}
        up = dict(pub.get("upload") or {})
        up["secret_key"] = ""
        up["access_key_set"] = bool((cfg.get("upload") or {}).get("access_key"))
        up["secret_key_set"] = bool((cfg.get("upload") or {}).get("secret_key"))
        if up.get("access_key"):
            up["access_key"] = up["access_key"][:4] + "…"
        pub["upload"] = up
        return {"config": pub,
                "providers_configured": {k: bool(v.get("api_key"))
                                         for k, v in (cfg.get("providers") or {}).items()},
                "capabilities": list_capabilities(),
                "stages": S.STAGES,
                "projects": list_projects(cfg["projects_dir"]),
                "projects_dir": cfg["projects_dir"],
                # 程序目录 / 数据目录 / 配置位置，让人一眼知道更新程序会不会碰到数据
                "paths": dict(paths.snapshot(), projects_dir=cfg["projects_dir"])}

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

    if path == "/api/usage":
        """这个项目到现在的用量：按环节、按模型汇总。金额按「设置 → 计价」估算。"""
        from core import ledger
        pj = Project(q["root"][0])
        return ledger.summary(pj.root, cfg.get("prices") or {})

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
        gate = dict(GATE.snapshot())
        gate.update(LLM_GATE.snapshot())        # 分析引擎的在途数也要能看见
        return {"jobs": JOBS.list(project_root=q.get("root", [""])[0],
                                  active_only=q.get("active", ["0"])[0] == "1"),
                "active": JOBS.active_count(),
                "gate": gate}

    if path == "/api/models":
        pid = q["provider"][0]
        pc = (cfg.get("providers") or {}).get(pid, {})
        prov = build_provider(pid, pc.get("api_key", ""), pc.get("base_url", ""))
        return {"models": prov.list_models()}

    if path == "/api/paths":
        return dict(paths.snapshot(), projects_dir=cfg["projects_dir"])

    if path == "/api/explorer":
        """资产库 + 按段看。把散在各处的产物拼成能看懂的两个视图。"""
        from core import explorer
        pj = Project(q["root"][0])
        return explorer.view(pj, (q.get("episode") or [""])[0])

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
        # 密钥类字段「留空 = 不改」。前端拿到的是掩码后的值，
        # 直接 update 会把已保存的密钥覆盖成空。
        SECRET = ("api_key", "secret_key", "access_key")
        for k, v in body.items():
            if k == "providers" and isinstance(v, dict):
                cur.setdefault("providers", {})
                for pid, pv in v.items():
                    pv = {kk: vv for kk, vv in pv.items()
                          if not (kk in SECRET and not str(vv).strip())}
                    cur["providers"].setdefault(pid, {}).update(pv)
            elif isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items()
                     if not (kk in SECRET and not str(vv).strip())}
                # 这几个是只读回显字段，不该被存进 config
                v.pop("access_key_set", None)
                v.pop("secret_key_set", None)
                cur.setdefault(k, {}).update(v)
            else:
                cur[k] = v
        save_config(cur)
        return {"ok": True}

    if path == "/api/paths/projects":
        """改产物目录。换机器时最常改的就是这个（盘不一样、想放到大盘上）。"""
        d = str(body.get("projects_dir") or "").strip().strip('"')
        if not d:
            raise ValueError("路径是空的")
        d = os.path.abspath(os.path.expanduser(d))
        if os.path.isfile(d):
            raise ValueError(f"这是个文件不是目录：{d}")
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".写入自检")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as exc:
            raise ValueError(f"这个目录建不了或者写不进去：{exc}") from exc
        cur = load_config()
        cur["projects_dir"] = d
        save_config(cur)
        found = list_projects(d)
        return {"ok": True, "projects_dir": d, "found": len(found),
                "names": [p.get("name") for p in found[:12]]}

    if path == "/api/paths/migrate":
        """把程序目录里的 config.json 搬到数据目录 —— 之后覆盖程序目录不会再丢配置。

        只复制 + 把原件改名成 .已搬走，不删任何东西。
        """
        r = paths.migrate_config(str(body.get("data_dir") or "").strip())
        return {"ok": True, **r, "paths": paths.snapshot()}

    if path == "/api/pipeline/preview":
        """先看清这一次会做什么、跳过什么，再决定点不点「开始」。"""
        from core import pipeline
        pj = proj_of(body)
        return pipeline.preview(pj,
                               include_produce=body.get("include_produce", True),
                               include_deliver=body.get("include_deliver", True),
                               only_episodes=body.get("only_episodes"),
                               produce_episodes=body.get("produce_episodes"))

    if path == "/api/pipeline/run":
        """一键跑到底。中断后再点一次就是续跑——做过的自动跳过。"""
        from core import pipeline
        pj = proj_of(body)
        meta = pj.meta()
        params = dict(cfg.get("defaults") or {})
        params.update(meta.get("params") or {})
        params.update({"project_code": meta.get("project_code", "PROJ-001"),
                       "episode": meta.get("episode", "EP01")})
        # 出图尺寸/画面比例/单段时长：点「开始」时给的值优先于「默认参数」。
        # 这些会被环节8 装配进 tasks.json，所以要在跑之前就定下来。
        for k, v in (body.get("params_override") or {}).items():
            if k in ("image_size", "ratio", "duration", "frames", "episode_minutes",
                     "shots_min", "shots_max") and str(v).strip():
                params[k] = int(v) if k not in ("image_size", "ratio") else v
        script_path = pj.p("01_剧本与分段", "原始剧本.txt")
        if os.path.isfile(script_path):
            from core.store import read_text
            params["script"] = read_text(script_path)

        # 出图/出片各自按优先级用哪几家，点「开始」时就定下来。
        # 提前解析一遍：key 没填、链是空的，现在就报，别等跑到第 300 步才发现。
        sel = body.get("provider_sel") or {}
        chains = {k: resolve_chain(cfg, k, sel.get(k)) for k in
                  ("asset", "storyboard", "video")}
        # 核对模型名是否真的存在（零费用）。各家命名不统一，照抄别家写法就会
        # 在跑到那一步时报「找不到这个模型」——那可能是几百步之后的事。
        if not body.get("skip_model_check"):
            bad = preflight_models(chains)
            if bad:
                raise ValueError("模型名对不上，先改了再跑：\n"
                                 + "\n".join("· " + b["msg"] for b in bad))

        # 同一个项目不许同时跑两条流水线：两条都会写同一批产物文件，
        # 白花两份钱（同一个环节调两次模型），后写的还会覆盖先写的。
        # 想重来先点「停下」，或者传 force。
        if not body.get("force"):
            live = [j for j in JOBS.list(project_root=pj.root, active_only=True)
                    if j["kind"] == "pipeline"]
            if live:
                raise ValueError(
                    f"这个项目已经有一条流水线在跑了（{live[0]['id']}，"
                    f"已跑 {live[0]['elapsed']} 秒，进度 {live[0]['finished']}/{live[0]['total']}）。"
                    f"两条一起跑会写同一批文件、把钱花两遍。"
                    f"要么等它跑完（做过的步骤下次会自动跳过），"
                    f"要么先点那条的「停下」再来。")

        job = JOBS.create("pipeline", 1, 1, project_root=pj.root,
                          project_name=os.path.basename(pj.root), provider="pipeline")
        pipeline.start(
            job, pj,
            llm_factory=lambda: build_llm(cfg, body.get("llm")),
            provider_factory=lambda kind: chains[kind],
            params=params, jobs=JOBS,
            concurrency=int(body.get("concurrency")
                            or (cfg.get("defaults") or {}).get("concurrency", 3)),
            max_retry=int(body.get("max_retry")
                          or (cfg.get("defaults") or {}).get("max_retry", 2)),
            include_produce=body.get("include_produce", True),
            include_deliver=body.get("include_deliver", True),
            only_episodes=body.get("only_episodes"),
            produce_episodes=body.get("produce_episodes"),
            # 分析引擎的并发。和出图出片的 concurrency 是两回事：那是按服务商
            # 配额算的，这是另一个网关、另一套限流。混一起用出图一忙就把分析饿死。
            ep_concurrency=int(body.get("llm_episodes")
                               or (cfg.get("defaults") or {}).get("llm_episodes", 4)),
            seg_concurrency=int(body.get("llm_segments")
                                or (cfg.get("defaults") or {}).get("llm_segments", 4)),
            llm_concurrency=int(body.get("llm_concurrency")
                                or (cfg.get("defaults") or {}).get("llm_concurrency", 6)))
        return {"ok": True, "job_id": job.id}

    if path == "/api/upload/selftest":
        """传一个小文件再用普通 HTTP 取回来，确认服务商真的能读到。

        只测「上传成功」不够：桶没开公开读、或公开域名填了 R2 的 S3 API 域名，
        上传都会成功但服务商取图 403。这两秒的自检能省掉一整批任务的失败。
        """
        from core import uploader
        up = dict(cfg.get("upload") or {})
        up.update({k: v for k, v in (body.get("upload") or {}).items() if str(v).strip()})
        return uploader.selftest(up)

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
        # script / text 两个名字都收：批量建剧那个接口用的是 text，
        # 之前这里只认 script，传 text 会被静默丢掉，建出一个没有剧本的空项目，
        # 直到跑环节1 才报「剧本是空的」，根本看不出是建项目时丢的。
        script = body.get("script") or body.get("text") or ""
        if not script.strip():
            raise ValueError("没有剧本正文。建项目时必须带 script（或 text）字段，"
                             "否则建出来的项目跑不了任何环节。")
        pj.save_meta(meta)
        from core.store import write_text
        write_text(pj.p("01_剧本与分段", "原始剧本.txt"), script)
        return {"ok": True, "root": root, "chars": len(script)}

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

        # 尺寸/比例/时长的运行时覆盖。
        # tasks.json 里的值是环节8 装配时按「默认参数」烧进去的；换了服务商之后
        # 那个尺寸可能这家不支持（坤鸡有 2048x2048、paisio 没有），所以生产页
        # 要能当场改。这里只改内存里的这一批任务，不动 tasks.json ——
        # 免得一次试跑把装配结果永久改掉。
        allow = {"video": ("ratio", "duration"), "asset": ("size",),
                 "storyboard": ("size", "frames")}[kind]
        ov = {}
        for k, v in (body.get("params_override") or {}).items():
            if k not in allow or str(v).strip() in ("", "None"):
                continue
            ov[k] = int(v) if k in ("duration", "frames") else v
        if ov:
            items = [dict(t, params=dict(t.get("params") or {}, **ov)) for t in items]
        # 未完成数（已存在的会在 worker 里跳过，这里只用于提示）
        todo = sum(1 for t in items if not os.path.isfile(pj.p(*t["output"].split("/"))))

        # 手动跑也走优先级链：首选挂了自动换下一家补剩下的
        chain = resolve_chain(cfg, kind, body.get("chain") or [pcfg])
        parent = JOBS.create(kind, len(items), conc,
                             project_root=pj.root, project_name=os.path.basename(pj.root),
                             provider=chain[0]["provider"], model=chain[0].get("model", ""))

        def go():
            try:
                # 资产图按参考图依赖分层，和「一键跑到底」同一套：状态资产的参考图
                # 是它的父资产，不分层就会父子并发，子任务读不到父资产的 png。
                layers = S.asset_layers(items) if kind == "asset" else [items]
                if len(layers) > 1:
                    parent.log(kind, f"按参考图依赖分 {len(layers)} 层："
                               + "、".join(f"第{i}层 {len(g)} 项"
                                          for i, g in enumerate(layers, 1)))
                r = {"attempts": [], "left": 0}
                for gi, grp in enumerate(layers, 1):
                    if parent.cancelled:
                        break
                    if len(layers) > 1:
                        parent.log(kind, f"—— 第 {gi}/{len(layers)} 层，{len(grp)} 项")
                    one = run_chain(
                        grp, chain=chain,
                        worker_of=lambda p: (S.make_video_worker(pj, p) if kind == "video"
                                             else S.make_image_worker(pj, p, kind)),
                        job_of=lambda p, n: JOBS.create(
                            kind, n, conc, project_root=pj.root,
                            project_name=os.path.basename(pj.root),
                            provider=p["provider"], model=p.get("model", "")),
                        key_of=lambda t: t["key"],
                        done_of=lambda t: os.path.isfile(pj.p(*t["output"].split("/"))),
                        max_retry=retry, log=lambda m: parent.log(kind, m))
                    r["attempts"] += one["attempts"]
                    r["left"] += one["left"]
                for a in r["attempts"]:
                    parent.set_item(f"{a['provider']}/{a['model']}", state=
                                   "failed" if a["counts"].get("failed") else "ok",
                                   msg=str(a["counts"]))
                parent.status = "error" if r["left"] else "done"
            except Exception as exc:                 # noqa: BLE001
                d = diagnose.build(exc, stage=kind, target=kind)
                parent.log(kind, diagnose.one_line(d))
                parent.abort_diag = d
                parent.status = "error"
            finally:
                import time as _t
                parent.finished_at = _t.time()

        threading.Thread(target=go, daemon=True).start()
        return {"ok": True, "job_id": parent.id, "total": len(items), "todo": todo,
                "chain": [f"{c['provider']}/{c.get('model','')}" for c in chain]}

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
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except UnicodeDecodeError as exc:
                # 浏览器一定发 UTF-8；能走到这里的是脚本/命令行客户端编码错了。
                # 直接说清是编码问题，不要甩一句 codec can't decode。
                raise ValueError(
                    f"请求体不是 UTF-8 编码（第 {exc.start} 字节 0x{raw[exc.start]:02x} 解不开）。"
                    f"如果你是用脚本调这个接口，把 body 显式编成 UTF-8 再发；"
                    f"网页端不会出这个问题。") from exc
            return self._json(api_post(unquote(u.path), body))
        except Exception as exc:                          # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)


def serve(host: str = "127.0.0.1", port: int = 8770):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv
