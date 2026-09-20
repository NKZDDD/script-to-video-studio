"""Agent project contract, immutable publications and persistent production selection.

Agent owns releases; Studio owns locks, accepted snapshots and runtime records.
A READY marker is a publication boundary, never permission to start paid work.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from . import paths, probe
from .store import LOCK, Project, read_json, read_text, write_json, write_text

SCHEMA = "studio.production-plan/v1"
KINDS = {"asset": "资产图", "scstate": "场景状态图", "board": "交接板整板",
         "region": "交接板区域图", "video": "分段视频"}
LEGACY_KINDS = dict(KINDS, storyboard="旧项目故事板")
SEMANTICS = {"CHAR": "人物身份", "LOOK": "造型与服装", "COST": "独立服装", "CT": "人物状态",
             "GRP": "群体", "CRE": "生物", "LOC": "场景", "PROP": "道具",
             "VEH": "载具", "VFX": "特效", "SCSTATE": "场景状态",
             "ABC": "交接板", "REGION": "区域图", "VIDEO": "分段视频", "AUD": "声音身份"}
FEATURES = {"advanced": "高级参数", "batch": "批量创建", "post": "字幕与后期",
            "logs": "详细日志", "legacy_import": "旧材料导入"}
DEFAULTS = {"source_type": "剧本", "episode_strategy": "按原文", "episode_count": 1,
            "medium": "真人影视", "style": "电影感", "culture": "按原文",
            "adaptation": "忠于原文", "notes": "", "ratio": "16:9", "duration": 30,
            "audio": "模型原生音频", "subtitles": False, "dialogue_language": "中文",
            "prompt_language": "中文", "reuse": True, "costume": "按剧情需要",
            "handoff": "按边界需要", "atlas": False, "offline_board": False,
            "emotion_curve": False, "report": False, "image_provider": "",
            "image_model": "", "video_provider": "", "video_model": ""}
DIRECTORIES = ["00_项目说明", "01_剧本与分段", "02_固定资产", "03_提示词/releases",
               "03b_场景状态图", "04_交接板/区域图", "05_分段视频", "06_成片", "07_检查与记录"]


def digest(data) -> str:
    raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def inside(root: str, rel: str) -> Path:
    """Reject absolute, drive-relative, traversal and symlink/junction escapes."""
    if not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        raise ValueError(f"必须使用项目内的相对路径：{rel!r}")
    if any(x in ("", ".", "..") for x in rel.split("/")):
        raise ValueError(f"路径不能含空段或 ..：{rel}")
    base = Path(root).resolve()
    target = base.joinpath(*rel.split("/")).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ValueError(f"路径越出项目：{rel}")
    return target


def skill_source() -> Path:
    return Path(paths.res("skills", "production-skill"))


def file_manifest(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): digest(p.read_bytes())
            for p in sorted(folder.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def schema_document() -> dict:
    ref = {"type": "object", "required": ["asset_id", "file_ref", "image_n"],
           "properties": {"asset_id": {"type": "string"}, "file_ref": {"type": "string"},
                          "image_n": {"type": "integer", "minimum": 1}}}
    task = {"type": "object", "required": ["key", "output", "prompt_ref", "params", "reference_images"],
            "properties": {"key": {"type": "string"}, "output": {"type": "string"},
                           "prompt_ref": {"type": "string"}, "params": {"type": "object"},
                           "reference_images": {"type": "array", "items": ref},
                           "handoff_refs": {"type": "array", "items": ref},
                           "episode": {"type": "string"}, "board": {"type": "string"},
                           "region": {"enum": ["A", "B", "C"]}}}
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": SCHEMA,
            "type": "object", "required": ["schema", "project_id", "revision", "resources"] + [k + "_tasks" for k in KINDS],
            "additionalProperties": False,
            "properties": {"schema": {"const": SCHEMA}, "project_id": {"type": "string"},
                           "revision": {"type": "string", "pattern": "^r[0-9]{4,}$"},
                           "resources": {"type": "array", "items": {"type": "object",
                               "required": ["id", "name", "semantic", "parent_id", "episodes", "task_key"],
                               "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                                   "semantic": {"enum": list(SEMANTICS)}, "parent_id": {"type": ["string", "null"]},
                                   "episodes": {"type": "array", "items": {"type": "string"}},
                                   "task_key": {"type": ["string", "null"]}}}},
                           **{k + "_tasks": {"type": "array", "items": task} for k in KINDS}}}
    schema["properties"]["video_tasks"]["items"] = dict(task, required=task["required"] + ["episode", "handoff_refs"])
    return schema


def create_project(base: str, body: dict, capabilities: list) -> Project:
    title = str(body.get("title", "")).strip()
    if not title or len(title) > 100:
        raise ValueError("项目名称须为 1–100 个字符")
    options = dict(DEFAULTS, **{k: v for k, v in (body.get("options") or {}).items() if k in DEFAULTS})
    for key in ("episode_count", "duration"):
        options[key] = int(options[key])
        if not 1 <= options[key] <= (999 if key == "episode_count" else 600):
            raise ValueError(f"{key} 超出可配置范围")
    if options["ratio"] not in ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
        raise ValueError("不支持的画幅")
    source = skill_source()
    manifest = file_manifest(source)
    if "SKILL.md" not in manifest or not any(n.startswith("references/") for n in manifest) or not any(n.startswith("scripts/") for n in manifest):
        raise ValueError("完整生产技能包缺失，不能创建残缺项目")
    code = "PRJ_" + uuid.uuid4().hex[:10].upper()
    folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:60] or "项目"
    pj = Project(str(Path(base).resolve() / (folder + "_" + code)))
    os.makedirs(pj.root, exist_ok=False)
    try:
        for d in DIRECTORIES:
            inside(pj.root, d).mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, pj.p("00_项目说明", "skill", "production-skill"))
        write_text(pj.p("01_剧本与分段", "原始素材.txt"), str(body.get("source", "")))
        lock = {"schema": SCHEMA, "project_id": code, "project_root": pj.root,
                "options": options, "options_hash": digest(options), "skill_hash": digest(manifest),
                "skill_files": manifest, "capabilities": capabilities, "created_at": time.time()}
        write_json(pj.p("00_项目说明", "project-lock.json"), lock)
        write_json(pj.p("00_项目说明", "production.schema.json"), schema_document())
        pj.save_meta({"title": title, "project_code": code, "system": "v34", "workflow": "agent",
                      "tags": body.get("tags") or [], "created_at": time.time(), "options": options})
        write_text(pj.p("00_项目说明", "production-contract.md"), contract(pj, lock))
        write_text(pj.p("00_项目说明", "AGENT_TASK.md"), template(pj, lock))
    except Exception:
        # Retain an incomplete directory for diagnosis; never silently remove user data.
        write_text(pj.p("00_项目说明", "创建未完成.txt"), "创建未完成，请重新创建项目，保留此目录用于排查。")
        raise
    return pj


def contract(pj: Project, lock: dict) -> str:
    return f"""# 本机生产契约

