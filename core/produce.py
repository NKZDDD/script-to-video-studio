# -*- coding: utf-8 -*-
"""出图出片的执行层 —— **不认哪一套生产体系**。

这里只有「拿到一条任务，怎么把它做出来」：参考图变成能发出去的形式、
按依赖分层派单、调服务商、量一下出来的东西对不对、把手改过的提示词护住。
任务长什么样由体系层（core/stages.py）装配，这一层只消费固定的几个字段：

    key / prompt_ref / reference_images[] / params / output

所以换一套生产体系（环节表、资产分类、提示词模板全变）时，这个文件不用动。
之所以要单独放：这里是踩坑最密集的地方（参考图形式声明错会静默丢图、
缺图不能凑合出、比例不对接口不报错），修复必须能干净地同步到别的体系分支上。
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional

from . import accounts, diagnose, ledger, probe, soften, uploader
from .apiutil import TASK_FATAL, ApiError, resolve_ref
from .providers import ImageTask, VideoTask, build as build_provider
from .store import Project, read_text, write_text


def write_prompt_txt(pj: Project, rel: str, text: str, log=None) -> None:
    """环节5/8 落盘一份提示词 txt。

    走这里而不是直接 write_text，是为了在覆盖**手改过**的那份之前先备份 +
    说一声。人在页面上改好一条、隔天重跑一次环节8 就被悄悄盖掉 ——
    这种事不报出来，等出图不对劲再回头找，原文已经没了。
    """
    from . import promptfile
    try:
        promptfile.guard_overwrite(pj, rel, text, log)
    except Exception:                                   # noqa: BLE001
        pass                                            # 备份失败不该挡住主流程
    write_text(pj.p(*rel.split("/")), text)


def _ordered_asset_refs(*groups, exclude: str = "") -> list:
    """按首次出现顺序合并资产 ID。"""
    out, seen = [], set()
    for group in groups:
        if isinstance(group, str):
            group = [group]
        for raw in (group or []):
            rid = str(raw or "").strip()
            if not rid or rid == exclude or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
    return out


class AssetDependencyCycleError(RuntimeError):
    """资产参考图形成闭环，任何一个成员都不可能成为第一张。"""


def asset_dependency_cycles(items: list) -> list:
    """返回资产/任务依赖图里的强连通分量；每一组都是一个真实循环。

    同时接受环节4的资产结构（asset_id/reference_assets）和 tasks.json 的任务结构
    （key/reference_images），便于分析阶段与生产阶段使用同一套判断。
    """
    by_key = {}
    order = {}
    for item in items or []:
        key = str(item.get("key") or item.get("asset_id") or "").strip()
        if key and key not in by_key:
            order[key] = len(order)
            by_key[key] = item
    graph = {}
    for key, item in by_key.items():
        if "key" in item:
            refs = [r.get("asset_id") for r in (item.get("reference_images") or [])]
        else:
            refs = _ordered_asset_refs([item.get("parent_asset_id")],
                                       item.get("reference_assets") or [])
        graph[key] = [str(r) for r in refs if str(r) in by_key]

    index = 0
    stack, on_stack = [], set()
    indexes, lowlinks, cycles = {}, {}, []

    def visit(node):
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for dep in graph[node]:
            if dep not in indexes:
                visit(dep)
                lowlinks[node] = min(lowlinks[node], lowlinks[dep])
            elif dep in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[dep])
        if lowlinks[node] != indexes[node]:
            return
        component = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or (component and component[0] in graph[component[0]]):
            cycles.append(sorted(component, key=lambda x: order[x]))

    for node in graph:
        if node not in indexes:
            visit(node)
    return sorted(cycles, key=lambda c: min(order[x] for x in c))


def asset_deps(tasks: list) -> dict:
    """每条资产任务在**这一批里**要等的那几条 → `{key: [key, ...]}`。

    这是分层的替代品。分层是「层内并发、层间串行」——正确但是慢：一层里有一条
    慢的（4K 出图、被审核挡了要改写重试几轮），**整层都在等它**，
    后面那些参考图早就齐了的也干等着。

    改成按条记依赖之后，执行器就绪即派：一条的参考图全好了它立刻开跑，
    等的只是自己真正依赖的那几条。产出和分层完全一样，只是不白等。

    成环照旧在派任务前就报出来 —— 换了调度方式不代表环能跑了。
    环的表现会变成「谁都不就绪」，那种「卡住」比报错难查得多。

    批外的不算依赖：已经出好的（不在 tasks 里）本来就不用等；
    判定不出图的在装配那一层已经挑掉了（见 run_v34.split_refs）。
    """
    cycles = asset_dependency_cycles(tasks)
    if cycles:
        detail = "；".join(" ↔ ".join(group) for group in cycles)
        raise AssetDependencyCycleError(
            f"资产循环依赖：{detail}。这些资产互相把对方当参考图，没有任何一个能先生产。"
            "请回到环节4调整 dependency_order/reference_assets：同级资产不得互相引用，"
            "每条引用只能指向更早完成的基础资产或状态资产。")
    keys = {t["key"] for t in tasks}
    out = {}
    for t in tasks:
        out[t["key"]] = sorted({
            str(r.get("asset_id") or "")
            for r in (t.get("reference_images") or [])
            if str(r.get("asset_id") or "") in keys
            and r.get("asset_id") != t["key"]})
    return out


def asset_layers(tasks: list) -> list:
    """把资产任务按参考图依赖分层：第 0 层没有参考图，第 N 层的参考图都在前面几层。

    为什么必须分层：状态资产 ST007 的父资产或来源图是 ST006，而任务是按环节4 的输出顺序
    排的、并发跑的。ST006 还在生成时 ST007 就被派出去，读不到 ST006.png 就直接
    失败（实测：ST007 重试两次全报「参考图文件不存在」）。
    分层之后层内并发、层间串行，来源资产必然先出完，竞态从根上没了。

    成环时必须在派任务前立即失败并报出成员。把循环组塞进同一层并发并没有消除依赖，
    只会让它们同时读取尚不存在的参考图，看起来像互相等待。
    """
    cycles = asset_dependency_cycles(tasks)
    if cycles:
        detail = "；".join(" ↔ ".join(group) for group in cycles)
        raise AssetDependencyCycleError(
            f"资产循环依赖：{detail}。这些资产互相把对方当参考图，没有任何一个能先生产。"
            "请回到环节4调整 dependency_order/reference_assets：同级资产不得互相引用，"
            "每条引用只能指向更早完成的基础资产或状态资产。")
    by_key = {t["key"]: t for t in tasks}
    layers, placed = [], set()
    rest = list(tasks)
    while rest:
        cur = [t for t in rest
               if all(r.get("asset_id") in placed or r.get("asset_id") not in by_key
                      for r in (t.get("reference_images") or []))]
        if not cur:  # 理论上已被上面的强连通分量检查覆盖；保留防御，绝不静默并发。
            blocked = "、".join(t["key"] for t in rest)
            raise AssetDependencyCycleError(f"资产依赖无法继续分层：{blocked}")
        layers.append(cur)
        placed.update(t["key"] for t in cur)
        rest = [t for t in rest if t["key"] not in placed]
    return layers


def make_ref_resolver(pj: Project, prov, provider_cfg: dict, model: str,
                      ref_side: int, media: str = "image") -> Callable:
    """把参考图引用变成能发出去的形式。

    配了对象存储 → 一律传上去换公网链接。不只是为了那些只收 URL 的接口：
    能吃 data URI 的家，请求体也从几 MB 的 base64 缩成一行链接，快且稳。
    没配 → 转 data URI；碰上只收 URL 的模型就明确报错说去哪配。

    **例外必须按服务商的声明来，不能一刀切上传**（这是踩过的坑）：
      · needs_bytes 的家（multipart）→ 给本机路径
      · accepts_url 为假的家（把参考图内联进某字段、只认裸 base64）→ 给 data URI
    给错形式的后果不是报错，是**参考图被丢掉照样出图** —— 状态资产没了父资产或来源
    参考，脸就不是本人，而且任务标 ok 没人知道。
    """
    up = provider_cfg.get("upload") or {}
    configured = uploader.configured(up) and up.get("mode", "always") != "when_required"
    need_url = prov.needs_url(model, media)
    need_bytes = prov.needs_bytes(model)
    can_url = prov.accepts_url(model, media)
    use_url = need_url or (configured and can_url)

    def resolve(src: str, log: Callable = print) -> str:
        src = (src or "").strip()
        if not src or src.startswith("http"):
            return src
        # **先验这张图是不是真的图，再决定怎么送。**
        # 三条分支（本机路径 / data URI / 传对象存储）以前各走各的，
        # 而 0 字节的文件在每一条上都过得去：传上去是个空对象、
        # 转 data URI 是段空数据 —— 服务商收到的是「有参考图」，
        # 实际什么都没有。出来的脸不是本人，任务标 ok，只能靠肉眼在几百张里发现。
        try:
            path0 = src if os.path.isabs(src) else os.path.join(pj.root, src)
            if not probe.have_output(path0):
                # 在 try 里抛：下面那个 except 会去查这张图**为什么**不在
                # （多半是它自己那一条失败了），把真正的原因接上来。
                raise ApiError(
                    f"参考图不存在或者是个空文件：{src}。"
                    f"少一张参考图出来的就不是同一个人，所以这一条不出。")
            if need_bytes:
                # 本机绝对路径，provider 自己读字节塞 multipart
                return src if os.path.isabs(src) else os.path.join(pj.root, src)
            if not use_url:
                return resolve_ref(src, pj.root, max_side=ref_side)
            path = src if os.path.isabs(src) else os.path.join(pj.root, src)
            return uploader.to_url(path, up, project_root=pj.root,
                                   max_side=ref_side, log=log)
        except ApiError as exc:
            raise _why_ref_missing(pj, src, exc) from exc

    return resolve


def _why_ref_missing(pj: Project, src: str, exc: ApiError) -> ApiError:
    """参考图不见了 —— 多半是它**自己那张图没出成**，而不是文件被谁删了。

    实跑：ST001 因为「图片额度用完」没出来，紧接着 ST002 报
    「参考图文件不存在: …/ST001.png」。照着这句话去查，只会去翻硬盘 ——
    而真正要处理的是额度，那条记录就在同一份失败清单里。

    所以这里回头查一眼：这个文件名对应的资产，是不是刚刚失败过。
    查得到就把**它的**失败原因接在后面。
    """
    stem = os.path.splitext(os.path.basename(src))[0]
    if not stem:
        return exc
    # 文件名常带后缀：PH006_R01.png / C001_V02.png —— 而失败记录的 target
    # 是资产号 PH006。只做全等匹配会**永远对不上**，于是这条线索白搭。
    # 实跑撞到：LK006 报「参考图 PH006_R01.png 不存在」，而 PH006 自己
    # 就在同一份清单里失败着，程序却没把两条连起来。
    cands = {stem, re.sub(r"_(R|V)\d+$", "", stem)}
    for d in diagnose.load(pj.root):
        aid = str(d.get("target"))
        if aid not in cands:
            continue
        better = ApiError(
            f"{exc}\n"
            f"—— 这张图之所以不在，是因为 {aid} 自己没出成："
            f"{d.get('title') or d.get('code')}。\n"
            f"要处理的是那一条，不是这一条：{(d.get('raw') or '')[:200]}",
            getattr(exc, "status", 0) or 0, TASK_FATAL)
        better.extra_fix = [f"先把 {aid} 出出来（它的失败原因见上），"
                            f"这一条会跟着好 —— 单独重试这一条没用"]
        return better
    return exc


# 提示词里 `Image 1 = C001 名称` 这种映射行。全角冒号/等号都认。
_IMAGE_MAP = re.compile(r"[Ii]mage\s*(\d+)\s*[=＝:：]\s*([A-Za-z0-9_\-]+)")

# V5.6 要求每个 Image 槽位写全六字段（第一个是 `Image N = ID`，剩下五个在这）。
# 只写「控制什么/不控制什么」是不够的 —— 模型知道这张图有权决定哪些维度，
# 却不知道**这张图是谁**。实跑时就撞过：提示词写了 Image 1 的控制范围，
# 没说 Image 1 是哪个人，模型把另一个角色的脸套了上去。
#
# 标签同时认 V5.6 的英文原文和中文写法：模板是中文写的，但用户可能
# 直接从 skill 文档里粘英文段落过来。认死一种会把对的判成错的。
_MAP_FIELDS = (
    ("who", "这张图是谁/是什么 + 画面可见内容",
     r"(?:Who\s*/?\s*What[^\n:：]*|是谁[^\n:：]*|身份与可见内容|可见内容)"),
    ("state", "故事时间 / 当前状态",
     r"(?:Story\s*Time[^\n:：]*|故事时间[^\n:：]*|时间与当前状态|当前状态)"),
    # 这两项同时认 V5.6 的 Controls / Does Not Control 和我们原有的
    # MUST PRESERVE / MUST NOT COPY —— 后者是前者的更细一层拆分，
    # 表达的是同一件事。只认新写法会把已经写对的提示词判成错的。
    ("controls", "有权控制的维度",
     r"(?:Controls|控制的维度|有权控制|MUST\s+PRESERVE)"),
    ("not_controls", "无权控制的维度",
     r"(?:Does\s*Not\s*Control|不控制|无权控制|MUST\s+NOT\s+COPY"
     r"|DOES\s+NOT\s+CONTROL)"),
    ("scope", "适用范围", r"(?:Applicable\s*Scope|适用范围)"),
)
_MAP_FIELD_RE = {
    key: re.compile(rf"^[\s\-·*]*{pat}\s*[:：]\s*(\S.*)$", re.M | re.I)
    for key, _label, pat in _MAP_FIELDS
}


def _image_sections(prompt: str) -> dict:
    """按 `Image N =` 把提示词切成每张图各自的一段。

    切段是必须的：五个字段名在整篇里各出现一次也能匹配上，
    但那可能全都挂在 Image 1 下面，Image 2 一个字段都没有。
    不切段的校验会把「只写了第一张」判成合格。
    """
    hits = list(_IMAGE_MAP.finditer(prompt or ""))
    out = {}
    for i, m in enumerate(hits):
        n = int(m.group(1))
        end = hits[i + 1].start() if i + 1 < len(hits) else len(prompt)
        out.setdefault(n, prompt[m.start():end])
    return out


_HAS_CONTROL = re.compile(
    r"身份绑定|严格继承|继承|有权控制|Controls|MUST\s+PRESERVE", re.I)
_HAS_DENY = re.compile(
    r"禁止改变|不照搬|不得照搬|不许照搬|无权控制|不控制"
    r"|Does\s*Not\s*Control|MUST\s+NOT\s+COPY", re.I)


def _who_is_named(sec: str, aid: str) -> bool:
    """这张图「是谁/是什么」说清了没有。分行和行内两种写法都算。

      分行  是谁/是什么 + 画面可见内容：成年男性正面半身…
      行内  Image 1=PH001 Isabel身份；Image 2=COST001女款礼服

    行内那种是模型被要求压缩之后的自然产物，语义上是完整的。
    只认分行的话会把「其实说清了」判成「没说清」，然后拦住整批生产 ——
    实测 41 条全被拦，而它们大体是对的。
    """
    if _MAP_FIELD_RE["who"].search(sec):
        return True
    m = re.search(r"[Ii]mage\s*\d+\s*[=＝:：]\s*" + re.escape(aid)
                  + r"([^\n；;。]*)", sec)
    return bool(m and len(m.group(1).strip(" \u3000,，、")) >= 2)


def _authority_split(sec: str) -> bool:
    """「能控制什么」和「不能控制什么」两头都说到了没有。"""
    return bool(_HAS_CONTROL.search(sec)) and bool(_HAS_DENY.search(sec))


def check_identity_map(prompt: str, want_refs: list, who: str = "",
                       ref: str = "") -> tuple:
    """V5.6 六字段身份映射。返回 (硬错误, 提醒)。

    **按危害分级，不是缺一项就停。** V5.6 的硬拦条件写的是「语义映射不完整」，
    它点名的风险是身份说不清（「无法消歧时阻断」）。
    「没按六行分开写」不等于「说不清」。

      硬停  任何一张图说不出它是谁       —— skill 点名的那条：无法消歧时阻断
      提醒  身份清楚，但没逐张划分权威   —— 见下
      提醒  缺时间 / 适用范围            —— 全局约束覆盖得住

    **「≥2 张却没划分权威」原来是硬停，那一条是我自己加的，已经降级。**
    skill 的硬拦条件只有一句「无法消歧时阻断」，而这种情况**身份是说清了的**
    （报错自己都写着「只说了是谁、没说各自管什么」）。
    加了它的实际后果：一条 6 张参考图的场景状态提示词把整批生产拦住，
    而它并没有说不清谁是谁 —— 这就是把自己的偏好当成规范去拦人。

    多图不划分权威确实会让模型平均融合，所以照旧报出来，
    但那是「做出来可能不对」，不是「做不出来」——归提醒。
    """
    want = [(r.get("image_n") or i + 1, str(r.get("asset_id") or ""))
            for i, r in enumerate(want_refs)]
    if not want:
        return "", ""
    secs = _image_sections(prompt or "")
    if not secs:
        return "", ""            # 连编号都没有，由 check_image_map 管，别报两遍

    nameless, thin, soft = [], [], []
    for n, aid in want:
        sec = secs.get(n)
        if sec is None:
            continue             # 编号缺失归 check_image_map
        if not _who_is_named(sec, aid):
            nameless.append(f"Image {n}（{aid}）")
            continue
        if len(want) >= 2 and not _authority_split(sec):
            thin.append(f"Image {n}（{aid}）")
        gone = [label for key, label, _ in _MAP_FIELDS
                if key in ("state", "scope")
                and not _MAP_FIELD_RE[key].search(sec)]
        if gone:
            soft.append(f"Image {n}（{aid}）少了 " + "、".join(gone))

    if nameless:
        return ("REFERENCE_MAPPING_BLOCKED　有参考图没说清它是谁。"
                + _whose(who, ref)
                + "说不清的是：" + "；".join(nameless)
                + "。出图模型收到的是几张没有标签的图 —— 只说「这张图控制服饰」"
                  "不够，它不知道这张图是哪个人，多人场景必然张冠李戴（实跑撞过）。"
                  "每个 Image 编号后面要紧跟这张图是谁/是什么，"
                  "比如 `Image 1 = C002 甲，成年男性正面半身`。"), ""
    notes = []
    if thin:
        notes.append(
            f"这 {len(thin)} 张只说了是谁、没说各自管什么："
            + "；".join(thin)
            + "。多图不划分权威时模型容易几张平均融合 —— "
              "逐张写清有权控制和无权控制会稳得多。")
    if soft:
        notes.append("；".join(soft) + "。这几项缺了容易让未来状态提前用上"
                                      "（伤口在受伤前出现），或者一张图的权威被无限扩张。")
    return "", "　".join(notes)

def _whose(who: str, ref: str) -> str:
    """出问题的是哪一条任务、该去改哪个文件。

    不写这一句的代价是真实踩过的：报错只说「Image 1（PS001）缺…」，
    人就去找 PS001 的提示词 —— 而那是**被引用的那张图**，它自己没问题。
    要改的是引用它的那个资产。
    """
    if not who:
        return ""
    return (f"出问题的是 **{who}** 这一条的提示词"
            + (f"（{ref}）" if ref else "")
            + "，不是被它引用的那张图。")


def _auto_fix_image_map(prompt: str, want_refs: list) -> tuple:
    """正文里 Image 映射写错时，以上传列表为准直接改过来。返回 (改后的正文, 改了哪些)。

    能确定唯一正确答案的才改：正文写了 `Image n = X`、而第 n 张实际
    要传的是 aid —— 那一行就改成 `Image n = aid`。X 原本指着谁、为什么
    写错，都不用管：出图模型只认编号，编号和第几张对上就对了。

    **缺编号不补、多写的编号不删** —— 那两种没有唯一正确答案：
    补行补在哪个位置、措辞怎么写没有标准；多写的里面混着「不出图但
    正文保留编号」的约定（no_image_refs），删错一个比留着更糟。
    这两种留给 `check_image_map` 照旧报错拦下。
    """
    want = [(r.get("image_n") or i + 1, str(r.get("asset_id") or ""))
            for i, r in enumerate(want_refs or [])]
    fixes = []
    for n, aid in want:
        m = re.search(rf"[Ii]mage\s*{n}\s*[=＝:：]\s*([A-Za-z0-9_\-]+)",
                      prompt or "")
        if m and m.group(1) != aid:
            prompt = prompt[:m.start(1)] + aid + prompt[m.end(1):]
            fixes.append(f"Image {n} 由 {m.group(1)} 纠正为 {aid}")
    return prompt, fixes


def check_image_map(prompt: str, want_refs: list, who: str = "",
                    ref: str = "", no_image: list = None) -> tuple:
    """核对提示词里的 Image 编号和程序实际上传顺序是否一致。

    出图模型收到的是 N 张**没有标签**的图，它只知道第 1 张、第 2 张。
    它不认识 `C001` 这个编号 —— 所以提示词必须逐张写 `Image 1 = C001 ...`，
    而且编号顺序必须等于上传顺序（reference_images 按 image_n 排的那个顺序）。

    编号写错的后果不是报错，是**每张参考图都被错误归属**：模型拿病房那张图
    去沿用人脸。图照出、任务标 ok，只能靠肉眼在几百张里发现。

    返回 (硬错误, 提醒)。分两级是因为轻重差很远：
      · 写了映射但对不上 → 硬停。已经确定是错的，出图只会浪费钱。
      · 两张以上却没写映射 → 硬停。模型不可能知道哪张是哪张。
      · 只有一张且没写映射 → 只提醒。顺序上不存在歧义，但仍该补。

    `no_image` 是装配那一层按 skill 挑出去的那几张（见 `run_v34.split_refs`）：
    正文里有它的编号、附件里没有它的图，**这是对的**，所以那几个号不算
    「多写了」。但要说出来 —— 不然就成了「声明 3 张只传 2 张」那类静默降级。
    """
    want = [(r.get("image_n") or i + 1, str(r.get("asset_id") or ""))
            for i, r in enumerate(want_refs)]
    skipped = {int(r.get("image_n") or 0): r for r in (no_image or [])
               if isinstance(r, dict) and str(r.get("asset_id") or "")}
    note = ""
    if skipped:
        note = (_whose(who, ref) + "、".join(
            f"Image {n} = {r['asset_id']}"
            f"（{r.get('decision') or '不出图'}"
            + (f"：{r['reason']}" if r.get("reason") else "") + "）"
            for n, r in sorted(skipped.items()))
            + " 按 skill 第七章不出图（只有文字契约），所以没有作为参考图上传；"
              "正文里对它的文字描述照旧生效。")
    if not want:
        return "", note
    got = _IMAGE_MAP.findall(prompt or "")
    if not got:
        head = (_whose(who, ref)
                + "提示词里没有 `Image N = 资产ID` 的参考图映射，"
                + f"但这一条要传 {len(want)} 张参考图（{'、'.join(a for _, a in want)}）。")
        if len(want) >= 2:
            return (head + "两张以上参考图却不说哪张是谁，模型只能猜，"
                    "必然把其中一张的身份用到别处 —— 所以这里停下。"
                    "去「任务明细」改这一条的提示词，逐张写 `Image 1 = 资产ID 名称`，"
                    "顺序和上面括号里一致；或者重跑对应的文字环节。"), note
        return "", head + "只有一张、顺序上没有歧义，先照常出图，但建议补上。" + note

    seen = {}
    for n, aid in got:
        seen.setdefault(int(n), aid)        # 同一编号重复出现时取第一次
    problems = []
    for n, aid in want:
        claim = seen.get(n)
        if claim is None:
            problems.append(f"Image {n} 应该是 {aid}，提示词里没提这个编号")
        elif claim != aid:
            problems.append(f"Image {n} 实际传的是 {aid}，提示词里却写成 {claim}")
    extra = sorted(set(seen) - {n for n, _ in want} - set(skipped))
    if extra:
        problems.append("提示词里多写了 Image "
                        + "、".join(str(n) for n in extra)
                        + f"（这一条只有 {len(want)} 张）")
    if problems:
        return ("参考图编号和实际上传顺序对不上。" + _whose(who, ref)
                + "；".join(problems)
                + f"。实际上传顺序是 {'、'.join(f'Image {n}={a}' for n, a in want)}。"
                  "编号错位等于每张参考图都被错误归属（拿场景图去沿用人脸），"
                  "出来的东西看着正常但全是错的，所以这里停下。"
                  "去「任务明细」按实际顺序改这一条的映射。"), note
    return "", note


def _check_video_ref_map(prompt: str, task: dict, spine: list,
                         aux: list, refs: list) -> None:
    """视频提示词里声明的 `Image N = ID`，每一个都要真的有图会上传。

    出片这一层原来只查一个方向（骨架多张、映射少了 → 提醒）。反方向
    —— 映射比上传多 —— 一个字不说，而那正是装配层悄悄挑掉补图之后
    的长相。两条路对不上时**硬停**，和出图那边的 check_image_map 同一条
    口径：已经确定是错的，出片只会浪费钱。

    认得出「会上传」的依据有三样：
      · 补图的 asset_id（reference_images 里的，精确匹配）
      · 骨架 Sheet：提示词里写 `SBPKG_段号_SHEET_X`，文件基名是
        `项目码_段号_SHEET_X` —— 前缀不同、尾巴相同，按 sheet_id 认
        （一个段内 sheet_id 唯一）
      · no_image_refs 里按 skill 判了不出图的那几张 —— 编号占位是
        设计如此（同出图那边 check_image_map 的口径）
    """
    mapped = _IMAGE_MAP.findall(prompt or "")
    if not mapped:
        return                      # V6.1 不写映射；没有映射归上面那道提醒管
    sent_aux = {str(r.get("asset_id") or "") for r in aux or []}
    skipped = {str(r.get("asset_id") or "")
               for r in (task.get("no_image_refs") or [])
               if isinstance(r, dict)}
    stems = {os.path.splitext(os.path.basename(s))[0] for s in spine or []}
    sheet_ids = [str(s.get("sheet_id") or "")
                 for s in (task.get("storyboard_refs") or [])]

    def _sent(aid: str) -> bool:
        if aid in sent_aux or aid in skipped:
            return True
        if any(stem == aid or (len(aid) > 6 and stem.endswith(aid))
               or aid.endswith(stem) for stem in stems if stem):
            return True
        return any(sid and len(sid) >= 5 and aid.endswith(sid)
                   for sid in sheet_ids)

    ghost = [f"Image {n} = {aid}" for n, aid in mapped if not _sent(aid)]
    if not ghost:
        return
    raise RuntimeError(
        f"{task.get('key') or '这一段'} 的视频提示词声明的参考图没有全部上传："
        f"{len(mapped)} 个编号里有 {len(ghost)} 个指不到任何会上传的图 —— "
        f"{'、'.join(ghost[:6])}{'…' if len(ghost) > 6 else ''}。"
        f"这一段实际只会传 {len(refs)} 张。"
        f"多半是第十三环节把装配层会挑掉的东西写进了 reference_order"
        f"（资产表里认不出的 ID、故事板没有的 Sheet），"
        f"也可能是故事板那一环这一段没出够张数。"
        f"模型收到的图比声明少，编号从缺的那张起整体错位 —— "
        f"片子出来看着正常，参考全是错的，所以这里停下。"
        f"去重跑第十三环节这一段（reference_order 只写真实存在的图），"
        f"缺故事板就先把这一段的 Sheet 数补对。")


def _ratio_warn(pj: Project, path: str, want: str, stage: str, key: str,
                provider_cfg: dict, model: str, media: str):
    """出完东西量一下真实尺寸，比例不对就挂一条提醒。

    这类问题接口不会报错——200、文件也正常下载，只是画面躺倒了。
    不主动量，就得等人工验收才发现，那时候钱早花完了。
    量不到（缺 ffprobe、格式不认识）就当没这回事，不能反过来卡住主流程。
    """
    try:
        bad = probe.check(path, want, kind=media)
    except Exception:                                  # noqa: BLE001
        return None
    if not bad:
        return None
    flip = ("你要的是竖屏，出来的是横屏。"
            if bad["portrait_wanted"] and not bad["portrait_got"] else
            "你要的是横屏，出来的是竖屏。"
            if not bad["portrait_wanted"] and bad["portrait_got"] else "")
    pj.log_event({"stage": stage, "id": key, "result": "ratio_mismatch", **bad})
    return diagnose.warn(
        "WRONG_RATIO",
        f"要的是 {bad['want']}，实际出来 {bad['got']}（约 {bad['got_ratio']}）。{flip}",
        stage=stage, target=key, provider=provider_cfg.get("provider", ""), model=model,
        extra_fix=[f"这个文件在：{pj.rel(path)}"])


def _lazy_llm(llm_factory: Optional[Callable]) -> Callable:
    """分析引擎按需构造一次，之后共用。

    不能在建 worker 的时候就造：整批图一次都没被拒的话（绝大多数时候），
    造它纯属白连一次；而密钥没配的项目会因此在出图这一步报「缺 llm_api_key」——
    出图跟分析引擎本来没关系，那个报错完全指错方向。
    """
    box: dict = {}

    def get():
        if not llm_factory:
            return None
        if "llm" not in box:
            try:
                box["llm"] = llm_factory()
            except Exception:                               # noqa: BLE001
                box["llm"] = None       # 造不出来就是没有，照常报原来的错
        return box["llm"]

    return get


def _fit_size(provider_cfg: dict, model: str, media: str,
              want, log: Callable = print):
    """把要的尺寸换成**这一家吃得下**的写法。换不过来就抛，让人去改配置。

    用户原话（2026-08-21）：「尺寸应该按照服务商的 api 规范去转换成他能吃的值
    确保尺寸正确」。

    起因：实跑里 7 张资产图请求 `16:9`、回来的是 `1024x1536`、`1086x1448`、
    `1254x1254` —— 三个人物出成竖图、两个场景出成方图。各家的写法根本不是
    一套（像素 / 比例 / 1K-4K 档位），同一个值换一家就可能不合法，
    而**不合法的后果不是报错**：多数家自己挑一个默认值出图，
    图出来了、尺寸不是你要的、任务标 ok。

    拿不到这一家的清单时**原样发**，不自己发明 —— 清单是服务商自己声明的，
    没声明就说明我们不知道它收什么，猜一个只会换一种错法。
    """
    from . import providers as _P
    from . import sizes as _sz
    try:
        cls = _P.REGISTRY.get(_P.resolve_id(provider_cfg.get("provider") or ""))
        cap = (cls().capabilities() if cls else {}) or {}
        blk = cap.get(media) or {}
        supported = blk.get("sizes" if media == "image" else "ratios") or []
    except Exception:                                       # noqa: BLE001
        supported = []
    val, note = _sz.resolve(want, supported)
    if val is None:
        raise RuntimeError(note)
    if note and log:
        log(f"⚠️ 尺寸换了：{note}")
    return val


def _soften_rounds(provider_cfg: dict) -> int:
    """改写总轮数。0 = 关掉。设置页的「被审核拒绝后改写重试」。

    每一级先试满三轮才降下一级（soften.ATTEMPTS_PER_TIER），所以默认
    12 = 四级阶梯 × 3。服务商配置里单独写了就按它的（某一家审得特别严
    时可以单独调），否则用全局默认。
    """
    v = provider_cfg.get("soften_rounds")
    if v in (None, ""):
        v = (provider_cfg.get("defaults") or {}).get(
            "soften_rounds", soften.DEFAULT_ROUNDS)
    return soften.clamp_rounds(v)


def _who_line(provider_cfg: dict, model: str) -> str:
    """这一次用的是哪家、哪个模型、**哪一把 Key**。

    分组必须写出来：有几家的行为是按分组不一样的（超模的 1K/4K、
    坤鸡的令牌分组），而排查时第一句问的就是「那次是哪个分组」。
    以前日志里只有模型名 —— 那个问题谁都答不上来。
    """
    bits = [str(provider_cfg.get("provider") or "?"), str(model or "?")]
    slot = str(provider_cfg.get("key_slot_label") or "")
    if slot and slot not in ("图片", "视频"):
        bits.append(f"用「{slot} Key」")
    return " / ".join(bits)


def make_image_worker(pj: Project, provider_cfg: dict, kind: str,
                      llm_factory: Optional[Callable] = None) -> Callable:
    """kind: asset | storyboard。返回 worker(task, log, cancel)。

    `llm_factory` 给了的话，被审核拒绝时会把提示词交给分析引擎改写再重发
    （见 soften.py）。不给就是关掉这个功能，按原来的方式直接失败。
    """
    prov = build_provider(provider_cfg["provider"], provider_cfg["api_key"],
                          provider_cfg.get("base_url", ""), provider_cfg.get("proxy", ""))
    model = provider_cfg.get("model", "")
    interval = int(provider_cfg.get("poll_interval", 5))
    timeout = int(provider_cfg.get("poll_timeout", 900))
    ref_side = int(provider_cfg.get("ref_max_side", 1024))
    to_ref = make_ref_resolver(pj, prov, provider_cfg, model, ref_side, media="image")
    _llm = _lazy_llm(llm_factory)

    def worker(task: dict, log: Callable, cancel: Callable) -> dict:
        out = pj.p(*task["output"].split("/"))
        want = _fit_size(provider_cfg, model, "image",
                         (task.get("params") or {}).get("size", "1024x1536"), log)
        if probe.have_output(out):
            # 跳过也要重新量一遍：不然「比例不对」的提醒会被这次跳过悄悄清掉，
            # 而文件其实还是那个躺倒的文件。以磁盘上的东西为准。
            return {"skipped": True, "msg": "已经有了，跳过（做出来的就不再动）",
                    "warn": _ratio_warn(pj, out, want, kind, task["key"],
                                        provider_cfg, model, "image")}
        prompt = read_text(pj.p(*task["prompt_ref"].split("/")))
        want_refs = sorted(task.get("reference_images", []),
                           key=lambda x: x.get("image_n", 0))
        # 先把整批都点一遍再动手解析。一是别为注定失败的任务白传几 MB 上对象存储，
        # 二是要一次说清缺哪几张，而不是缺一张报一张。
        srcs = [(r, r.get("url") or r.get("file_ref") or "") for r in want_refs]
        missing = [r.get("asset_id") or f"第{r.get('image_n', '?')}张"
                   for r, s in srcs if not s]
        if missing:
            # 声明了要这张参考图，却指不到任何文件。以前是 `if src` 跳过 ——
            # 于是「声明 1 张、一张没传」照样出图，出来的脸不是本人，任务还标 ok。
            # 这类静默降级只能靠肉眼在几百张里发现，是最坏的一类错。
            raise RuntimeError(
                f"参考图指不到文件：{'、'.join(missing)}。"
                f"声明了 {len(want_refs)} 张，只解析出 {len(want_refs) - len(missing)} 张 —— "
                f"少一张就出图，脸和场景都会跑掉，所以这里停下。"
                f"多半是环节8 把不存在的东西写进了参考图顺序"
                f"（比如把本段故事板自己写进去），去「任务明细」看这一条的参考图那栏。")
        # 两道校验顺序不能反：编号对不上时六字段校验会因为找不到槽位而漏报，
        # 报「身份映射不全」也会盖住真正的问题（编号错位）。
        who, ref = task["key"], task.get("prompt_ref") or ""
        # 先把能自动纠正的编号错位改掉（以上传列表为准），再校验。
        # 改过的要说出来并且入账 —— 不然排查的人对着提示词文件找错位，
        # 而实际出图用的正文和文件已经不一样了。
        prompt, map_fixes = _auto_fix_image_map(prompt, want_refs)
        if map_fixes:
            msg = (f"{who} 的提示词里参考图编号错位，已按实际上传顺序自动纠正："
                   + "；".join(map_fixes)
                   + f"。出图用的是纠正后的正文；文件本体没动，重跑会再纠一遍。")
            log(f"⚠️ {msg}")
            diagnose.record(pj.root, diagnose.warn(
                "IMAGE_MAP_AUTOFIX", msg, stage=kind, target=task["key"],
                extra_fix=[f"提示词文件在：{ref}（文件里仍是错位的写法）"]))
        for bad_map, map_warn in (check_image_map(prompt, want_refs, who, ref,
                                                  task.get("no_image_refs")),
                                  check_identity_map(prompt, want_refs, who, ref)):
            if bad_map:
                raise RuntimeError(bad_map)
            if map_warn:
                log(f"⚠️ {map_warn}")
        refs = [to_ref(s, log) for _, s in srcs]
        log(f"{_who_line(provider_cfg, model)}　参考图×{len(refs)}")
        meta = soften.run_with_softening(
            lambda p: prov.generate_image(
                ImageTask(prompt=p, refs=refs, size=want, model=model),
                out, log=log, cancel=cancel,
                poll_interval=interval, poll_timeout=timeout),
            prompt, pj=pj, llm=_llm(), kind=kind, key=task["key"],
            rounds=_soften_rounds(provider_cfg), log=log)
        pj.upsert_registry(kind, {"id": task["key"], "file_ref": task["output"],
                                  "status": "generated", **meta})
        pj.log_event({"stage": kind, "id": task["key"], "result": "ok", **meta})
        # 记一次出图。出图出片是按次计费的，钱主要花在这里，必须入账。
        ledger.record(pj.root, kind="image", stage=kind, target=task["key"],
                      episode=task.get("episode", ""),
                      provider=meta.get("provider", provider_cfg.get("provider", "")),
                      model=meta.get("model", model), count=1, size=want)
        return {"output": task["output"],
                "warn": _ratio_warn(pj, out, want, kind, task["key"],
                                    provider_cfg, model, "image")}

    return worker


def make_video_worker(pj: Project, provider_cfg: dict,
                      llm_factory: Optional[Callable] = None) -> Callable:
    prov = build_provider(provider_cfg["provider"], provider_cfg["api_key"],
                          provider_cfg.get("base_url", ""), provider_cfg.get("proxy", ""))
    model = provider_cfg.get("model", "")
    interval = int(provider_cfg.get("poll_interval", 10))
    timeout = int(provider_cfg.get("poll_timeout", 2400))
    ref_side = int(provider_cfg.get("ref_max_side", 1024))
    to_ref = make_ref_resolver(pj, prov, provider_cfg, model, ref_side, media="video")
    _llm = _lazy_llm(llm_factory)
    # 按账号计费、一个账号只能同时跑一条的家（HVTALD）：按账号排队。
    # 声明在服务商自己身上，这里只问一句。别家 pool 是 None，走老路。
    pid = provider_cfg["provider"]
    n_acct = (accounts.configure(pid, provider_cfg["api_key"])
              if getattr(prov, "per_account_serial", False) else 0)
    pool = accounts.pool(pid) if n_acct else None
    if n_acct:
        # **把并发闸门的上限压到账号数。**
        #
        # 不压的话，等空账号这件事发生在 `with GATE.slot(provider)` **里面** ——
        # 一个账号 + 并发 10 = 9 条任务占着全局槽位干等，而全局默认只有 8 个槽，
        # 别家的出图会被这 9 条堵死。
        #
        # 压到账号数之后，多出来的任务卡在闸门外，进来的每一条都能立刻
        # 拿到一个空账号。池子里的排队只剩下「换家补跑时账号数变了」这种边角。
        from .executor import GATE
        GATE.set_provider_limit(pid, n_acct)

    def worker(task: dict, log: Callable, cancel: Callable) -> dict:
        out = pj.p(*task["output"].split("/"))
        p = task.get("params") or {}
        want = _fit_size(provider_cfg, model, "video",
                         p.get("ratio", "9:16"), log)
        if probe.have_output(out):
            # 同上：跳过时也重新量，别把「比例不对」的提醒清没了
            return {"skipped": True, "msg": "已经有了，跳过",
                    "warn": _ratio_warn(pj, out, want, "video", task["key"],
                                        provider_cfg, model, "video")}
        # V6.2 第 19 章：视频必须带覆盖**完整关键时间推进**的有序故事板骨架。
        # 所以这里传的是整条，不是一张。老产物只有一张，那就是一条长度 1 的骨架。
        spine = [str(s.get("file_ref") or "")
                 for s in sorted(task.get("storyboard_refs") or [],
                                 key=lambda s: s.get("order") or 0)
                 if s.get("file_ref")] or [task.get("storyboard_ref") or ""]
        # **不能用 isfile。** 0 字节和下了一半的文件都是「文件存在」，
        # 而出片是最贵的一步：拿一张空图当参考发出去，模型等于没有参考，
        # 出来的人不是本人 —— 任务还标 ok。配了对象存储的话更彻底：
        # 那个 0 字节文件会被原样传上去再给服务商，连解码失败都不会有。
        bad = [s for s in spine
               if not (s.startswith("http")
                       or probe.have_output(pj.p(*s.split("/"))))]
        if bad or not spine[0]:
            raise RuntimeError(
                f"固定故事板不存在或者是个空文件，出不了片："
                f"{'、'.join(bad) or '这一段一张故事板都没有'}。"
                f"这一段的骨架应该有 {len(spine)} 张，缺 {len(bad) or len(spine)} 张 —— "
                f"先把环节9（故事板生产）这一段跑出来。"
                f"V6.2 要求整条时间骨架都在：缺中间那几张的话，"
                f"模型不知道这一段先发生什么后发生什么，出来的画面和剧情没有关系。")
        prompt = read_text(pj.p(*task["prompt_ref"].split("/")))
        # **提示词里的映射要覆盖整条骨架。**
        #
        # 出图那一层一直有这道校验，出片这一层以前没有 —— 而 V6.2 之后
        # 一段有 1..N 张有序故事板，正文只映射了第一张的话，模型收到 N 张
        # 没有标签的图、只知道其中一张是谁。**它不会报错**，只会把后段的
        # 画面当成前段用，出来的片子时间顺序是乱的。
        #
        # 只提醒不硬停：出片是最贵的一步，为一句措辞把整段拦住不值得 ——
        # 而这条提醒足够让人看出该回去改哪个环节。
        if len(spine) > 1:
            mapped = {a for _, a in _IMAGE_MAP.findall(prompt or "")}
            miss = [os.path.splitext(os.path.basename(s))[0]
                    for s in spine
                    if not any(os.path.splitext(os.path.basename(s))[0].endswith(m)
                               or m.endswith(os.path.splitext(os.path.basename(s))[0])
                               for m in mapped)]
            if miss:
                log(f"⚠️ 这一段的故事板骨架有 {len(spine)} 张，但提示词里只映射了 "
                    f"{len(mapped)} 张 —— 没提到的：{'、'.join(miss[:4])}。"
                    f"模型收到的是几张没有标签的图，只知道其中一张是谁，"
                    f"会把后段当前段用（不报错，出来的片子时间顺序是乱的）。"
                    f"去重跑第十三环节，或者在「任务明细」里把缺的那几行补上 —— "
                    f"`Image N = SBPKG_..._SHEET_B`，顺序和上面传的一致。")
        refs = [to_ref(s, log) for s in spine]
        # **骨架之后是补图。以前这几张压根没被上传过。**
        #
        # 出片这一层只读 `aux_reference`，而那个字段只有 V6.1 会设
        # （stages.py 里从 `aux_reference_asset_id` 来）。电影级把补图放在
        # `reference_images`（n13 的 reference_order 里骨架之后那几条，
        # 每一条还带着「这一张解决哪项骨架给不了的权威缺口」的论证）——
        # 没有一处读它。
        #
        # 后果不是报错：提示词里写着 `Image 3 = LK002 当前造型`，
        # 实际只传了骨架那两张 —— 编号整体错位，模型把补图的说明套在
        # 骨架第二张上。**画面出得来，用的是错的参考。**
        aux = sorted((task.get("reference_images") or []),
                     key=lambda r: r.get("image_n") or 0)
        for r in aux:
            f = str(r.get("url") or r.get("file_ref") or "")
            if f:
                refs.append(to_ref(f, log))
        # V6.1 的老字段照旧认（那条路径一直是对的）
        if task.get("aux_reference"):
            refs.append(to_ref(task["aux_reference"], log))
        # ---- 声明的映射 ↔ 实际要上传的清单，两头对账 ----
        #
        # 用户实遇（成片跑完了才发现）：提示词里 Image 1..5 映射了 5 张，
        # 实际只传了 1 张 —— 装配层把资产表认不出的补图 ID 悄悄挑掉了
        # （run_v34 装配视频任务时按 amap 过滤 reference_order），而上面
        # 那道「骨架多张、映射少了」的提醒只查一个方向，反方向一个字不说。
        # 后果不是报错：模型收到的图比声明少，编号从缺的那张开始整体
        # 错位，每条描述都套到别的图上 —— 片子出来看着正常，参考全是错的。
        # V6.1 不受影响：它的视频提示词不写 `Image N = 英文ID` 映射。
        _check_video_ref_map(prompt, task, spine, aux, refs)
        log(f"{_who_line(provider_cfg, model)}　{p.get('duration', 15)}s {want} "
            f"故事板骨架×{len(spine)} 补图×{len(refs) - len(spine)} "
            f"参考图共×{len(refs)}")

        def _go(use, pr):
            return use.generate_video(
                VideoTask(prompt=pr, refs=refs, duration=int(p.get("duration", 15)),
                          ratio=want, model=model,
                          resolution=provider_cfg.get("resolution", "")),
                out, log=log, cancel=cancel,
                poll_interval=interval, poll_timeout=timeout)

        acct_label = ""
        if pool is None:
            meta = soften.run_with_softening(
                lambda pr: _go(prov, pr),
                prompt, pj=pj, llm=_llm(), kind="video", key=task["key"],
                rounds=_soften_rounds(provider_cfg), log=log)
        else:
            # 占一个空账号，占着期间这个账号不会被别的任务用。
            # **每个账号一个独立的 provider 实例** —— 改共享那个的凭据
            # 是竞态：两条并发任务会互相把对方的账号改掉，
            # 于是两条都打到同一个账号上，而这正是要防的事。
            with pool.slot(log=log, cancel=cancel) as acct:
                acct_label = acct.label
                mine = build_provider(pid, acct.api_key,
                                      provider_cfg.get("base_url", ""),
                                      provider_cfg.get("proxy", ""))
                meta = soften.run_with_softening(
                    lambda pr: _go(mine, pr),
                    prompt, pj=pj, llm=_llm(), kind="video", key=task["key"],
                    rounds=_soften_rounds(provider_cfg), log=log)
                # 做成了才记 —— 计数是「这个账号今天做出了多少条」，
                # 不是「试了多少次」。失败的那次没扣次数也不该占计数。
                accounts.bump(pid, acct.label)
        if acct_label:
            meta = dict(meta or {})
            meta["account"] = acct_label
        pj.upsert_registry("video", {"id": task["key"], "file_ref": task["output"],
                                     # 整条骨架都入台账 —— 事后要能查出这一段
                                     # 是拿哪几张、按什么顺序做出来的。
                                     "storyboard_ref": spine[0],
                                     "storyboard_spine": list(spine),
                                     "status": "generated", **meta})
        pj.log_event({"stage": "video", "id": task["key"], "result": "ok", **meta})
        # 出片是最贵的一步，必须入账。带上时长——按秒计价的家要用
        ledger.record(pj.root, kind="video", stage="video", target=task["key"],
                      episode=task.get("episode", ""),
                      provider=meta.get("provider", provider_cfg.get("provider", "")),
                      model=meta.get("model", model), count=1,
                      # 按账号计费的家：这一条是哪个账号出的钱，账本里要有。
                      # 没有的话「这个月哪个账号花了多少」只能靠猜。
                      account=acct_label,
                      duration=int(p.get("duration", 15)), ratio=want)
        return {"output": task["output"],
                "warn": _ratio_warn(pj, out, want, "video", task["key"],
                                    provider_cfg, model, "video")}

    return worker
