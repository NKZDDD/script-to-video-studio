"""Production-only orchestration. No language-model client or prompt rewriting."""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
import re
import threading
import time

from . import agent_projects as A, audio_refs, diagnose, probe, produce, resources, sizes, submitted
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


def _seconds(values) -> str:
    """[4, 5, …, 12] → 「4–12 秒」；[5, 10, 30] → 「5、10、30 秒」。"""
    nums = sorted({float(v) for v in values})
    if len(nums) > 2 and all(n.is_integer() for n in nums) and nums == [float(n) for n in range(int(nums[0]), int(nums[-1]) + 1)]:
        return f"{nums[0]:g}–{nums[-1]:g} 秒"
    return "、".join(f"{n:g}" for n in nums) + " 秒"


_FINAL = ("ok", "skipped", "failed", "cancelled", "aborted", "interrupted")


def busy(jobs, root: str) -> dict:
    """本项目在跑的批次里还没结束的任务 → 它所在的批次。

    同一个项目可以同时开几批，只要任务不重叠：同一个任务在两批里各提交一次，
    就是同一段付两次钱。
    """
    out = {}
    if jobs is None:
        return out
    for job in list(jobs.jobs.values()):
        if job.project_root != root or job.status in ("done", "error", "cancelled", "aborted"):
            continue
        for key, item in list(job.items.items()):
            if item.get("state") not in _FINAL:
                out[key] = job
    return out


def retries(cfg: dict) -> int:
    return max(0, int((cfg or {}).get("defaults", {}).get("max_retry", 2)))


def soften_rounds(cfg: dict) -> int:
    """被审核拒绝后用 LLM 改写提示词再重发，最多几轮。0 = 关掉。"""
    from . import soften
    v = (cfg or {}).get("defaults", {}).get("soften_rounds")
    return soften.clamp_rounds(soften.DEFAULT_ROUNDS if v in (None, "") else v)


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
        result[kind] = dict(provider=pid, model=model, concurrency=max(1, int(cfg.get("defaults", {}).get("concurrency", 3))))
        result[kind].update(saved.get(kind) or {})
    return result


def validate_settings(value: dict, caps: list) -> dict:
    out = {}
    byid = {c["id"]: c for c in caps}
    for kind, row in value.items():
        if kind not in A.LEGACY_KINDS or not isinstance(row, dict):
            raise ValueError("生产设置类别无效")
        conc = int(row.get("concurrency", 3))
        if conc < 1:
            raise ValueError("每类并发至少为 1")
        media = "video" if kind == "video" else "image"
        pid = row.get("provider", "")
        if pid not in byid or media not in byid[pid].get("supports", []):
            raise ValueError(f"服务商不支持该类别：{kind}")
        out[kind] = {"provider": pid, "model": str(row.get("model", "")), "concurrency": conc}
        resolution = str(row.get("image_resolution") or "").upper()
        if resolution and resolution not in ("1K", "2K", "4K"):
            raise ValueError("图片清晰度须为 1K、2K 或 4K")
        shape = str(row.get("size" if media == "image" else "ratio") or "").strip()
        if shape and not re.fullmatch(r"[0-9A-Za-z:x.]{1,20}", shape):
            raise ValueError("比例 / 尺寸只能是 16:9、1024x1536 这类写法")
        out[kind]["size" if media == "image" else "ratio"] = shape
        if media == "image":
            out[kind]["image_resolution"] = resolution
        else:
            clarity = str(row.get("resolution") or "").strip()
            if clarity and not re.fullmatch(r"[0-9A-Za-z]{1,12}", clarity):
                raise ValueError("视频清晰度只能是 720p、1080p 这类写法")
            out[kind]["resolution"] = clarity
            seconds = row.get("duration")
            if seconds in (None, ""):
                out[kind]["duration"] = ""
            else:
                if isinstance(seconds, bool) or not 1 <= int(seconds) <= 600:
                    raise ValueError("视频秒数须在 1–600 之间")
                out[kind]["duration"] = int(seconds)
        override = row.get("override") or {}
        if not isinstance(override, dict) or set(override) - {"size", "ratio", "duration", "resolution"}:
            raise ValueError("不支持的生产参数覆盖")
        out[kind]["override"] = {k: v for k, v in override.items() if v not in (None, "")}
    return out


