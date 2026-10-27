"""Explicit independent-C handoff extension shared by both material readers."""

from . import single_handoff

MODE = "independent_c"
MARKER = "<!-- studio-independent-c-v2 -->"
RULES = """<!-- studio-independent-c-v2 -->
## 可选交接方式：独立 C → AB 双联板

仅在用户选择该方式时使用；原 ABC 三区整板流程继续有效。本节是 Studio 已支持的程序契约扩展：独立 C 不受旧版“C 必须派生自 ABC、B/C 必须同一整板”条款限制，其他创作和项目锁定要求不变。

- 独立 C 图片增加 `handoff_mode: "independent_c"`，不填 `region`、`board` 或 `parent`。不靠文件名或提示词猜它的身份。它可引用人物、场景和状态资产，但不能反向引用整板/区域图。
- 兼容旧材料的 `handoff_type: "scene_c"` 或 `handoff_type: "independent_c"`；未填 handoff_mode 时按独立 C 识别。新材料优先写 handoff_mode。显式填写的 handoff_mode 优先，非法值仍报错；ab_board、ab_panel 不作为独立 C。
- JSONL 仍写 `kind: "image"`，独立 C 落入 `03b_场景状态图/`；发布包放入 `scstate_tasks`，资源 semantic 为 SCSTATE。不是从 AB 裁出的区域 C。
- AB 双联整板仍用原整板字段，JSONL key 仍属 ABC 家族，发布包放入 `board_tasks`。其 `reference_images` 必须真实引用一张已声明的独立 C，可另带必要身份等参考；一次生成 A/B 双联板。
- A/B 区域图沿用 `region: "A"/"B"` 与 `board`，各自唯一图片参考为同一张 AB 整板。引用独立 C 的 AB 板不再派生区域 C。
- 视频 `handoff_refs` 的 `OUT_A`、`IN_B` 沿用原区域图规则；`IN_C` 可直接引用独立 C，不要求填写 board。若同时有 IN_B，IN_B 所属 AB 板必须直接引用这张独立 C；若 IN_C 额外填写 board，也必须指向引用它的同一 AB 板。OUT_A 可属于另一个出站边界。
- 三个 role 各自最多一条；独立 C 通过 IN_C 进入视频，不另塞入 reference_images 重复上传。整板仍不得直接喂给视频。
- Image 编号按真实上传顺序连续，提示词映射仍逐图说明身份/用途。单文件 JSONL、现有字段含义、项目秒数、比例和其他制作选项保持原约定。

旧项目采用扩展时须重新发布/导入材料。修改提示词正文不能代替结构化标记和引用；旧 ABC 任务无需批量迁移。
"""


def mode(row):
    """Resolve explicit legacy aliases without hiding an invalid canonical mode."""
    explicit = str(row.get("handoff_mode") or "").strip()
    if explicit:
        return explicit
    return MODE if row.get("handoff_type") in ("scene_c", MODE) else ""


def is_independent(row):
    return mode(row) == MODE