项目：{lock['project_id']}
唯一项目根目录：`{pj.root}`
协议：{SCHEMA}；选项散列：{lock['options_hash']}；技能包散列：{lock['skill_hash']}。

先读 project-lock.json、production.schema.json 和 skill/production-skill/SKILL.md；按需路由完整 references/，保留 scripts/。
生产步骤：锁定配置 → 剧本分段 → 身份/造型/状态等固定资产 → 空间状态与按需交接板 → 七段式视频提示词 → 分层审计。
原始素材：01_剧本与分段/原始素材.txt；缺原文时请求补齐，禁止凭空创建剧本。
Studio 不执行文本分析。请直接在此本机项目下产出，不要产出到临时对话目录再要求用户导入。

## 发布
每次用新目录 03_提示词/releases/r0001（后续递增 r0002…），写 tasks.json、images/*.txt、videos/*.txt。
tasks.json 使用 schema、project_id、revision、resources 和 asset_tasks/scstate_tasks/board_tasks/region_tasks/video_tasks 五个数组。
task 必填 key/output/prompt_ref/params/reference_images；视频须有 handoff_refs（无交接板时写 []）。
所有路径相对项目根，使用 /，不能带 ..、绝对路径或外部 URL。prompt_ref 指向本次发布中的 UTF-8 文本。
输出分类：02_固定资产、03b_场景状态图、04_交接板、04_交接板/区域图、05_分段视频。
resources 每个对象：id、name、semantic、parent_id（无则 null）、episodes（全剧共享则 []）、task_key。
semantic 可选 {', '.join(SEMANTICS)}；一个 task 对应一个资源，AUD 为声音身份元数据，task_key=null。
人物以身份为父节点挂 LOOK/CT；共用资源只保留一个 ID，episodes 记录使用集，不为每集复制。
reference_images 各项包含 asset_id（资源 ID）、file_ref（对应任务输出）。图片 image_n=1..N；视频先上传 handoff_refs，再上传 reference_images，两组合起来的 image_n 必须是连续 1..N。
先声明上游任务，再引用输出；父资源关系与实际参考依赖均不得循环。已有外部图片应登记为资源和任务后由用户手动放图。
交接板整板不直接给视频；区域任务必须有 board=整板任务 key、region=A/B/C，且以该整板为唯一参考，独立生成而非裁切。
handoff_refs 项另带 role=IN_B/IN_C/OUT_A，board=整板 key，必须引用对应区域，同一 IN 边界的 B/C 来自同一整板。
视频必须填写 episode（例如 EP01），且与对应资源的唯一 episodes 条目一致；params 至少包含 ratio/duration。图片 params 至少包含 size。能力未知先提出修订，禁止静默截短、裁图或丢参考。
剧情文字可以出现；subtitles 只控制字幕。AUD 不变成收费音频任务。没有强制旧故事板阶段。

最后原子写 READY.json（临时文件写完再替换）：
```json
{{"schema":"{SCHEMA}","project_id":"{lock['project_id']}","revision":"r0001",
 "options_hash":"{lock['options_hash']}","skill_hash":"{lock['skill_hash']}",
 "files":{{"tasks.json":"该文件字节的SHA256","images/example.txt":"该文件字节的SHA256"}}}}
```
files 使用相对本次发布目录的路径，必须覆盖 tasks.json 和每个被引用的提示词文件。
Studio 自动发现，验证后保存不可变快照。不能修改已发布版本；改动需新版本。
相同 key 的输出/提示词/参数/依赖有改变时必须改用新输出文件名（递增版本），禁止把旧成品当成新版本复用。
Studio 拥有 00_项目说明、07_检查与记录、selections.json、active-plan.json；请勿修改这些运行态文件或服务商凭据。
检测通过只表示结构就绪，不代表视觉质量通过，也不触发付费生成。
"""


def template(pj: Project, lock: dict) -> str:
    return f"""# Agent 生产任务

请在本机目录 `{pj.root}` 完成项目「{pj.meta()['title']}」。
先阅读 `{pj.p('00_项目说明', 'production-contract.md')}`，随后读取同目录 project-lock.json 和完整 skill/production-skill/ 技能包。
原文在 `{pj.p('01_剧本与分段', '原始素材.txt')}`。若尚未提供原文，先要求补齐。

锁定选项（这些默认值已由用户在创建向导中确认）：
```json
{json.dumps(lock['options'], ensure_ascii=False, indent=2)}
```

按技能流程逐步规划，再按 production.schema.json 生成新发布目录及 READY.json。所有产物直接写入上面指定的绝对项目路径。
不能把中间产物放到其他目录并要求用户再搬运；不能改项目锁或自行调用收费生图/视频 API。
先审查已有版本及 07_检查与记录/agent-revisions 内的定向修订要求；只更改受影响任务并保留未变资产复用。
完成后列出发布版本、结构审计结果、需要用户查看的视觉审计项。
"""


def task_rows(plan: dict, legacy=False) -> list:
    return [dict(t, kind=k) for k in (LEGACY_KINDS if legacy else KINDS)
            for t in plan.get(k + "_tasks", [])]


def references(task: dict) -> list:
    from .video_refs import primary_refs
    refs = list(task.get("reference_images") or []) + list(primary_refs(task))
    if task.get("aux_reference") and not task.get("reference_images"):
        refs.append({"file_ref": task["aux_reference"]})
    return refs


def no_cycles(graph: dict, label: str):
    visiting, done = set(), set()
    def visit(key):
        if key in visiting:
            raise ValueError(f"{label}存在循环：{key}")
        if key in done:
            return
        visiting.add(key)
        for dep in graph.get(key, []):
            visit(dep)
        visiting.remove(key)
        done.add(key)
    for key in graph:
        visit(key)


def validate_release(pj: Project, revision: str) -> tuple:
    if not re.fullmatch(r"r\d{4,}", revision):
        raise ValueError("发布目录需为 r0001 形式")
    folder = inside(pj.root, "03_提示词/releases/" + revision)
    ready = read_json(str(folder / "READY.json"))
    lock = read_json(pj.p("00_项目说明", "project-lock.json"))
    if not isinstance(ready, dict) or not isinstance(lock, dict):
        raise ValueError("READY 或项目锁缺失/未写完整")
    if not read_text(pj.p("01_剧本与分段", "原始素材.txt")).strip():
        raise ValueError("缺少原始素材，请先在 Agent 契约页补充原文，再让 Agent 发布")
    for k, want in {"schema": SCHEMA, "project_id": lock["project_id"], "revision": revision,
                    "options_hash": lock["options_hash"], "skill_hash": lock["skill_hash"]}.items():
        if ready.get(k) != want:
            raise ValueError(f"READY.{k} 与项目契约不一致")
    files = ready.get("files")
    if not isinstance(files, dict) or "tasks.json" not in files:
        raise ValueError("READY.files 必须含 tasks.json 及全部提示词校验和")
    blobs = {}
    for rel, sha in files.items():
        data = inside(str(folder), rel).read_bytes()
        if digest(data) != sha:
            raise ValueError(f"发布文件散列不匹配：{rel}")
        blobs[rel] = data
    plan = json.loads(blobs["tasks.json"].decode("utf-8-sig"))
    if not isinstance(plan, dict):
        raise ValueError("tasks.json 必须是对象")
    if plan.get("schema") != SCHEMA or plan.get("project_id") != lock["project_id"] or plan.get("revision") != revision:
        raise ValueError("tasks.json 的协议/项目/版本不一致")
    if any(k not in set(["schema", "project_id", "revision", "resources"] + [k + "_tasks" for k in KINDS]) for k in plan):
        raise ValueError("tasks.json 含契约之外字段；新项目不支持旧故事板数组")
    for k in KINDS:
        if not isinstance(plan.get(k + "_tasks"), list):
            raise ValueError(f"缺少 {k}_tasks 数组")
        if any(not isinstance(t, dict) for t in plan[k + "_tasks"]):
            raise ValueError(f"{k}_tasks 中每个任务必须是对象")
    rows = task_rows(plan)
    keys, outputs = {}, {}
    prefixes = dict(zip(KINDS, ["02_固定资产/", "03b_场景状态图/", "04_交接板/", "04_交接板/区域图/", "05_分段视频/"]))
    for t in rows:
        key = t.get("key")
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError(f"任务 key 缺失或重复：{key}")
        keys[key] = t
        output = t.get("output", "")
        target = inside(pj.root, output)
        normalized = output.casefold()
        if normalized in outputs or not output.startswith(prefixes[t["kind"]]):
            raise ValueError(f"输出重复或目录不匹配：{output}")
        if target.suffix.lower() not in ((".mp4", ".webm", ".mov") if t["kind"] == "video" else (".png", ".jpg", ".jpeg", ".webp")):
            raise ValueError(f"输出格式不支持：{output}")
        outputs[normalized] = key
        prompt = t.get("prompt_ref", "")
        inside(pj.root, prompt)
        prefix = f"03_提示词/releases/{revision}/"
        if not prompt.startswith(prefix) or prompt[len(prefix):] not in blobs or not blobs[prompt[len(prefix):]].decode("utf-8-sig").strip():
            raise ValueError(f"提示词不在本次发布或未纳入 READY 校验：{key}")
        p = t.get("params")
        if not isinstance(p, dict) or not (p.get("ratio") and isinstance(p.get("duration"), (float, int)) and p["duration"] > 0 if t["kind"] == "video" else p.get("size")):
            raise ValueError(f"缺少生产参数：{key}")
        if not isinstance(t.get("reference_images"), list) or (t["kind"] == "video" and not isinstance(t.get("handoff_refs"), list)):
            raise ValueError(f"缺少参考图数组：{key}")
        if any(not isinstance(r, dict) for r in t["reference_images"] + (t.get("handoff_refs") or [])):
            raise ValueError(f"参考图必须使用对象结构：{key}")
    resources = plan.get("resources")
    if not isinstance(resources, list):
        raise ValueError("缺少 resources")
    rids, mapped = {}, set()
    for r in resources:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"] or r["id"] in rids or not isinstance(r.get("name"), str) or not r["name"] or r.get("semantic") not in SEMANTICS or not isinstance(r.get("episodes"), list) or any(not isinstance(e, str) for e in r["episodes"]):
            raise ValueError("资源 ID/名称/分类/集号非法或重复")
        rids[r["id"]] = r
        tk = r.get("task_key")
        if r["semantic"] == "AUD":
            if tk is not None:
                raise ValueError("AUD 只记录声音身份，不创建收费任务")
        elif tk not in keys or tk in mapped:
            raise ValueError(f"资源任务映射不唯一或不存在：{r['id']}")
        else:
            mapped.add(tk)
            if keys[tk]["kind"] == "video" and (not isinstance(keys[tk].get("episode"), str) or not keys[tk]["episode"] or r["episodes"] != [keys[tk]["episode"]]):
                raise ValueError(f"视频必须指定唯一的集，且与资源所属集一致：{tk}")
    if mapped != set(keys):
        raise ValueError("每项生产任务必须登记到资源树")
    parents = {}
    for rid, r in rids.items():
        p = r.get("parent_id")
        if p and p not in rids:
            raise ValueError(f"资源父节点不存在：{rid}")
        parents[rid] = [p] if p else []
    no_cycles(parents, "资源树")
    graph = {}
    for t in rows:
        allrefs = references(t)
        for group in ([*t.get("handoff_refs", []), *t["reference_images"]],):
            if [r.get("image_n") for r in group] != list(range(1, len(group) + 1)):
                raise ValueError(f"参考图编号必须连续且按实际上传顺序：{t['key']}")
            if len({r.get("file_ref") for r in group}) != len(group):
                raise ValueError(f"重复参考会改变上传编号：{t['key']}")
        if any(k in t for k in ("storyboard_refs", "storyboard_ref", "aux_reference", "no_image_refs")):
            raise ValueError(f"新项目不接受旧参考字段：{t['key']}")
        graph[t["key"]] = []
        for r in allrefs:
            if r.get("url"):
                raise ValueError("新契约只接受项目内已登记的参考图")
            rr = rids.get(r.get("asset_id")) or {}
            dep = keys.get(rr.get("task_key")) or {}
            if not dep or dep["output"] != r.get("file_ref"):
                raise ValueError(f"引用未闭合：{t['key']} → {r.get('asset_id')}")
            inside(pj.root, r["file_ref"])
            graph[t["key"]].append(dep["key"])
        if t["kind"] == "region":
            board = keys.get(t.get("board"), {})
            if board.get("kind") != "board" or t.get("region") not in ("A", "B", "C") or graph[t["key"]] != [board["key"]]:
                raise ValueError(f"区域图必须且仅引用指定整板：{t['key']}")
        if t["kind"] == "video":
            ins = set()
            if any(keys[rids[r["asset_id"]]["task_key"]]["kind"] in ("region", "board") for r in t["reference_images"]):
                raise ValueError(f"区域图必须通过 handoff_refs 指定边界用途：{t['key']}")
            for r in t["handoff_refs"]:
                dep = keys[rids[r["asset_id"]]["task_key"]]
                role = r.get("role")
                if dep["kind"] != "region" or role not in ("IN_B", "IN_C", "OUT_A") or dep.get("region") != role[-1] or r.get("board") != dep.get("board"):
                    raise ValueError(f"视频交接引用须为对应区域且标明同源整板：{t['key']}")
                if role.startswith("IN_"):
                    ins.add(dep["board"])
            if len(ins) > 1 or any(keys[d]["kind"] == "board" for d in graph[t["key"]]):
                raise ValueError(f"视频不能混用入站整板或直接引用整板：{t['key']}")
    no_cycles(graph, "生产依赖")
    return plan, blobs, ready


def scan(pj: Project) -> dict:
    if pj.meta().get("workflow") != "agent":
        return {"legacy": True, "revision": "legacy", "errors": []}
    with LOCK:
        active = read_json(pj.p("07_检查与记录", "active-plan.json"), {})
        errors = []
        root = Path(pj.p("03_提示词", "releases"))
        revisions = sorted((p.name for p in root.glob("r*") if (p / "READY.json").is_file() and re.fullmatch(r"r\d{4,}", p.name)), key=lambda s: int(s[1:]))
        for revision in revisions:
            if active.get("revision") and int(revision[1:]) <= int(active["revision"][1:]):
                continue
            try:
                plan, blobs, ready = validate_release(pj, revision)
                old = pj.tasks() if active else {}
                oldrows = {t["key"]: t for t in task_rows(old)}
                oldoutputs = {t["output"].casefold(): t["key"] for t in oldrows.values()}
                oldfp = active.get("fingerprints", {})
                fingerprints = {}
                prefix = f"03_提示词/releases/{revision}/"
                for t in task_rows(plan):
                    stable = {k: v for k, v in t.items() if k not in ("prompt_ref", "episode")}
                    fingerprints[t["key"]] = digest([stable, digest(blobs[t["prompt_ref"][len(prefix):]])])
                    prev = oldrows.get(t["key"])
                    if t["output"].casefold() in oldoutputs and oldoutputs[t["output"].casefold()] != t["key"]:
                        raise ValueError(f"输出已属于其他资源，请使用新输出文件名：{t['output']}")
                    if prev and prev["output"] == t["output"] and oldfp.get(t["key"]) != fingerprints[t["key"]]:
                        raise ValueError(f"{t['key']} 内容改变，须使用新输出文件名")
                snap_rel = f"07_检查与记录/accepted/{revision}-{digest(ready)[:12]}"
                snap = inside(pj.root, snap_rel)
                snap.mkdir(parents=True, exist_ok=True)
                for rel, data in blobs.items():
                    dest = inside(str(snap), rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                frozen = copy.deepcopy(plan)
                for kind in KINDS:
                    for t in frozen[kind + "_tasks"]:
                        t["prompt_ref"] = snap_rel + "/" + t["prompt_ref"][len(prefix):]
                write_json(str(snap / "tasks.json"), frozen)
                active = {"revision": revision, "tasks_path": snap_rel + "/tasks.json", "fingerprints": fingerprints,
                          "activated_at": time.time(), "ready_hash": digest(ready)}
                write_json(pj.p("07_检查与记录", "active-plan.json"), active)
            except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
                errors.append({"revision": revision, "message": str(exc)})
        return dict(active, legacy=False, errors=errors)


def selections(pj: Project, rows: list) -> dict:
    path = pj.p("07_检查与记录", "selections.json")
    with LOCK:
        state = read_json(path, {}) or {}
        for r in rows:
            state.setdefault(r["key"], True)
        if rows:
            write_json(path, state)
        return state


def select(pj: Project, changes: dict):
    valid = {t["key"] for t in task_rows(pj.tasks(), legacy=True)}
    with LOCK:
        state = selections(pj, task_rows(pj.tasks(), legacy=True))
        for k, v in changes.items():
            if k not in valid or not isinstance(v, bool):
                raise ValueError("勾选项已不在当前计划中，请刷新")
            state[k] = v
        write_json(pj.p("07_检查与记录", "selections.json"), state)


def view(pj: Project) -> dict:
    publication = scan(pj)
    plan = pj.tasks()
    rows = task_rows(plan, legacy=True)
    chosen = selections(pj, rows)
    by_output = {t["output"]: t["key"] for t in rows}
    for t in rows:
        t["selected"] = chosen.get(t["key"], True)
        t["done"] = probe.have_output(str(inside(pj.root, t["output"])))
        t["dependencies"] = list(dict.fromkeys(by_output[r["file_ref"]] for r in references(t) if r.get("file_ref") in by_output))
    done = {t["key"] for t in rows if t["done"]}
    for t in rows:
        t["missing_selection"] = [k for k in t["dependencies"] if k not in done and not chosen.get(k)] if not t["done"] else []
    resources = plan.get("resources") or [
        {"id": t["key"], "name": t.get("name") or t["key"], "semantic":
         next((k for k in SEMANTICS if "_" + k + "_" in t["key"]),
              {"asset": "CHAR", "video": "VIDEO", "board": "ABC", "region": "REGION"}.get(t["kind"], "SCSTATE")),
         "parent_id": None, "episodes": [t["episode"]] if t.get("episode") else [], "task_key": t["key"]} for t in rows]
    return {"root": pj.root, "meta": pj.meta(), "publication": publication, "tasks": rows,
            "resources": resources, "production": read_json(pj.p("07_检查与记录", "production-settings.json"), {}),
            "template": read_text(pj.p("00_项目说明", "AGENT_TASK.md")) if pj.meta().get("workflow") == "agent" else ""}


def revision_request(pj: Project, keys: list, note: str) -> dict:
    current = view(pj)
    tasks = [t for t in current["tasks"] if t["key"] in keys]
    if not tasks or not note.strip():
        raise ValueError("选择需修订的任务并填写修订原因")
    rel = "07_检查与记录/agent-revisions/" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".md"
    text = f"# Agent 定向修订\n\n项目根：`{pj.root}`\n当前发布：{current['publication'].get('revision', 'legacy')}\n\n原因：{note}\n\n"
    text += "仅修订下列任务及受影响下游，按原契约发布新版本；不要覆盖旧发布或成品。\n\n"
    text += "\n".join(f"- {t['key']}：{t['prompt_ref']}" for t in tasks)
    write_text(str(inside(pj.root, rel)), text)
    return {"rel": rel, "text": text}
