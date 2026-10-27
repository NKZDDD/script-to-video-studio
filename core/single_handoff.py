"""Explicit C -> independent A/B contract; shared by JSONL and Agent releases."""
from __future__ import annotations

import threading
_SOURCE_LOCK = threading.Lock()

MODE = "c_then_a_b"
DIRECTORY = "04_交接图"
MARKER = "<!-- studio-single-handoff-v1 -->"
RULES = """<!-- studio-single-handoff-v1 -->
## 独立单图交接：同版 C → 独立 A、独立 B

新建 Liam-2 交接使用 c_then_a_b；原 ABC 三区整板、independent_c 独立 C → AB 双联板继续兼容，已有任务不自动迁移。本节仅扩展交接映射，不改变项目编号、剧情、七段式视频提示词和 Image N 规则。

- JSONL 仍为 kind=image，C/A/B 三张图各写 handoff_mode="c_then_a_b"、view="C"/"A"/"B"。A/B 另写 boundary（同一交接边界 ID）、c_key（同版 C 的任务 key），并在 reference_images 真实引用该 C。C 的 key/filename 必须包含实际版本，换版使用新 key/filename。
- 这是 Studio 外部契约扩展；Liam 的内部 audit_abc 不直接作为生产任务导入。内部 scene_c / handoff_view 及 view、boundary、c_key 按上述字段映射；不能只更改提示词或伪装 region/board 来通过验收。
- tasks.json 增加可选 handoff_tasks 数组，资源 semantic=HANDOFF，输出在 04_交接图/C、A、B 下；旧五个数组保留。C 不是 SCSTATE，不为每个 C 强制增建状态图。
- C/A/B 均为独立单幅图，不填 region/board/parent。一处 boundary 具有 A、B 各一张且 c_key 相同。可先仅发布 C；包含 A/B 时须同时登记配对任务。C 可供多个有依据的边界复用。
- 同一交接边界的 A/B/C 表达同一时刻现场的不同取景；一段视频引用的入站 B/C 与出站 A 可以属于不同边界，不能把三个 role 一概解释成不同的时刻。
- A/B 分别直接引用唯一同版 C，可以追加必要身份/道具资产；不直接或间接引用 A/B、旧整板/区域或另一张竞争 C。C 不能依赖下游 A/B 或旧整板/区域。先联合确定 A/B 提示词，执行时 C 完成后 A/B 可并行，不按 A 的生成结果改 B。
- 视频 handoff_refs：OUT_A 引 A，IN_B 引 B，IN_C 引 C，不填 board。IN_B/IN_C 同时存在时必须指向同版 C；OUT_A 可以属于另一出站边界。各 role 最多一张，不重复塞入 reference_images。实际上传顺序仍按 image_n 连续编号。
- C/A/B 使用项目视频比例，像素按所选模型支持范围设置，不沿用旧双联/三联容器尺寸。各镜真实机位、人物状态、物体承托和同刻关系由已审材料定义；程序校验依赖和来源，不冒充图像或三维几何审核。
- 材料阶段允许图片尚未生成。执行 A/B 时保留真实 C 文件摘要和版本来源；同 key 的 C 被替换时应换版并重新发布相关依赖，不能静默混用。
"""


def is_single(row):
    return row.get("handoff_mode") == MODE