def audit(nodes):
    """nodes: key/kind/refs(key list)/handoff(key,role,optional board) plus fields."""
    if not any(mode(n) for n in nodes):
        return []  # Original ABC contract remains with the existing readers.
    by_key = {n["key"]: n for n in nodes}
    out = []
    def fail(n, code, text):
        out.append({"level": "error", "code": code, "msg": f"{n['key']}：{text}"})
    for n in nodes:
        if mode(n) not in ("", MODE, single_handoff.MODE):
            fail(n, "HANDOFF_MODE_UNKNOWN", "handoff_mode 只支持 independent_c / c_then_a_b；原 ABC 流程不填此字段")
        if is_independent(n):
            if n["kind"] != "scstate" or n.get("region") or n.get("board"):
                fail(n, "INDEPENDENT_C_SHAPE", "独立 C 必须是场景状态图片任务，不填 region/board/parent")
            if any(by_key.get(k, {}).get("kind") in ("board", "region") for k in n["refs"]):
                fail(n, "INDEPENDENT_C_DOWNSTREAM", "独立 C 不能反向引用整板或区域图")
    ab = {}
    for n in nodes:
        if n["kind"] == "board":
            cs = [k for k in n["refs"] if is_independent(by_key.get(k, {}))]
            if cs:
                ab[n["key"]] = cs
                if len(cs) != 1:
                    fail(n, "AB_C_COUNT", "AB 整板必须且只引用一张独立 C")
                if any(by_key.get(k, {}).get("kind") == "region" for k in n["refs"]):
                    fail(n, "AB_USES_REGION", "AB 整板不能反向引用区域图")
    for n in nodes:
        if n["kind"] == "region" and (n.get("board") in ab or any(k in ab for k in n["refs"])):
            if n.get("region") not in ("A", "B"):
                fail(n, "AB_REGION_C", "AB 双联板只派生 A/B；IN_C 应引用其上游独立 C")
            if n["refs"] != [n.get("board")]:
                fail(n, "AB_REGION_SOURCE", "A/B 区域图必须且仅引用 board 指定的同一张 AB 整板")
        if n["kind"] != "video":
            continue
        hs = n.get("handoff", [])
        roles = [h.get("role") for h in hs if h.get("role")]
        if any(roles.count(role) > 1 for role in ("IN_B", "IN_C", "OUT_A")):
            fail(n, "HANDOFF_ROLE_DUP", "IN_B/IN_C/OUT_A 各自最多一条")
        named = {h["key"] for h in hs}
        if any(is_independent(by_key.get(k, {})) and k not in named for k in n["refs"]):
            fail(n, "INDEPENDENT_C_ROLE", "视频的独立 C 必须通过 handoff_refs 的 IN_C 引用")
        if any(is_independent(by_key.get(k, {})) and n["refs"].count(k) > 1 for k in named):
            fail(n, "INDEPENDENT_C_DUP", "独立 C 只能上传一次，不要同时放在 handoff_refs 和 reference_images")
        incoming_cs = [h["key"] for h in hs if h.get("role") == "IN_C"]
        incoming_abs = [by_key.get(h["key"], {}).get("board") for h in hs if h.get("role") == "IN_B"]
        if any(ab[b] != [c] for b in incoming_abs if b in ab for c in incoming_cs):
            fail(n, "INDEPENDENT_C_SOURCE", "IN_B 来自 AB 双联板时，IN_C 必须引用该板实际使用的独立 C，不能混用其他板的区域 C")
        for h in hs:
            dep = by_key.get(h["key"], {})
            if single_handoff.is_single(dep):
                continue  # Validated by the single-image contract, including incoming source matching.
            if not is_independent(dep):
                if (any(is_independent(by_key.get(r["key"], {})) for r in hs)
                        or dep.get("board") in ab):
                    role = h.get("role")
                    if dep.get("kind") != "region" or role not in ("IN_B", "IN_C", "OUT_A") or dep.get("region") != role[-1] or (h.get("board") and h["board"] != dep.get("board")):
                        fail(n, "HANDOFF_REGION_SOURCE", "A/B 交接须引用相应区域，引用项 board 如填写须与区域图来源相同")
                continue
            if h.get("role") != "IN_C":
                fail(n, "INDEPENDENT_C_ROLE", "独立 C 只能用作 IN_C，不能替代 A/B 区域图")
            boards = [by_key.get(b["key"], {}).get("board") for b in hs if b.get("role") == "IN_B"]
            if h.get("board"):
                boards.append(h["board"])
            if any(ab.get(b) != [h["key"]] for b in boards) or len(set(boards)) > 1:
                fail(n, "INDEPENDENT_C_SOURCE", "IN_B 所属 AB 整板必须直接引用同一张独立 C；IN_C 的 board 如填写也须一致")
    return out
