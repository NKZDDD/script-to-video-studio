"""Production-only orchestration. No language-model client or prompt rewriting."""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
import threading
import time

from . import agent_projects as A, diagnose, probe, produce, resources, sizes
from .apiutil import _wrong_kind, _incomplete_image, MIN_BYTES, MEDIA_IMAGE
from .executor import GATE, Job, run_batch
from .store import LOCK, read_json, read_text, write_json, write_text


@lru_cache(maxsize=4096)
def _checked(path: str, size: int, modified: int) -> bool:
    try:
        if size < MIN_BYTES or _wrong_kind(path) or _incomplete_image(path):
            return False
        if Path(path).suffix.lower() in MEDIA_IMAGE:
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                im.load()
        return True
    except Exception:
        return False


def valid_output(path: str) -> bool:
    try:
        p = Path(path)
        st = p.stat()
        return _checked(str(p), st.st_size, st.st_mtime_ns)
    except OSError:
        return False


def settings(pj, caps: list, cfg=None) -> dict:
    cfg = cfg or {}
    saved = read_json(pj.p("07_检查与记录", "production-settings.json"), {}) or {}
    opts = pj.meta().get("options", {})
    result = {}
    for kind in A.LEGACY_KINDS:
        media = "video" if kind == "video" else "image"
        compatible = [c for c in caps if media in c.get("supports", [])]
        chain = cfg.get("chains", {}).get("video" if media == "video" else ("asset" if kind == "asset" else "storyboard"), [])
        first = chain[0] if chain else {}
        first = first if isinstance(first, dict) else {"provider": first}
        pid = opts.get(media + "_provider") or first.get("provider") or (compatible[0]["id"] if compatible else "")
        cap = next((c for c in compatible if c["id"] == pid), {})
        model = opts.get(media + "_model") or (first.get("model") if first.get("provider") == pid else "") or cap.get(media, {}).get("default_model", "")
        result[kind] = dict(provider=pid, model=model, concurrency=max(1, min(512, int(cfg.get("defaults", {}).get("concurrency", 3)))))
        result[kind].update(saved.get(kind) or {})
    return result


def validate_settings(value: dict, caps: list) -> dict:
    out = {}
    byid = {c["id"]: c for c in caps}
    for kind, row in value.items():
        if kind not in A.LEGACY_KINDS or not isinstance(row, dict):
            raise ValueError("生产设置类别无效")
        conc = int(row.get("concurrency", 3))
        if not 1 <= conc <= 512:
            raise ValueError("每类并发须在 1–512 之间")
        media = "video" if kind == "video" else "image"
        pid = row.get("provider", "")
        if pid not in byid or media not in byid[pid].get("supports", []):
            raise ValueError(f"服务商不支持该类别：{kind}")
        out[kind] = {"provider": pid, "model": str(row.get("model", "")), "concurrency": conc}
        resolution = str(row.get("image_resolution") or "").upper()
        if resolution and resolution not in ("1K", "2K", "4K"):
            raise ValueError("图片清晰度须为 1K、2K 或 4K")
        if media == "image":
            out[kind]["image_resolution"] = resolution
        override = row.get("override") or {}
        if not isinstance(override, dict) or set(override) - {"size", "ratio", "duration", "resolution"}:
            raise ValueError("不支持的生产参数覆盖")
        out[kind]["override"] = {k: v for k, v in override.items() if v not in (None, "")}
    return out