def preview(pj, cfg: dict, caps: list, resolve_cfg, only=None, running=None) -> dict:
    data = A.view(pj)
    rows = data["tasks"]
    saved = settings(pj, caps, cfg)
    wanted = set(only) if only is not None else {t["key"] for t in rows if t["selected"]}
    if wanted - {t["key"] for t in rows}:
        raise ValueError("所选任务已变化，请刷新")
    running = running or {}
    selected = [t for t in rows if t["key"] in wanted]
    done = {t["key"] for t in rows if valid_output(str(A.inside(pj.root, t["output"])))}
    elsewhere = [t["key"] for t in selected if t["key"] not in done and t["key"] in running]
    todo = [t for t in selected if t["key"] not in done and t["key"] not in running]
    coming = wanted | set(running)
    blocked, pcs = [], {}
    capmap = {c["id"]: c for c in caps}
    for t in todo:
        try:
            kind = t["kind"]
            pending = submitted.load(pj.root, t["output"])
            if pending:
                row = saved[kind]
                if row["provider"] != pending.get("provider"):
                    raise ValueError(f"已有任务 {pending['task_id']} 属于「{pending.get('provider')}」，"
                                     "请切回原服务商取回，或手动放弃旧任务后重新生成")
                if kind not in pcs:
                    pc = resolve_cfg(cfg, {"provider": pending["provider"], "model": pending.get("model", "")},
                                     "video" if kind == "video" else "asset")
                    pc["soften_rounds"], pc["agent_mode"] = 0, True
                    pcs[kind] = pc
                t["resume_only"] = True
                # 取回已生成结果不依赖当前提示词、参考图、模型是否仍可新建。
                continue
            for dep in t["dependencies"]:
                if dep not in coming and dep not in done:
                    raise ValueError(f"缺少勾选的上游：{dep}（可点“补选依赖”）")
            for r in A.references(t):
                rel = r.get("file_ref") or r.get("url") or ""
                if rel.startswith(("http://", "https://")) and data["publication"].get("legacy"):
                    continue
                target = A.inside(pj.root, rel)
                if not valid_output(str(target)) and not any(x["output"] == rel and x["key"] in coming for x in rows):
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
            if kind == 'video':
                audio_refs.validate(pj, t, effective, cfg.get('upload') or {})
                t['_audio_capability'] = {k: effective[k] for k in ('max_audio_refs', 'supports_audio_refs') if k in effective}
            p = dict(t.get("params") or {})
            if cfg.get("agent_features", {}).get("advanced"):
                p.update(row.get("override") or {})
            if media == "image" and row.get("image_resolution"):
                p["resolution"] = row["image_resolution"]
            if media == "video" and row.get("duration"):
                p["duration"] = int(row["duration"])
            if media == "video" and row.get("resolution"):
                p["resolution"] = row["resolution"]
            if media == "image" and p.get("resolution") and p["resolution"] not in effective.get("resolutions", []):
                raise ValueError("当前图片模型不支持所选清晰度，请选择对应档位模型或恢复跟随任务")
            sizefield = "ratio" if media == "video" else "size"
            if row.get(sizefield):
                p[sizefield] = row[sizefield]
            supported = effective.get("ratios" if media == "video" else "sizes")
            if not supported:
                raise ValueError("该模型的画幅能力未知，先补全服务商能力声明")
            value, note = sizes.resolve(p.get(sizefield), supported)
            if value is None:
                raise ValueError(note)
            if str(value) != str(p.get(sizefield)) and sizes.as_ratio(value) != sizes.as_ratio(p.get(sizefield)):
                chosen = "这一行选的" if row.get(sizefield) else "任务里写的"
                raise ValueError(f"{chosen} {p.get(sizefield)} 与服务商支持的 {supported} 不兼容；"
                                 "在「本次服务商与并发」这一行的比例里选一个支持的，或让 Agent 修订")
            p[sizefield] = value
            refs = len({r.get("file_ref") or r.get("url") for r in A.references(t)})
            maximum = effective.get("max_refs")
            if refs and not effective.get("refs_unlimited") and (maximum is None or refs > maximum):
                raise ValueError(f"参考图 {refs} 张，服务商上限 {maximum if maximum is not None else '未知'}")
            if media == "video":
                if p.get("resolution") and p["resolution"] not in effective.get("resolutions", []):
                    raise ValueError(f"清晰度 {p['resolution']} 不在这个视频模型的能力里"
                                     f"（{'、'.join(effective.get('resolutions', [])) or '没有声明'}），"
                                     "在「本次服务商与并发」视频那一行的清晰度里重新选，或选「跟随任务」")
                rules = {}
                raw_rules = ((cfg.get("providers") or {}).get(row["provider"], {}).get("durations") or "")
                for rule in re.split(r"[;\n]+", str(raw_rules)):
                    if "=" in rule:
                        model, seconds = rule.split("=", 1)
                        rules[model.strip()] = [int(s) for s in re.split(r"[,，\s]+", seconds.strip()) if s.isdigit() and int(s) > 0]
                manual = rules.get(row["model"]) or rules.get("*")
                durations = manual or effective.get("durations")
                if not durations or float(p.get("duration", 0)) not in [float(d) for d in durations]:
                    who = f"「{capmap.get(row['provider'], {}).get('name') or row['provider']} / {row['model']}」"
                    source = "设置里手填的「已实测的模型时长档位」" if manual else "服务商的模型能力声明"
                    can = f"只支持 {_seconds(durations)}（来自{source}）" if durations else "没有声明支持哪些时长"
                    asked = (f"视频那一行选的是 {p.get('duration')} 秒" if row.get("duration")
                             else f"这一段要 {p.get('duration')} 秒")
                    raise ValueError(f"{asked}，但视频模型{who}{can}。"
                                     "处理办法：在「本次服务商与并发」视频那一行的秒数里选一个支持的档位；"
                                     "换一个支持这个时长的视频模型；或者让 Agent 按不超过上限的时长重新切段；"
                                     "如果确认服务商实际支持，到设置 → 服务商里补上「已实测的模型时长档位」。")
            t["params"] = p
            if kind not in pcs or pcs[kind].get("model") != row["model"]:
                pc = resolve_cfg(cfg, {"provider": row["provider"], "model": row["model"]}, "video" if media == "video" else "asset")
                pc["soften_rounds"] = soften_rounds(cfg)
                pc["agent_mode"] = True
                pcs[kind] = pc
        except (ValueError, OSError, KeyError, TypeError) as exc:
            blocked.append({"key": t["key"], "reason": str(exc)})
    return {"revision": data["publication"].get("revision", ""), "selected": len(selected),
            "keys": [t["key"] for t in selected],
            "reuse": len(selected) - len(todo) - len(elsewhere), "todo": len(todo), "blocked": blocked,
            "elsewhere": elsewhere, "max_retry": retries(cfg), "soften_rounds": soften_rounds(cfg),
            "resuming": sum(1 for t in todo if t.get("submitted")),
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


def start(pj, cfg, caps, resolve_cfg, jobs, only=None, expected_revision=None, llm_factory=None):
    """`llm_factory` 只用在一件事上：被审核拒绝时改写提示词再重发（见 soften.py）。"""
    with LOCK:
        others = busy(jobs, pj.root)
        result = preview(pj, cfg, caps, resolve_cfg, only, running=others)
        if expected_revision is not None and result["revision"] != expected_revision:
            raise ValueError("计划已更新，请重新预览生产范围")
        if result["blocked"]:
            return dict(public_preview(result), ok=False)
        if not result["todo"]:
            message = (f"选中的 {len(result['elsewhere'])} 项正在另一批里生成，这次没有新的要提交"
                       if result["elsewhere"] else "已选产物均有效，已复用，无需生成")
            return dict(public_preview(result), ok=True, message=message)
        tasks = copy.deepcopy(result["_tasks"])
        settings_ = result["_settings"]
        job = jobs.create("production", len(tasks), sum(settings_[k]["concurrency"] for k in result["_providers"]),
                          project_root=pj.root, project_name=pj.meta().get("title", ""))
        job.max_retry = retries(cfg)
        directory = f"07_检查与记录/jobs/{job.id}"
        # Freeze prompt bytes now. Editing releases or selections cannot change this job.
        for i, t in enumerate(tasks):
            if not t.get("resume_only"):
                text = read_text(str(A.inside(pj.root, t["prompt_ref"])))
                t["prompt_ref"] = f"{directory}/prompts/{i:05}.txt"
                write_text(str(A.inside(pj.root, t["prompt_ref"])), text)
            job.set_item(t["key"], state="pending")
        write_json(pj.p(*directory.split("/"), "snapshot.json"),
                   {"revision": result["revision"], "tasks": tasks, "settings": settings_, "created_at": time.time()})
        # 已经交出去的这一批取消勾选：再点一次「开始生产」不会把同一批重新提交，
        # 失败了要重做也得人自己再勾上。
        A.select(pj, {k: False for k in result["keys"]})
        children, threads = [], []
        bykey = {t["key"]: t for t in tasks}

        def ready(t):
            if t.get("resume_only"):
                return True, ""
            waits = []
            for dep in t["dependencies"]:
                owner = job if dep in bykey else others.get(dep)
                if owner is None:
                    continue
                state = (owner.items.get(dep) or {}).get("state")
                if state in ("failed", "aborted", "cancelled"):
                    return None, f"上游 {dep} 未完成" + ("" if owner is job else "（在另一批里）")
                if state not in ("ok", "skipped"):
                    waits.append(dep + ("" if owner is job else "（另一批在生成）"))
            return (False, "等待上游：" + "、".join(waits)) if waits else (True, "")

        def run_kind(kind, pc):
            items = [t for t in tasks if t["kind"] == kind]
            child = ChildJob(job, kind, len(items), settings_[kind]["concurrency"], pc)
            children.append(child)
            try:
                worker = (produce.make_video_worker(pj, pc, llm_factory) if kind == "video" else
                          produce.make_image_worker(pj, pc, "asset" if kind == "asset" else "storyboard",
                                                    llm_factory))
                def checked(t, log, cancel):
                    use = worker
                    if t.get("resume_only"):
                        record = submitted.load(pj.root, t["output"])
                        if not record:
                            raise produce.ApiError("原任务记录已变化，请重新查看生产计划，不会自动创建新任务。",
                                                   kind="task_fatal", err_code="submitted_pending")
                        original = resolve_cfg(cfg, {"provider": record["provider"], "model": record.get("model", "")},
                                               "video" if kind == "video" else "asset")
                        original["agent_mode"], original["soften_rounds"] = True, 0
                        use = (produce.make_video_worker(pj, original) if kind == "video" else
                               produce.make_image_worker(pj, original, "asset" if kind == "asset" else "storyboard"))
                    value = use(t, log, lambda: job.cancelled or cancel())
                    if not valid_output(str(A.inside(pj.root, t["output"]))):
                        raise RuntimeError("服务商返回后产物未通过文件校验")
                    return value
                child.cancelled = job.cancelled
                run_batch(child, items, checked, key_of=lambda t: t["key"], ready_of=ready,
                          max_retry=retries(cfg))
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
    # Keep all running batches visible, even when newer finished batches fill the default list limit.
    batches = jobs.list(limit=None)
    batches.sort(key=lambda j: j["status"] in ("done", "error", "cancelled", "aborted"))
    return {"usage": resources.snapshot(), "gates": gate, "active_jobs": jobs.active_count(), "jobs": batches,
            "advice": resources.advise(gate["production_peak"], limited, len(recent))}