def audit(nodes, ratio=""):
    """Input nodes use key/kind/refs(key list)/handoff(key,role,optional board)."""
    by_key = {n["key"]: n for n in nodes}
    singles = [n for n in nodes if is_single(n)]
    if not singles:
        return []
    out, boundaries = [], {}

    def fail(n, code, msg):
        out.append({"level": "error", "code": code, "msg": f"{n['key']}：{msg}"})

    def ancestors(n):
        seen, pending = set(), list(n.get("refs", []))
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            pending.extend(by_key.get(key, {}).get("refs", []))
        return [by_key[k] for k in seen if k in by_key]

    for n in singles:
        view = n.get("view")
        if n["kind"] != "handoff" or view not in ("A", "B", "C") or any(n.get(k) for k in ("region", "board", "parent")):
            fail(n, "SINGLE_HANDOFF_SHAPE", "单图交接须为 handoff 图片，view 为 C/A/B，不填 region/board/parent")
        from . import sizes
        size = (n.get("params") or {}).get("size") or n.get("ratio") or ratio
        parsed = sizes.parse(size)
        target = sizes.aspect(ratio)
        if target and (not parsed or parsed[0] == "tier" or abs(sizes.aspect(size) / target - 1) > .02):
            fail(n, "SINGLE_ASPECT", "独立 C/A/B 应采用项目视频比例，不使用旧整板容器尺寸或仅写清晰度档位")
        upstream = ancestors(n)
        if any(a.get("kind") in ("board", "region", "video") or (is_single(a) and a.get("view") in ("A", "B")) for a in upstream):
            fail(n, "SINGLE_HANDOFF_DOWNSTREAM", "C/A/B 不能直接或间接依赖 A/B、视频或旧整板/区域")
        if view == "C":
            if n.get("c_key") or any(is_single(a) or a.get("handoff_mode") == "independent_c" or a.get("handoff_type") == "scene_c" for a in upstream):
                fail(n, "SINGLE_C_SOURCE", "C 不能再引用另一张交接 C 或自身，不填 c_key")
            continue
        c = by_key.get(n.get("c_key"), {})
        direct = [k for k in n.get("refs", []) if is_single(by_key.get(k, {}))]
        if not is_single(c) or c.get("view") != "C" or direct != [n.get("c_key")]:
            fail(n, "SINGLE_C_SOURCE", "A/B 必须实际且仅引用 c_key 指定的同版 C；c_key 不能代替 reference_images")
        if any((is_single(a) and a.get("view") == "C" and a["key"] != n.get("c_key")) or a.get("handoff_mode") == "independent_c" or a.get("handoff_type") == "scene_c" for a in upstream):
            fail(n, "SINGLE_C_SOURCE", "A/B 依赖链中存在另一张竞争 C")
        boundary = n.get("boundary")
        if not isinstance(boundary, str) or not boundary.strip():
            fail(n, "SINGLE_BOUNDARY", "A/B 必须声明同一 boundary")
        else:
            boundaries.setdefault(boundary, []).append(n)
    for rows in boundaries.values():
        if sorted(str(n.get("view")) for n in rows) != ["A", "B"] or len({n.get("c_key") for n in rows}) != 1:
            fail(rows[0], "SINGLE_PAIR", "同一 boundary 须有独立 A/B 各一张，且引用同版 C")
    for n in nodes:
        if n.get("kind") != "video":
            continue
        hs = n.get("handoff", [])
        hs_keys = {h["key"] for h in hs}
        for key in n.get("refs", []):
            if is_single(by_key.get(key, {})) and (key not in hs_keys or n["refs"].count(key) != 1):
                fail(n, "SINGLE_VIDEO_ROLE", "单图交接须通过 handoff_refs 引用一次，不放入普通 reference_images")
        for h in hs:
            dep = by_key.get(h["key"], {})
            if is_single(dep) and (h.get("role") != {"A": "OUT_A", "B": "IN_B", "C": "IN_C"}.get(dep.get("view")) or h.get("board")):
                fail(n, "SINGLE_VIDEO_ROLE", "单图交接 role 须对应 OUT_A/IN_B/IN_C，不填 board")
        ins = {h.get("role"): by_key.get(h["key"], {}) for h in hs if h.get("role") in ("IN_B", "IN_C")}
        if len(ins) == 2 and any(is_single(d) for d in ins.values()):
            b, c = ins["IN_B"], ins["IN_C"]
            if not all(is_single(d) for d in (b, c)) or b.get("c_key") != c.get("key"):
                fail(n, "SINGLE_VIDEO_SOURCE", "视频 IN_B 和 IN_C 必须来自同版 C，不混用旧整板来源")
    return out


def freeze_source(pj, task, srcs):
    """Snapshot actual C bytes before upload, with a persistent version/hash guard.

    Hashing/network reads happen outside the shared state lock. The returned local
    snapshot is what the adapter uploads, so changes to the original cannot mix
    two versions between hash collection and request submission.
    """
    if not is_single(task) or task.get("view") not in ("A", "B"):
        return srcs, {}
    import hashlib
    from .store import read_json, write_json
    from .agent_projects import inside, task_rows

    # Resource IDs in Agent packages need not equal task keys. Resolve the
    # declared task output rather than assuming an ID naming convention.
    c_output = next((t["output"] for t in task_rows(pj.tasks(), legacy=True) if t["key"] == task.get("c_key")), "")
    matches = [(i, r, src) for i, (r, src) in enumerate(srcs)
               if (src == c_output if c_output else r.get("asset_id") == task.get("c_key"))]
    if len(matches) != 1:
        raise ValueError("独立 A/B 缺少唯一的同版 C 参考，请重新验收材料")
    index, ref, source = matches[0]
    path = inside(pj.root, source)
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    identity = hashlib.sha256((task["c_key"] + "\n" + source).encode()).hexdigest()
    relative = f"07_检查与记录/handoff-sources/{sha}{path.suffix}"
    snapshot = inside(pj.root, relative)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot.exists():
        # Same digest always means identical bytes; write a unique temporary file
        # then replace so a concurrent A/B worker never reads a partial image.
        import os
        import uuid
        temporary = snapshot.with_name(snapshot.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, snapshot)
        finally:
            temporary.unlink(missing_ok=True)
    record = str(snapshot.parent / (identity + ".json"))
    evidence = {"c_key": task["c_key"], "file_ref": source, "sha256": sha, "snapshot": relative}
    with _SOURCE_LOCK:
        previous = read_json(record, {}) or {}
        if previous and previous != evidence:
            raise ValueError("同 key 的 C 文件已改变，不能混用不同版本；请为 C 换新 key/文件名并重新发布相关 A/B 和视频")
        if not previous:
            write_json(record, evidence)
    actual = list(srcs)
    actual[index] = (ref, relative)
    return actual, {"handoff_source": evidence}