def preview(pj, cfg: dict, caps: list, resolve_cfg, only=None) -> dict:
    data = A.view(pj)
    rows = data["tasks"]
    saved = settings(pj, caps, cfg)
    wanted = set(only) if only is not None else {t["key"] for t in rows if t["selected"]}
    if wanted - {t["key"] for t in rows}:
        raise ValueError("所选任务已变化，请刷新")
    selected = [t for t in rows if t["key"] in wanted]
    done = {t["key"] for t in rows if valid_output(str(A.inside(pj.root, t["output"])))}
    todo = [t for t in selected if t["key"] not in done]
    blocked, pcs = [], {}
    capmap = {c["id"]: c for c in caps}
    for t in todo:
        try:
            kind = t["kind"]
            for dep in t["dependencies"]:
                if dep not in wanted and dep not in done:
                    raise ValueError(f"缺少勾选的上游：{dep}（可点“补选依赖”）")
            for r in A.references(t):
                rel = r.get("file_ref") or r.get("url") or ""
                if rel.startswith(("http://", "https://")) and data["publication"].get("legacy"):
                    continue
                target = A.inside(pj.root, rel)
                if not valid_output(str(target)) and not any(x["output"] == rel and x["key"] in wanted for x in rows):
                    raise ValueError(f"缺失或无效参考图：{rel}")
            dest = A.inside(pj.root, t["output"])
            if dest.exists() and not valid_output(str(dest)):
                raise ValueError("目标文件已存在但校验失败，请先备份移走或手动替换，避免误跳过")
            if not read_text(str(A.inside(pj.root, t["prompt_ref"]))).strip():
                raise ValueError("提示词为空")
            row = saved[kind]
            media = "video" if kind == "video" else "image"
            cap = capmap.get(row["provider"], {}).get(media, {})
            if not row["model"] or row["model"] not in cap.get("models", []):
                raise ValueError("模型不在当前能力清单，请在设置中刷新模型或选择已声明的模型")
            effective = dict(cap, **cap.get("model_options", {}).get(row["model"], {}))
            p = dict(t.get("params") or {})
            if cfg.get("agent_features", {}).get("advanced"):
                p.update(row.get("override") or {})
            if media == "image" and row.get("image_resolution"):
                p["resolution"] = row["image_resolution"]
            if media == "image" and p.get("resolution") and p["resolution"] not in effective.get("resolutions", []):
                raise ValueError("当前图片模型不支持所选清晰度，请选择对应档位模型或恢复跟随任务")
            sizefield = "ratio" if media == "video" else "size"
            supported = effective.get("ratios" if media == "video" else "sizes")
            if not supported:
                raise ValueError("该模型的画幅能力未知，先补全服务商能力声明")
            value, note = sizes.resolve(p.get(sizefield), supported)
            if value is None:
                raise ValueError(note)
            if str(value) != str(p.get(sizefield)) and sizes.as_ratio(value) != sizes.as_ratio(p.get(sizefield)):
                raise ValueError(f"请求 {p.get(sizefield)} 与服务商支持的 {supported} 不兼容；请显式修改参数或让 Agent 修订")
            p[sizefield] = value
            refs = len({r.get("file_ref") or r.get("url") for r in A.references(t)})
            maximum = effective.get("max_refs")
            if refs and (maximum is None or refs > maximum):
                raise ValueError(f"参考图 {refs} 张，服务商上限 {maximum if maximum is not None else '未知'}")
            if media == "video":
                if p.get("resolution") and p["resolution"] not in effective.get("resolutions", []):
                    raise ValueError("该清晰度不在模型能力中，请清空覆盖或选择已支持的清晰度")
                rules = {}
                import re
                raw_rules = ((cfg.get("providers") or {}).get(row["provider"], {}).get("durations") or "")
                for rule in re.split(r"[;\n]+", str(raw_rules)):
                    if "=" in rule:
                        model, seconds = rule.split("=", 1)
                        rules[model.strip()] = [int(s) for s in re.split(r"[,，\s]+", seconds.strip()) if s.isdigit() and int(s) > 0]
                durations = rules.get(row["model"]) or rules.get("*") or effective.get("durations")
                if not durations or float(p.get("duration", 0)) not in [float(d) for d in durations]:
                    raise ValueError(f"时长 {p.get('duration')} 秒不在当前模型能力 {durations or '未知'} 中")
            t["params"] = p
            if kind not in pcs:
                pc = resolve_cfg(cfg, {"provider": row["provider"], "model": row["model"]}, "video" if media == "video" else "asset")
                pc["soften_rounds"] = 0
                pc["agent_mode"] = True
                pcs[kind] = pc
        except (ValueError, OSError, KeyError, TypeError) as exc:
            blocked.append({"key": t["key"], "reason": str(exc)})
    return {"revision": data["publication"].get("revision", ""), "selected": len(selected),
            "keys": [t["key"] for t in selected],
            "reuse": len(selected) - len(todo), "todo": len(todo), "blocked": blocked,
            "rows": [{"kind": k, **saved[k], "count": sum(t["kind"] == k for t in todo),
                      "global_limit": GATE.snapshot()["global_limit"],
                      "provider_limit": GATE.snapshot()["per_provider_limit"].get(saved[k]["provider"])}
                     for k in saved if any(t["kind"] == k for t in todo)],
            "_tasks": todo, "_providers": pcs, "_settings": saved}


def public_preview(result):
    return {k: v for k, v in result.items() if not k.startswith("_")}


class ChildJob(Job):
    """Keep per-kind scheduler states while presenting one cancellable production run."""
    def __init__(self, parent, kind, count, conc, pc):
        super().__init__(kind, count, conc, parent.project_root, parent.project_name, pc["provider"], pc["model"])
        self.parent = parent

    def set_item(self, key, **kw):
        super().set_item(key, **kw)
        self.parent.set_item(key, **kw)

    def log(self, key, message):
        super().log(key, message)
        self.parent.log(key, message)


def start(pj, cfg, caps, resolve_cfg, jobs, only=None, expected_revision=None):
    with LOCK:
        if jobs.list(project_root=pj.root, active_only=True):
            raise ValueError("本项目已有运行任务，请等待结束或先停止，避免重复收费")
        result = preview(pj, cfg, caps, resolve_cfg, only)
        if expected_revision is not None and result["revision"] != expected_revision:
            raise ValueError("计划已更新，请重新预览生产范围")
        if result["blocked"]:
            return dict(public_preview(result), ok=False)
        if not result["todo"]:
            return dict(public_preview(result), ok=True, message="已选产物均有效，已复用，无需生成")
        tasks = copy.deepcopy(result["_tasks"])
        settings_ = result["_settings"]
        job = jobs.create("production", len(tasks), sum(settings_[k]["concurrency"] for k in result["_providers"]),
                          project_root=pj.root, project_name=pj.meta().get("title", ""))
        directory = f"07_检查与记录/jobs/{job.id}"
        # Freeze prompt bytes now. Editing releases or selections cannot change this job.
        for i, t in enumerate(tasks):
            text = read_text(str(A.inside(pj.root, t["prompt_ref"])))
            t["prompt_ref"] = f"{directory}/prompts/{i:05}.txt"
            write_text(str(A.inside(pj.root, t["prompt_ref"])), text)
            job.set_item(t["key"], state="pending")
        write_json(pj.p(*directory.split("/"), "snapshot.json"),
                   {"revision": result["revision"], "tasks": tasks, "settings": settings_, "created_at": time.time()})
        children, threads = [], []
        bykey = {t["key"]: t for t in tasks}

        def ready(t):
            for dep in t["dependencies"]:
                if dep in bykey:
                    state = job.items[dep]["state"]
                    if state in ("failed", "aborted", "cancelled"):
                        return None, f"上游 {dep} 未完成"
                    if state not in ("ok", "skipped"):
                        return False, f"等待 {dep}"
            return True, ""

        def run_kind(kind, pc):
            items = [t for t in tasks if t["kind"] == kind]
            child = ChildJob(job, kind, len(items), settings_[kind]["concurrency"], pc)
            children.append(child)
            try:
                worker = (produce.make_video_worker(pj, pc) if kind == "video" else
                          produce.make_image_worker(pj, pc, "asset" if kind == "asset" else "storyboard"))
                def checked(t, log, cancel):
                    value = worker(t, log, lambda: job.cancelled or cancel())
                    if not valid_output(str(A.inside(pj.root, t["output"]))):
                        raise RuntimeError("服务商返回后产物未通过文件校验")
                    return value
                child.cancelled = job.cancelled
                run_batch(child, items, checked, key_of=lambda t: t["key"], ready_of=ready,
                          max_retry=max(0, min(5, int(cfg.get("defaults", {}).get("max_retry", 2)))))
            except Exception as exc:
                for t in items:
                    if job.items[t["key"]]["state"] not in ("ok", "skipped", "failed"):
                        job.set_item(t["key"], state="failed", msg=str(exc))
                job.log(kind, str(exc))

        def go():
            try:
                for kind, pc in result["_providers"].items():
                    thread = threading.Thread(target=run_kind, args=(kind, pc), daemon=True)
                    threads.append(thread)
                    thread.start()
                while any(t.is_alive() for t in threads):
                    if job.cancelled:
                        for child in children:
                            child.cancelled = True
                    time.sleep(.1)
                job.status = "cancelled" if job.cancelled else ("error" if job.counts().get("failed") else "done")
            finally:
                job.finished_at = time.time()
                write_json(pj.p(*directory.split("/"), "result.json"), job.snapshot())
        threading.Thread(target=go, daemon=True).start()
        return dict(public_preview(result), ok=True, job_id=job.id)


def runtime(jobs) -> dict:
    gate = GATE.snapshot()
    resources.sample(gate["global_inflight"])
    # Only actual completed calls form the denominator; persisted failure rows do not.
    samples = []
    for job in list(jobs.jobs.values()):
        samples.extend(it for it in job.snapshot()["items"].values() if it.get("state") in ("ok", "failed"))
    recent = samples[-200:]
    limited = sum((it.get("diag") or {}).get("code") == "RATE_LIMITED" for it in recent)
    return {"usage": resources.snapshot(), "gates": gate, "active_jobs": jobs.active_count(),
            "advice": resources.advise(gate["production_peak"], limited, len(recent))}
