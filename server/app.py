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

from core import build_info, diagnose, docparse, episodes, probe, stages as S
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

# 放进插件目录当范例。写成 .py.txt 是故意的：改完名字去掉 .txt 才会被加载，
# 免得半成品被当成真插件加载失败、在设置页刷一条红色错误。
PLUGIN_TEMPLATE = '''# -*- coding: utf-8 -*-
"""外挂服务商范例。改完把文件名的 .txt 去掉，回设置页点「重新扫描」。

必填：id / name / supports / capabilities() / 对应的 generate_*
参考图形式一定要声明对（见下面的三个方法），声明错了不会报错，
只会让参考图被悄悄丢掉 —— 出来的图不是同一个人，任务还标成功。
"""

from core.providers.base import ImageTask, Provider, VideoTask
from core.apiutil import ApiError, extract_image_items


class MyProvider(Provider):
    id = "myprovider"                 # 唯一标识，配置和优先级链里用它
    name = "我的服务商 example.com"      # 前端下拉显示的名字
    default_base_url = "https://api.example.com"
    supports = ("image",)             # ("image",) / ("video",) / 两个都写
    aliases = ()                      # 别名，配置里写别名也认

    # 参考图给什么形式。三个方法按需覆盖，不覆盖就是「给我 data URI」：
    #   ref_mode = "url"    这家只收公网链接（本机图会先传对象存储）
    #   ref_mode = "bytes"  这家只收文件字节（multipart 接口）
    #   accepts_url(...)    返回 False = 给链接它读不了（比如内联 base64 的接口）
    # 同一家图片和视频接口不一样时，覆盖带 media 参数的那两个方法按 media 分开判断。
    ref_mode = "data_uri"

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "image": {
                "models": ["my-model-v1"],
                "default_model": "my-model-v1",
                "sizes": ["1024x1536", "1024x1024"],
                "default_size": "1024x1536",
                "max_refs": 5,
                "ref_mode": "data_uri",
                "notes": "这里写这家的坑：字段名、单位、限制，跑之前会看到。",
            },
            "notes": "这一家的总体说明。",
        }

    def generate_image(self, task: ImageTask, dest: str, *, log=print,
                       cancel=None, poll_interval: int = 5,
                       poll_timeout: int = 900) -> dict:
        body = {"model": task.model or "my-model-v1",
                "prompt": task.prompt,
                "size": task.size or "1024x1536"}
        if task.refs:
            body["images"] = task.refs[:5]
        log(f"model={body['model']} size={body['size']} 参考图×{len(task.refs or [])}")
        data = self.session.request("POST", "/v1/images/generations",
                                    json_body=body, retries=2, timeout=600)
        items = extract_image_items(data)
        if not items:
            raise ApiError(f"没返回可用结果: {str(data)[:300]}")
        self.session.save_item(items[0], dest)       # 存盘（URL 或 b64 都能处理）
        return {"provider": self.id, "model": body["model"], "source": items[0][:200]}
'''

JOBS = JobManager()


def load_config() -> dict:
    cfg = read_json(paths.config_path(), {}) or {}
    cfg.setdefault("projects_dir", paths.default_projects_dir())
    cfg.setdefault("providers", {})
    llm_cfg = cfg.setdefault("llm", {})
    # 老配置没有该字段时也按非流式处理。这个开关适用于任意 LLM Base URL，
    # 不与具体服务商绑定。
    llm_cfg.setdefault("stream", False)
    # **读进来就把非法的上限纠正掉。**
    # 保存时会夹（_max_tokens），但那只管新存的 —— 早先存进去的
    # 9,999,999 会一直躺在 config.json 里：页面上显示着它，每次调用都在
    # 日志里刷一句「你填的是 9,999,999…」，而人不点保存就一直不会变。
    # 这里纠正之后，下一次任何一处保存都会把正确的值落盘。
    if "max_tokens" in llm_cfg:
        llm_cfg["max_tokens"] = _max_tokens(llm_cfg["max_tokens"])
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
        # 只留「真的要人来定」的：出图尺寸、画面比例、单段秒数（视频模型一次
        # 能生成多久，是硬约束）、并发、重试。
        # 镜头数 5-8、关键帧 4-6、故事板格数、单集分钟这些**不再是配置项** ——
        # 它们是给模型判断用的创作区间（skill 规定的），写在提示词模板里。
        # 做成旋钮会误导人去「控制」它们，而该由模型按每一段的信息密度定。
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


SYSTEMS = ("v61", "v34")

# 两套体系对外只有这两个名字。**别再用别的说法。**
#
# 名字一度有三层在打架：内部 id 叫 `v34`、界面写「V3.4」、而实际在跑的
# skill 已经是 V5.6 —— 同一套东西三个叫法，讨论问题时谁都不确定在说哪个。
#
# 内部 id **不许改**：它是 project.json 里 `system` 字段的值，改了所有
# 已建项目会被判成「另一套体系」，产物全部重跑、钱重花一遍。
# 所以只统一显示名，id 原样留着。
#
# skill_version 是**当前**基于的 skill 版本，升级时改这里一处；
# 项目建立时会把它抄进 project.json，这样老项目也知道自己按哪版跑的。
SYSTEM_LABELS = {
    "v34": {"name": "电影级十七章", "skill_version": "V6.1",
            "note": "17 章 / 15 次 LLM 调用，逐段落到场景状态图与故事板包"},
    "v61": {"name": "通用十二环节", "skill_version": "V6.1",
            "note": "12 环节 / 8 次 LLM 调用，逐集逐段直接编故事板"},
}


def system_label(sid: str) -> str:
    """给人看的全名，例如「电影级十七章（V5.6）」。"""
    s = SYSTEM_LABELS.get(sid or "")
    return f"{s['name']}（{s['skill_version']}）" if s else (sid or "未知体系")


def _system_of(v) -> str:
    """建项目时选的生产体系。认不出的一律回落 v61 —— 那是跑过真项目的那套。"""
    s = str(v or "").strip().lower()
    return s if s in SYSTEMS else "v61"


# 新建项目用哪套。和上面那个默认值**故意不一样**：
# 读老项目的 meta 缺字段，只能是 v61（它们本来就是那套跑出来的）；
# 建新项目缺字段，那是调用方没传，应该给现在在用的那套 —— 用一个函数管两件事，
# 就会出现「批量建剧全建成了旧体系」而且不报错。批量建剧接口原本就是这么漏的。
NEW_SYSTEM = "v34"


def _new_system(v) -> str:
    s = str(v or "").strip().lower()
    if s in SYSTEMS:
        return s
    # 这一版限定了体系就用它 —— 不然「通用版」那个包默认建出来是电影级，
    # 而体系建完不能改，等于白建一个项目。
    return build_info.only() or NEW_SYSTEM


def system_of(pj: Project) -> str:
    """这个项目用哪套体系。

    老项目的 meta 里没有这个字段，回落 v61 —— 它们本来就是 V6.1 跑出来的，
    换一套体系去读会把产物全判成「还没做」，然后重跑一遍花第二份钱。
    """
    return _system_of((pj.meta() or {}).get("system"))


_CHAOMO_KEY_FIELDS = {
    "llm": "llm_api_key",
    "image_1k": "image_1k_api_key",
    "image_4k": "image_4k_api_key",
    "video": "video_api_key",
}
_KEY_LABELS = {"llm": "LLM", "image": "图片", "image_1k": "图片 1K",
               "image_4k": "图片 4K", "video": "视频"}


def _media_capability(kind: str) -> str:
    return "video" if kind == "video" else "image"


def _provider_key_slot(provider_id: str, capability: str, model: str = "") -> str:
    pid = resolve_provider_id(provider_id or "")
    if pid == "chaomo" and capability == "image":
        return "image_4k" if "4k" in str(model).lower() else "image_1k"
    return capability


def _provider_api_key(provider_id: str, provider_cfg: dict, capability: str,
                      model: str = "") -> str:
    """取某种能力真正该用的 key；超模没有可跨能力复用的通用 key。"""
    pid = resolve_provider_id(provider_id or "")
    slot = _provider_key_slot(pid, capability, model)
    field = _CHAOMO_KEY_FIELDS.get(slot) if pid == "chaomo" else "api_key"
    return str((provider_cfg or {}).get(field or "api_key") or "").strip()


def _key_fields(provider_id: str) -> tuple:
    """这一家的 Key 分几把各自填。取不到就当不分 —— 照旧一个框。"""
    try:
        from core import providers as _P
        cls = _P.REGISTRY.get(_P.resolve_id(provider_id or ""))
        return tuple(getattr(cls, "key_fields", ()) or ())
    except Exception:                                   # noqa: BLE001
        return ()


def _merge_group_keys(provider_id: str, incoming: dict, saved: dict) -> dict:
    """把 `image_4k_api_key` 这几把合并进 `api_key`，返回改过的 incoming。

    页面上是几个各自命名的框（跟超模一样）；存下来仍然是
    `1k=…;4k=…;high=…` 这一个字符串 —— 服务商那边本来就认它
    （`kunji.parse_keys`），也是用户直接粘客服那段文本时的形状。
    这样不用改服务商一行代码，老项目存的单把 Key 也照旧能用。

    **每一把各自「留空 = 不改」。** 不按把合并的话，改 4K 那一把会把 1K
    那把清掉 —— 而清掉之后 1K 回落到 default，图照样出得来，
    只是用的是另一把 Key，谁都看不出来。
    """
    fields = _key_fields(provider_id)
    if not fields:
        return incoming
    names = [f[0] for f in fields]
    if not any(n in incoming for n in names):
        return incoming                 # 这次没动这几个框，别碰 api_key

    from core.providers.kunji import parse_keys as _pk
    # **这次一起提交了 `api_key` 那一栏时，以它为底。** 用户的用法是
    # 「把客服给的 `1k=…;4k=…` 整段粘进来，再单独改某一把」——
    # 拿已存的当底就会把刚粘进来的那段丢掉，而丢掉之后不报错，
    # 只是有几档用的还是旧 Key。
    typed = str((incoming or {}).get("api_key") or "").strip()
    base = typed if (typed and "…" not in typed and "•" not in typed) \
        else str((saved or {}).get("api_key") or "")
    out = dict(_pk(base))
    for (name, gid, _label, _why) in fields:
        if name not in incoming:
            continue                    # 这一把没提交，保持原样
        val = str(incoming.pop(name) or "").strip()
        if not val or "…" in val or "•" in val:
            continue                    # 留空 / 是掩码回显 → 不改这一把
        out[gid] = val
    order = [f[1] for f in fields if out.get(f[1])]
    # 老的单把 Key（`{"default": …}`）要一起写回去 —— 它是「没配分组的档位」
    # 的回落。只写分组那几把的话，一个原来单填一把、现在补了 4K 的人
    # 会发现 1K 那一档没 Key 了。`parse_keys` 认 `default=` 这个键。
    if out.get("default") and "default" not in order:
        order.append("default")
    if order:
        incoming["api_key"] = ";".join(f"{g}={out[g]}" for g in order)
    return incoming


def _provider_key_status(provider_id: str, provider_cfg: dict) -> dict:
    """只返回有没有配置，不把密钥本身送到浏览器。"""
    out = {cap: bool(_provider_api_key(provider_id, provider_cfg, cap))
           for cap in ("llm", "image_1k", "image_4k", "video")}
    out["image"] = out["image_1k"] or out["image_4k"]
    # 分把填的家：逐把报「配了没有」，页面用它显示「已保存，留空不改」。
    # 不报的话每个框都写着「粘贴 key」，人会以为没存上，然后重新粘一遍 ——
    # 而重新粘的时候很容易只粘一把，把另外几把冲掉。
    fields = _key_fields(provider_id)
    if fields:
        from core.providers.kunji import parse_keys as _pk
        have = _pk(str((provider_cfg or {}).get("api_key") or ""))
        for (name, gid, _l, _w) in fields:
            out[name] = bool(have.get(gid))
        # 只有一把通用 Key（老项目、或者客服只给了一把）：那把在 default 上，
        # 而 default 是「视频 Key」那一格的分组 —— 所以那一格照样显示已保存。
        out["key_default_only"] = bool(have.get("default")) and not any(
            have.get(f[1]) for f in fields if f[1] != "default")
    return out


def _llm_provider_id(llm_cfg: dict) -> str:
    """优先按 Base URL 识别，避免页面换了域名却残留旧 provider。"""
    host = (urlparse(str(llm_cfg.get("base_url") or "")).hostname or "").lower()
    if host:
        for item in list_capabilities():
            known = (urlparse(item.get("default_base_url") or "").hostname or "").lower()
            if known and known == host:
                return resolve_provider_id(item.get("id") or "")
    return resolve_provider_id(llm_cfg.get("provider") or "") or "paisio"


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
    capability = _media_capability(kind)
    for sel in raw:
        pid = resolve_provider_id((sel or {}).get("provider") or "")
        if not pid:
            continue
        saved = ((cfg.get("providers") or {}).get(pid, {}) or {})
        model = (sel or {}).get("model") or ""
        slot = _provider_key_slot(pid, capability, model)
        if not _provider_api_key(pid, saved, capability, model):
            skipped.append(f"{pid}（{_KEY_LABELS[slot]} Key）")
            continue
        out.append(resolve_provider_cfg(cfg, sel, kind))
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
            cache_key = (pid, _provider_key_slot(pid, _media_capability(kind), model))
            if cache_key not in cache:
                try:
                    cache[cache_key] = set(build_provider(
                        pid, pcfg.get("api_key", ""), pcfg.get("base_url", ""),
                        pcfg.get("proxy", "")).list_models())
                except Exception:                       # noqa: BLE001
                    cache[cache_key] = set()            # 拉不到就不判，别误伤
            avail = cache[cache_key]
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


def resolve_provider_cfg(cfg: dict, sel: dict, kind: str = "") -> dict:
    """页面选择 + config 里保存的凭据 → 完整服务商配置。"""
    # 别名归一：同一家常有几个叫法（鹤 / 派系 / pis 都是 api.paisio.online），
    # 老配置里写的可能是别名，认了才不会报「未知服务商」。
    pid = resolve_provider_id(sel.get("provider") or "")
    saved = ((cfg.get("providers") or {}).get(pid)
             or (cfg.get("providers") or {}).get(sel.get("provider") or "", {}))
    out = dict(saved)
    out.update({k: v for k, v in sel.items() if v not in (None, "")})
    out["provider"] = pid
    capability = _media_capability(kind)
    slot = _provider_key_slot(pid, capability, out.get("model", ""))
    key = _provider_api_key(pid, out, capability, out.get("model", ""))
    if not key:
        label = _KEY_LABELS[slot]
        raise ValueError(f"服务商 {pid} 未配置{label} Key（在「服务商」页签保存）")
    # worker 仍只接收统一的 api_key；在进入 worker 前完成按能力路由。
    out["api_key"] = key
    # **哪个分组要带下去。** 算出来却扔掉的话，日志和失败记录里只有模型名，
    # 看不出这一次用的是哪一把 Key —— 而有几家（超模的 1K/4K、坤鸡的令牌分组）
    # 的行为是**按分组**不一样的：实遇超模 1K 分组把内嵌图片字段截在 4096 字符，
    # 排查时第一个要问的就是「那次是哪个分组」，而答案原本谁都给不出来。
    out["key_slot"] = slot
    out["key_slot_label"] = _KEY_LABELS.get(slot, slot)
    # 参考图上传配置是全局共用的（一个对象存储服务所有服务商），
    # 但某家自己的上传端点能不能用是按家配的
    out["upload"] = dict(cfg.get("upload") or {})   # 上传配置全局共用一份
    # 全局默认也带上：worker 只拿得到这一份配置，拿不到整个 config。
    # 「被审核拒绝后改写重试」是全局设的，某一家想单独调就在这家里写
    # 同名字段覆盖 —— 不带下来的话，设置页那个输入框就是个假旋钮。
    out["defaults"] = dict(cfg.get("defaults") or {})
    return out


def build_llm(cfg: dict, override: dict = None) -> LLM:
    c = dict(cfg.get("llm") or {})
    # False 是 stream 的有效覆盖值，不能像空字符串一样过滤掉。
    c.update({k: v for k, v in (override or {}).items() if v not in (None, "")})
    if not c.get("api_key"):
        pid = _llm_provider_id(c)
        saved = ((cfg.get("providers") or {}).get(pid, {}) or {})
        c["api_key"] = _provider_api_key(pid, saved, "llm")
        if not c.get("base_url"):
            c["base_url"] = saved.get("base_url", "")
    if not c.get("api_key"):
        raise ValueError("LLM 未配置 api_key（在「分析引擎」页签保存）")
    notes: list = []
    mt = _max_tokens(c.get("max_tokens"), note=notes.append)
    llm = LLM(c["api_key"], c.get("base_url", "https://api.paisio.online"),
              c.get("model", "claude-sonnet-5"), timeout=_timeout(c.get("timeout")),
              proxy=c.get("proxy", ""), max_tokens=mt,
              stream=c.get("stream", False) is True)
    # 被夹住的话让每次调用的日志都带上一句 —— 只在设置页说一次，
    # 跑起来看日志的人看不到，而看日志的时候才是他在纳闷「怎么是 200000」
    llm.config_notes = notes
    return llm


# 各家的真实上限都在 20 万 token 以内。填 999999 这种值网关会拿它去做
# 预分配或直接转给上游，行为不可预测 —— 实测表现是流到中途连接被关，
# 报错却指向「网络中断」，根本看不出是这个值的问题。
# 页面上那个 input 标了 max="200000"，但 HTML 的 max 不会阻止提交，
# 所以真正的关口必须在这里。
# 12.8 万，不是 20 万。
#
# **网关会校验这个字段并直接 400。** 我原来按「远高于任何现役模型的实际
# 输出能力，撞不到它」定了 20 万 —— 那句话是错的：撞不到的是**模型**，
# 而**网关**会先把它挡回来：
#
#   HTTP 400: Field 'max_output_tokens' must be at most 128000
#
# 代价很实在：实跑里 n3/n4/n4b 各挂了一次，每次都是先把几十万 token 的输入
# 发出去、等 127 秒、然后拿回这一句。而且我还加过「读配置时自动纠正」——
# 于是那个被拒绝的 20 万被**写进了 config.json**，每次都稳定复现。
MAX_TOKENS_CEILING = 128000


def ref_limit_of(cfg: dict, kind: str = "video") -> int:
    """这条链的首选服务商一次能吃几张参考图。拿不到返回 0（=未知）。

    这个数注册表里一直有，但以前只有出图那一步用它做校验，
    编提示词的环节完全不知道 —— LLM 按剧情需要引 5、6 张，
    到出图才撞上限。现在把它送进提示词，让模型自己按上限挑。
    首选那家的上限就够了：换家重试时上限只会更宽松或差不多，
    按最紧的那家写反而更安全。
    """
    try:
        chain = resolve_chain(cfg, kind) or []
        if not chain:
            return 0
        pid = chain[0].get("provider") if isinstance(chain[0], dict) else chain[0]
        caps = {c["id"]: c for c in list_capabilities()}
        sec = (caps.get(pid) or {}).get(kind) or {}
        return int(sec.get("max_refs") or 0)
    except Exception:                                   # noqa: BLE001
        return 0                                        # 拿不到就说未知，别编


def _override(over, allow: tuple) -> dict:
    """「一键跑到底」/「补生产」面板送来的出图尺寸 / 画幅 / 单段秒数。

    **面板为准；面板那一项为空就不覆盖** —— 于是回落到项目参数，
    再回落到「设置 → 默认参数」。这三档的顺序是用户定的。

    秒数用 float 再取整，不用 `int(v)`：面板送 `null` 时（下拉是空的，
    JS 的 NaN 序列化成 null）`int(None)` 直接抛 TypeError，
    而那个异常从「开始」那个请求里冒出来，报的是一句 Python 类型错误，
    看不出是「视频链没配好、秒数候选是空的」。`"30.0"` 同理。

    两处入口以前各写一遍，条件还不一样（一处认 `"None"` 字符串、
    一处不认）—— 同一个空值在一条路上被忽略、在另一条路上让整轮跑崩。
    """
    out = {}
    for k, v in (over or {}).items():
        if k not in allow:
            continue
        if v is None or str(v).strip() in ("", "None", "NaN", "undefined"):
            continue                    # 空 = 不覆盖，让它回落
        if k == "duration":
            try:
                out[k] = int(float(v))
            except (TypeError, ValueError):
                continue                # 不是个数就当没给，别把整轮跑废掉
        else:
            out[k] = str(v)
    return out


def _task_index(pj: Project) -> dict:
    """tasks.json → {类别: {key: 那一条}}。手动放图要按 key 找到它的输出路径。

    **不自己拼路径。** 拼的话就得复制一份「家族 → 目录名」的表，
    而那张表在 run_v34 里 —— 两份迟早对不上，对不上的后果是图放在
    没人读的位置：出图那步照样重新生成一张，把人工挑的那张顶掉，不报错。
    """
    data = pj.tasks() or {}
    out = {}
    for kind in ("asset_tasks", "scstate_tasks", "storyboard_tasks", "video_tasks"):
        rows = {}
        for r in (data.get(kind) or []):
            if isinstance(r, dict) and r.get("key") and r.get("output"):
                rows[str(r["key"])] = r
        out[kind] = rows
    return out


def params_of(cfg: dict, pj: Project, with_script: bool = True) -> dict:
    """跑环节要的那套参数。五个入口以前各拼一遍，漏一项就静默少一项输入。"""
    meta = pj.meta()
    params = dict(cfg.get("defaults") or {})
    params.update(meta.get("params") or {})
    params.update({"project_code": meta.get("project_code", "PROJ-001"),
                   "episode": meta.get("episode", "EP01"),
                   # 两个都给：出图那几步和出片那一步的上限不是一个数。
                   # 只给一个的话，写故事板提示词的环节会按视频那家的上限
                   # 去引参考图，到出图才撞上限（实遇：出图超模 9 张、
                   # 视频派欧 30 张，故事板被告知 30）。
                   "ref_limit": ref_limit_of(cfg, "video"),
                   "ref_limit_video": ref_limit_of(cfg, "video"),
                   "ref_limit_image": ref_limit_of(cfg, "asset")})
    if with_script:
        sp = pj.p("01_剧本与分段", "原始剧本.txt")
        if os.path.isfile(sp):
            from core.store import read_text
            params["script"] = read_text(sp)
    return params


def _timeout(v) -> int:
    """LLM 超时。夹在 [60, 3600]。

    太小的坏处很具体：环节1/3 要吃整本剧本，模型思考几分钟不吐字是正常的，
    非流式下会被判成读超时，然后（更糟）当成网络抖动重试 ——
    白等三轮，还可能让上游重复计费。
    太大的坏处是卡死的请求要等一小时才放手。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 900
    return max(60, min(n, 3600))


def _poll(v, lo: int, hi: int, default: int) -> int:
    """出图出片的轮询参数。夹住范围。

    等出结果的总时长调小了最坑：视频在快出来的时候被判成失败，
    钱照花、东西没拿到，而且报错长得像服务商挂了。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


def _max_tokens(v, note=None) -> int:
    """夹到合法范围。

    **夹住不能是静默的。** 这个项目里最难查的错全是「悄悄少给了一点」，
    而我在这儿自己犯了一次：页面上填 999999，日志里显示 200000，
    人只会以为程序藏了个限制，不知道是自己那个值被改掉了。
    所以 note 给了就在被夹住时回报一句。

    另外这个上限不是性能约束：200000 远高于任何现役模型的输出能力，
    夹到它等于没夹 —— 真撞到它，说明那个值本来就不是个真数。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 16000
    if n <= 0:
        return 0                      # 0 = 不传这个参数，走服务商默认
    out = max(1024, min(n, MAX_TOKENS_CEILING))
    if note and out != n:
        note(f"「单次输出上限」你填的是 {n:,}，不在合法范围内，按 {out:,} 发出。"
             f"上限是 {MAX_TOKENS_CEILING:,} —— 再高网关会直接拒收整个请求"
             f"（Field 'max_output_tokens' must be at most 128000），"
             f"输入白发一遍、白等两分钟。")
    return out


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
        # llm.api_key 之前是原文发给前端的 —— providers 整块剔了、upload 的
        # 密钥也抹了，就漏了这一个。页面只需要知道「配了没」。
        lm = dict(pub.get("llm") or {})
        lm["api_key_set"] = bool((cfg.get("llm") or {}).get("api_key"))
        lm.pop("api_key", None)
        pub["llm"] = lm
        from core import system_v34 as V34
        from core.llm import _env_proxy, mask_url
        env_p = _env_proxy()
        mode = (lm.get("proxy") or "").strip()
        saved_providers = cfg.get("providers") or {}
        key_status = {pid: _provider_key_status(pid, saved_providers.get(pid, {}))
                      for pid in set(PROVIDER_REGISTRY) | set(saved_providers)}
        return {"config": pub,
                # 实际生效的网络路径。前端算不出来 —— 环境变量和系统代理
                # 只有服务端看得见。不回显的话代理就是隐形的。
                "net": {
                    "env_proxy": mask_url(env_p),
                    "proxy_note": (
                        "强制直连（忽略系统与环境代理）" if mode.lower() in
                        ("direct", "直连", "none", "off") else
                        f"配置指定的代理 {mask_url(mode)}" if mode else
                        f"跟随系统/环境代理 {mask_url(env_p)}" if env_p else
                        "直连（系统与环境都没有代理）"),
                },
                "providers_configured": {pid: any(status.values())
                                         for pid, status in key_status.items()},
                "provider_keys_configured": key_status,
                # 各家保存过的**非密钥**设置。以前只回一个「配了没」的布尔值，
                # 于是页面上 base_url 永远显示默认值 —— 改过自定义端点的人
                # 再点一次保存就被默认值盖掉了，而且一声不吭。
                "providers_public": {
                    k: {f: v[f] for f in ("base_url", "poll_timeout",
                                          "poll_interval",
                                          # 用户实测出来的时长档位。不回显的话
                                          # 保存一次就看不见了，等于填了个黑洞。
                                          "durations") if f in v}
                    for k, v in (cfg.get("providers") or {}).items()},
                "capabilities": list_capabilities(),
                # 两套体系的环节表都下发，前端按项目的 system 挑一套显示。
                # 不在这里挑：切项目不用重新拉一遍 bootstrap。
                "stages": S.STAGES,
                # 名字**只从 SYSTEM_LABELS 取**，别在这里另写一份 ——
                # 以前这里写「V3.4 电影级十七章」而实际跑的是 skill V5.6，
                # 三处名字对不上，讨论问题时谁都不确定在说哪一套。
                # 两套的环节表**都**下发：这一版限定了体系也照样要能打开
                # 对方建的老项目 —— 拿不到环节表的话，那个项目在页面上
                # 会显示成「一个环节都没有」。
                # `new_ok` 才是「新建时能不能选」，由构建标记决定。
                "systems": {
                    sid: dict(SYSTEM_LABELS[sid], name=system_label(sid),
                              stages=(V34.STAGES if sid == "v34" else S.STAGES),
                              new_ok=(not build_info.only()
                                      or build_info.only() == sid))
                    for sid in ("v61", "v34")},
                "flavor": build_info.flavor_name(SYSTEM_LABELS),
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
                            if probe.have_output(pj.p(*t["output"].split("/")))),
            }
        ep = (q.get("episode") or [""])[0]
        # **按这个项目自己的体系算**，不是写死 V6.1 的环节表。
        # 写死的后果：v34 项目拿到的是 s1..s8 的完成状态，
        # 而页面画的是 n1..n14 —— 每一格都显示「没做」，
        # 已经跑完的环节看起来一个都没跑。
        sys_id = system_of(pj)
        if sys_id == "v34":
            from core import system_v34 as _V34
            table, series = _V34.STAGES, set(_V34.SERIES_STAGES)
        else:
            table, series = S.STAGES, set(S.SERIES_STAGES)
        stage_state = {}
        for st in table:
            if st["kind"] == "llm" and st["out"]:
                # 逐集环节看的是「这一集做了没」；不指定集时看第一集，只为渲染流程图
                sub = "" if st["id"] in series else (ep or (episodes.ids(pj) or [""])[0])
                stage_state[st["id"]] = os.path.isfile(pj.stage_path(st["out"], sub))
        # 老 v34 项目的叙事结构/资产/空间/总账停在集目录里。V5.6 对照下来
        # 这几份该是全剧一份的，程序现在去项目根找 —— 找不到会判成
        # 「还没跑」然后把已经花过钱的七个环节重跑一遍。
        # 只**发现**不自动迁：迁移会动产物，得让人点一下。
        need_migrate = []
        if system_of(pj) == "v34":
            from core import migrate_v56
            need_migrate = migrate_v56.pending(pj)
        return {"meta": pj.meta(), "tasks_summary": done, "stages_done": stage_state,
                "root": pj.root, "episodes": episodes.summary(pj), "episode": ep,
                # 这个项目**实际**用哪套 —— 页面必须用这个，不许自己从项目列表里猜。
                # 猜的后果实跑撞过：新建的项目还不在那份列表里，页面回落成
                # 「通用十二环节」画了 12 个环节，而后端按 project.json 跑的是
                # 电影级 17 章。**页面和实际生产是两套**，而且不报错。
                "system": sys_id,
                "need_migrate": need_migrate}

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
        capability = (q.get("kind") or ["image"])[0]
        model = (q.get("model") or [""])[0]
        key = _provider_api_key(pid, pc, capability, model)
        if capability == "llm" and _llm_provider_id(cfg.get("llm") or {}) == pid:
            key = (cfg.get("llm") or {}).get("api_key") or key
        if not key:
            raise ValueError(f"服务商 {pid} 未配置{_KEY_LABELS.get(capability, '')} Key")
        prov = build_provider(pid, key, pc.get("base_url", ""))
        return {"models": prov.list_models()}

    if path == "/api/paths":
        return dict(paths.snapshot(), projects_dir=cfg["projects_dir"])

    if path == "/api/prompts":
        """提示词模板。

        scope=global   全局基础模板（设置页）
        scope=project  这一部剧自己的（要带 root）—— 语言、题材、基础设定这类
                       只影响一部剧的要求写在这里，不该动全局
        不带 name 返回清单，带了返回那一份。
        """
        from core import prompts as _pt
        n = (q.get("name") or [""])[0]
        scope = (q.get("scope") or ["global"])[0]
        root = (q.get("root") or [""])[0]
        pj = Project(root) if root else None
        if n:
            return _pt.read(n, pj, scope)
        return {"items": _pt.catalog(pj), "scope": scope,
                "global_dir": paths.prompts_dir(),
                "project_dir": S.project_prompt_dir(pj) if pj else ""}

    if path == "/api/prompt_preview":
        """跑之前看看这一步到底会发出去什么。不调模型、不写盘、不占资产。"""
        pj = proj_of({"project_root": q["root"][0]})
        meta = pj.meta()
        params = params_of(cfg, pj)
        if system_of(pj) == "v34":
            from core import run_v34
            return run_v34.preview_prompt(
                pj, (q.get("stage") or ["n1"])[0], params,
                (q.get("episode") or [""])[0], (q.get("segment") or [""])[0])
        return S.preview_prompt(pj, (q.get("stage") or ["s1"])[0], params,
                                (q.get("episode") or [""])[0],
                                (q.get("segment") or [""])[0])

    if path == "/api/tasks":
        """本项目的任务明细 —— 全部读磁盘，不用跑起来也能看。"""
        from core import explorer
        pj = Project(q["root"][0])
        return explorer.tasks(pj, (q.get("episode") or [""])[0])

    if path == "/api/prompt_file":
        """读一条实际发出去的提示词（资产/故事板/视频），给页面上的编辑框。"""
        from core import promptfile
        return promptfile.read_one(Project(q["root"][0]), (q.get("rel") or [""])[0])

    if path == "/api/providers/status":
        """服务商加载报告：哪几家、从哪儿来、有没有加载失败的插件。"""
        from core import providers as _pv
        return _pv.status()

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
        SECRET = ("api_key", "llm_api_key", "image_api_key", "image_1k_api_key",
                  "image_4k_api_key", "video_api_key", "secret_key", "access_key",
                  # 坤鸡那几把也在这张表里（image_1k/4k、video 已经有了），
                  # 补上 high 那一把 —— 漏了的话它会以明文原样存进 config。
                  "image_high_api_key")

        def _is_mask(v) -> bool:
            """这个值是我们自己打码出来的回显，不是真密钥。

            回显用的是「前4位 + …」。前端**不该**把它塞回输入框，
            但那一层已经错过一次：access_key 的掩码（5 个字符）被存了回去，
            上传时报「Credential access key has length 5, should be 32」，
            而用户完全看不出是自己点了一下保存造成的。

            所以这里再拦一道 —— 前端哪天又漏一个字段，也不会把配置写坏。
            """
            return "…" in str(v) or "•" in str(v)
        for k, v in body.items():
            if k == "providers" and isinstance(v, dict):
                cur.setdefault("providers", {})
                for pid, pv in v.items():
                    # 分组 Key（坤鸡的 1K/4K/high）先按组合并成一个 api_key，
                    # 再走下面那道「留空 = 不改」—— 顺序不能反：反了的话
                    # 合并出来的 api_key 会被当成「这次没填」而丢掉。
                    pv = _merge_group_keys(pid, dict(pv),
                                           (cur.get("providers") or {}).get(pid) or {})
                    pv = {kk: vv for kk, vv in pv.items()
                          if not (kk in SECRET
                                  and (not str(vv).strip() or _is_mask(vv)))}
                    if "poll_timeout" in pv:
                        pv["poll_timeout"] = _poll(pv["poll_timeout"], 60, 7200, 900)
                    if "poll_interval" in pv:
                        pv["poll_interval"] = _poll(pv["poll_interval"], 2, 60, 5)
                    cur["providers"].setdefault(pid, {}).update(pv)
            elif isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items()
                     if not (kk in SECRET
                             and (not str(vv).strip() or _is_mask(vv)))}
                # 这几个是只读回显字段，不该被存进 config
                v.pop("access_key_set", None)
                v.pop("secret_key_set", None)
                # 存的时候就夹住，不只在用的时候夹 —— 否则 config.json 里
                # 一直躺着个 999999，页面回显也是它，人会以为这个值是有效的。
                if k == "llm" and "max_tokens" in v:
                    v["max_tokens"] = _max_tokens(v["max_tokens"])
                if k == "llm" and "timeout" in v:
                    v["timeout"] = _timeout(v["timeout"])
                cur.setdefault(k, {}).update(v)
            else:
                cur[k] = v
        save_config(cur)
        return {"ok": True}

    if path == "/api/prompts/check":
        from core import prompts as _pt
        return _pt.check(body["name"], body.get("text", ""))

    if path == "/api/prompts/save":
        """存改写版。校验不过就不存 —— 模板改坏了要几百次调用之后才看得出来。"""
        from core import prompts as _pt
        root = str(body.get("project_root") or "")
        return _pt.save(body["name"], body.get("text", ""),
                        force=bool(body.get("force")),
                        pj=Project(root) if root else None,
                        scope=body.get("scope", "global"))

    if path == "/api/prompts/upgrade":
        """把缺失的必需占位符补回这份改写里 —— **只提议，不保存。**

        提议而不直接存：这份东西是用户手写的，程序不该替他按保存。
        他看过差异再决定，和「看差异」「恢复继承」是同一层的操作。
        """
        from core import prompts as _pt
        return {"ok": True, **_pt.upgrade(body["name"], body.get("text", ""))}

    if path == "/api/prompts/reset":
        from core import prompts as _pt
        root = str(body.get("project_root") or "")
        return _pt.reset(body["name"], pj=Project(root) if root else None,
                         scope=body.get("scope", "global"))

    if path == "/api/prompt_file/save":
        """改这一条实际发出去的提示词。

        不做内容校验：这是人工兜底的口子（出图被安全策略拦、某个词模型不认），
        人比规则清楚。只挡空内容和越界路径。
        改完立刻生效 —— worker 是出图那一刻才读文件的，不用重跑文字环节。
        """
        from core import promptfile
        return promptfile.save_one(proj_of(body), body.get("rel", ""),
                                   body.get("text", ""))

    if path == "/api/providers/reload":
        """重新扫描服务商（内置 + 插件目录），不用重启。

        新加一家之后配额表里要补上它，否则新家会没有并发配额。
        """
        from core import providers as _pv
        st = _pv.reload_all()
        cur = load_config()
        per = cur.setdefault("limits", {}).setdefault("per_provider", {})
        added = [p for p in _pv.REGISTRY if p not in per]
        for pid in added:
            per[pid] = PROVIDER_QUOTA.get(pid, 4)
        if added:
            save_config(cur)
        return {"ok": True, **st, "new_quota_for": added,
                "capabilities": list_capabilities()}

    if path == "/api/providers/mkdir":
        """把插件目录建出来，并放一份带注释的模板，照着改就能加一家。"""
        from core import providers as _pv
        d = paths.plugins_dir()
        os.makedirs(d, exist_ok=True)
        sample = os.path.join(d, "_示例_把我改成你的服务商.py.txt")
        if not os.path.isfile(sample):
            with open(sample, "w", encoding="utf-8") as f:
                f.write(PLUGIN_TEMPLATE)
        return {"ok": True, "dir": d, "sample": sample, **_pv.status()}

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
            # **别叫 probe。** 模块级 `from core import ... probe ...` 是个模块，
            # 在这儿赋一个同名局部变量，会让**整个 api_post**（741-1519 行）
            # 里的 `probe` 都变成局部变量 —— 于是别的分支里那句
            # `probe.have_output(...)` 读到一个还没赋值的名字，
            # 报「cannot access free variable 'probe'」。
            # 一个巨型函数里随手起的临时变量名，能把几百行外的另一个功能打死。
            touch = os.path.join(d, ".写入自检")
            with open(touch, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(touch)
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
        if system_of(pj) == "v34":
            from core import pipeline_v34
            return pipeline_v34.preview(
                pj, include_produce=body.get("include_produce", True),
                include_deliver=body.get("include_deliver", True),
                only_episodes=body.get("only_episodes"))
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
        params = params_of(cfg, pj, with_script=False)
        # 出图尺寸/画面比例/单段时长：点「开始」时给的值优先于「默认参数」。
        # 这些会被环节8 装配进 tasks.json，所以要在跑之前就定下来。
        params.update(_override(body.get("params_override"),
                                ("image_size", "ratio", "duration")))
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
        if system_of(pj) == "v34":
            from core import pipeline_v34
            pipeline_v34.start(
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
                ep_concurrency=int(body.get("llm_episodes")
                                   or (cfg.get("defaults") or {}).get("llm_episodes", 4)),
                seg_concurrency=int(body.get("llm_segments")
                                    or (cfg.get("defaults") or {}).get("llm_segments", 4)),
                # 这一项以前没往 v34 传 —— 页面上「分析·总上限」填了没人收，
                # 电影级的分析并发一直卡在代码默认的 4
                llm_concurrency=int(body.get("llm_concurrency")
                                    or (cfg.get("defaults") or {}).get("llm_concurrency", 6)))
            return {"ok": True, "job_id": job.id, "system": "v34"}
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
                              "system": _new_system(body.get("system")),
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
                # 用哪套生产体系。建项目时定，之后不改 —— 两套的产物文件名、
                # 环节表、任务结构都不一样，中途换等于把已有产物全作废。
                "system": _new_system(body.get("system")),
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

    if path == "/api/project/migrate_v56":
        """把老 v34 项目的五份产物从集目录合并搬到项目根。

        不迁的话程序去项目根找不到，判成「还没跑」，
        然后把已经花过钱的七个环节重跑一遍。
        老文件改名成 .bak 留着 —— 迁错了还能拿回来。
        """
        pj = proj_of(body)
        from core import migrate_v56
        lines = []
        r = migrate_v56.run(pj, log=lines.append)
        return {**r, "log": lines}

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

    if path == "/api/project/settings":
        """项目基础信息：读 schema + 当前值，或者保存。

        schema 和值一起下发：页面不该自己维护一份字段表 ——
        字段一改就得两边同步，漏一处就是「填了没生效」而且不报错。
        """
        from core import settings as ST
        pj = proj_of(body)
        if body.get("values") is not None:
            ST.save(pj, body["values"])
            # params 那几项写回它们原来的家，**不在 settings 里存第二份**
            pmap = {f["key"]: f["maps_to"] for f in ST.FIELDS
                    if f["source"] == "params" and f.get("maps_to")}
            back = {pmap[k]: v for k, v in body["values"].items()
                    if k in pmap and str(v).strip() != ""}
            if back:
                meta = pj.meta()
                meta.setdefault("params", {}).update(back)
                pj.save_meta(meta)
        params = params_of(cfg, pj, with_script=False)
        cap = (pj.meta() or {}).get("capability") or {}
        derived = dict(ST.FIXED_DERIVED,
                       reference_capacity_per_call=params.get("ref_limit", ""),
                       target_video_model=cap.get("target_video_model", ""),
                       native_multishot_support=cap.get(
                           "native_multishot_support", ""),
                       current_episode=(pj.meta() or {}).get("episode", ""),
                       target_image_model=(resolve_chain(cfg, "asset") or [{}])[0]
                       .get("model", ""))
        used = ST.used_by()
        return {
            "fields": [dict(f, tier=ST.tier_of(f["key"]), value=(
                ST.load(pj).get(f["key"]) if f["source"] == "settings"
                else params.get(f.get("maps_to") or f["key"], "")
                if f["source"] == "params"
                else derived.get(f["key"], "")),
                # 「这个设定影响哪几个环节」是**扫模板得出的**，不是手写表
                used_by=used.get(ST.placeholder_of(f["key"])) or [])
                for f in ST.FIELDS],
            "groups": list(dict.fromkeys(f["group"] for f in ST.FIELDS)),
        }

    if path == "/api/resources":
        """本机占用 + 并发能开到多少。

        两类数据分开看：
          本机余量   → 还能不能再开
          服务商反应 → 再开有没有用

        只看第一类会把并发调到一个本机撑得住、但服务商全在限流的数字上 ——
        那时候任务不失败，只是变慢并反复重试，看起来在跑，实际在原地烧钱。
        """
        from core import resources
        pj = None
        try:
            pj = proj_of(body)
        except Exception:                                   # noqa: BLE001
            pass                # 没开项目也要能看本机占用
        gate = GATE.snapshot()
        llm = LLM_GATE.snapshot()
        inflight = (int(gate.get("global_inflight") or 0)
                    + int(llm.get("llm_inflight") or 0))
        resources.sample(inflight)
        # 最近的限流次数：从这个项目的失败记录里数
        n429 = calls = 0
        if pj:
            rows = diagnose.load(pj.root)
            recent = rows[-200:]
            calls = len(recent)
            n429 = sum(1 for r in recent if r.get("code") == "RATE_LIMITED")
        return {"ok": True,
                "usage": resources.snapshot(),
                "gates": dict(gate, **llm),
                "inflight": inflight,
                "advice": resources.advise(
                    max(int(llm.get("llm_peak") or 0), inflight), n429, calls)}

    if path == "/api/project/settings/extract":
        """把粘进来的一段【项目基础信息】读成字段 —— **只出建议，不落盘**。

        为什么不自动保存：这些值会改变生产结果（画幅、时长、改编权限），
        模型读错一个字，整部剧就按错的跑，而且要到成片才看得见。
        所以固定是「解析 → 预览 → 你挑 → 保存」四步，中间那两步不能省。

        真正值钱的不是抽取，是 `conflicts` —— 散文之间的矛盾没人能自动发现。
        实跑炸过一次：用户写「字幕烧录进画面」，和「画面内禁止出现任何文字」
        并存，程序不报错，出来的图里字幕被抹掉了。
        """
        from core import settings as ST
        pj = proj_of(body)
        raw = (body.get("text") or "").strip()
        if not raw:
            raise ValueError("没有可解析的文本 —— 把那段【项目基础信息】粘进来再点。")
        llm = build_llm(cfg, body.get("llm"))
        # 拿**当前生效的**全局规则去比对，不是内置那份 ——
        # 用户改过 _common 的话，冲突要按他改过的版本判。
        rules = S.system_prompt(pj, params_of(cfg, pj, with_script=False))
        user = S.render(S.load_prompt("_settings_extract", pj),
                        ST.extract_vars(pj, raw, rules))
        out = llm.json_call("你是项目参数抽取器。只输出一个 JSON。", user,
                            required=["values"], log=lambda m: None)
        values, dropped = ST.sanitize(out.get("values") or {})
        cur = ST.load(pj)
        params = params_of(cfg, pj, with_script=False)
        return {
            "ok": True,
            # 逐条给「现在是什么 → 建议改成什么」，让人看得见改动幅度
            "proposals": [
                {"key": k, "label": ST.BY_KEY[k]["label"],
                 "source": ST.BY_KEY[k]["source"],
                 "current": (cur.get(k) if ST.BY_KEY[k]["source"] == "settings"
                             else params.get(ST.BY_KEY[k].get("maps_to") or k, "")),
                 "proposed": v}
                for k, v in values.items()],
            "conflicts": out.get("conflicts") or [],
            "unclear": out.get("unclear") or [],
            "dropped": dropped,
        }

    if path == "/api/stage/run":
        pj = proj_of(body)
        stage_id = body["stage"]
        meta = pj.meta()
        params = params_of(cfg, pj)

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
                        # 单集按钮显示为「重跑」时，环节5必须覆盖已有资产提示词；
                        # 否则旧 txt 会被增量过滤器跳过，用户看不到新规则的效果。
                        force = (stage_id == "s5" and not body.get("all_episodes")
                                 and pj.stage_data("s5_asset_prompts", tgt) is not None)
                        S.run_llm_stage(pj, stage_id, llm, params,
                                        log=lambda m, k=key: job.log(k, m), episode=tgt,
                                        force=force)
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

    if path == "/api/accounts":
        """按账号计费那几家：每个账号每天做了多少条。

        **不返回任何凭据** —— 只有 label（deviceId 前 8 位）和计数。
        """
        from core import accounts as _acct
        pid = resolve_provider_id(str(body.get("provider") or "hvtald"))
        pc = (cfg.get("providers") or {}).get(pid) or {}
        # 先按当前配置刷一遍账号池，页面上「配了几个」才是实时的。
        # 账号没变时 configure 不重建 —— 重建会把在途任务占的槽位变回空闲。
        n = _acct.configure(pid, str(pc.get("api_key") or ""))
        rep = _acct.report(pid, days=int(body.get("days") or 7))
        rep["accounts"] = n
        return {"ok": True, "provider": pid, "report": rep}

    if path == "/api/task/manual":
        """手动放图：把用户自己的图放到某条出图任务该落的位置。

        为什么需要：有些资产就该用真实素材（演员定妆照、真实场景照、
        品牌道具），或者模型怎么都画不对，人工挑一张更快。
        以前只能自己往文件夹里拷 —— 而**家族目录名要猜对**，
        猜错了图放在那儿也没人读，出图那步照样重新生成一张，
        既花钱又把人工挑的那张顶掉，且不报错。

        两种情况分开处理，差别很大：

          位置是空的  → 直接写进去、登记指纹。出图那步看到文件在就跳过。
          已经有一张  → **不原地覆盖。** 建一个新版本（R02、R03…），
                        因为引用过旧那张的故事板还指着旧文件；
                        原地换的话它们的指纹全部对不上，
                        而更糟的是：**内容变了、引用没变，没人报错**。
                        建新版本之后要重装配任务（tasks.json 里记的还是旧路径），
                        所以这里顺手重装配，并把这件事说出来。
        """
        import base64
        pj = proj_of(body)
        kind = str(body.get("kind") or "").strip()
        key = str(body.get("key") or "").strip()
        raw = base64.b64decode(body.get("content_b64") or "")
        if not raw:
            raise ValueError("没有收到文件内容")
        if len(raw) > 40 * 1024 * 1024:
            raise ValueError("文件超过 40MB")
        # **先确认它真是一张图。** 一个改了扩展名的文件放进去之后，
        # 「出没出」那道检查只看大小，会判成已出 —— 然后下游把它当参考图
        # 发给服务商，报的是一句服务商的解码错误，看不出是这张图的事。
        try:
            from io import BytesIO
            from PIL import Image
            im = Image.open(BytesIO(raw))
            im.verify()
            fmt = (im.format or "").upper()
        except Exception as exc:                        # noqa: BLE001
            raise ValueError(f"这个文件不是能读的图片（{exc}）。"
                             f"如果是 HEIC/WebP 之类，先转成 PNG 或 JPG 再放。")
        # **太小的文件放进去等于没放。** 「做出来了没有」全程只看文件在不在
        # 加一个体积下限（probe.have_output，用来挡 0 字节空壳）——
        # 比它还小的图会被判成「还没出」，然后出图那一步照样生成一张盖掉它。
        # 图在盘上、被顶掉了、一句话都没有。
        if len(raw) < probe.MIN_OUTPUT_BYTES:
            raise ValueError(
                f"这张图只有 {len(raw)} 字节，比「算做出来了」的下限"
                f"（{probe.MIN_OUTPUT_BYTES} 字节）还小 —— 放进去也会被判成"
                f"「还没出」，然后出图那一步生成一张把它盖掉，而且不报错。"
                f"用一张正常分辨率的图（正片的资产图通常几百 KB）。")

        tasks = _task_index(pj)
        row = (tasks.get(kind) or {}).get(key)
        if not row:
            have = "、".join(sorted(tasks.get(kind) or {})[:6]) or "（这一类没有任务）"
            raise ValueError(f"这个项目的「{kind}」里没有 {key}。"
                             f"当前有：{have}…。任务是环节5 / 环节8 装配出来的，"
                             f"先把文字环节跑完再放图。")

        rel = row["output"]
        rebuilt = 0
        note = ""
        if os.path.isfile(pj.p(*rel.split("/"))) and kind == "asset_tasks":
            # 已经有一张 —— 建新版本，别原地换
            from core import registry_v34 as REG
            rev = REG.bump(pj, key, "人工放入的图")
            rel = re.sub(r"_R\d{2}(\.\w+)$", lambda m: f"_R{rev:02d}{m.group(1)}", rel)
            note = (f"这个位置本来已经有一张了，所以建了第 {rev} 版（{rel}）"
                    f"而不是原地覆盖 —— 引用过旧那张的故事板还指着旧文件，"
                    f"原地换的话它们用的图变了而引用没变，没有一处会报错。")
        elif os.path.isfile(pj.p(*rel.split("/"))):
            note = ("这个位置本来已经有一张，已被这次放入的图覆盖。"
                    "（故事板和场景状态图没有版本机制，只能覆盖 ——"
                    "如果有别的东西引用过旧那张，去「连续性检查」看一眼。）")

        dst = pj.p(*rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(raw)

        if kind == "asset_tasks":
            from core import registry_v34 as REG
            try:
                REG.promote(pj, key, rel)
            except Exception as exc:                    # noqa: BLE001
                # 登记失败要说 —— 不登记的话参考图解析那一步找不到它，
                # 报的是「注册表里没有这个资产」，看不出是放图这一步没登完。
                note += f"（登记指纹失败：{exc} —— 参考图解析可能会找不到它）"
            if rel != row["output"]:
                from core import run_v34 as R
                params = params_of(cfg, pj, with_script=False)
                built = R.build_tasks(pj, params)
                pj.save_tasks(built)
                rebuilt = len(built.get("asset_tasks") or [])

        pj.log_event({"stage": "manual_image", "kind": kind, "target": key,
                      "result": "ok", "file": rel, "bytes": len(raw),
                      "format": fmt})
        return {"ok": True, "file": rel, "bytes": len(raw), "format": fmt,
                "rebuilt": rebuilt, "note": note,
                "msg": f"{key} 已用手动放入的图（{fmt}，{len(raw) // 1024} KB）。"
                       f"出图那一步看到文件在就会跳过它，不会再生成、也不会再花钱。"}

    if path == "/api/tasks/rebuild":
        pj = proj_of(body)
        meta = pj.meta()
        params = params_of(cfg, pj, with_script=False)
        return {"ok": True, "tasks": S.build_tasks(pj, params)}

    if path == "/api/generate":
        pj = proj_of(body)
        kind = body["kind"]                    # asset | storyboard | video
        pcfg = resolve_provider_cfg(cfg, body.get("provider_sel") or {}, kind)
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
                 "storyboard": ("size",)}[kind]
        ov = _override(body.get("params_override"), allow)
        if ov:
            items = [dict(t, params=dict(t.get("params") or {}, **ov)) for t in items]
        # 未完成数（已存在的会在 worker 里跳过，这里只用于提示）
        todo = sum(1 for t in items if not probe.have_output(pj.p(*t["output"].split("/"))))

        # 手动跑也走优先级链：首选挂了自动换下一家补剩下的
        chain = resolve_chain(cfg, kind, body.get("chain") or [pcfg])
        # 被审核拒绝时用它改写提示词重发。延迟构造 —— 绝大多数批次一次都
        # 不会用到，提前造只是白连一次，还会让没配 LLM 的项目在出图这一步
        # 报「缺 llm_api_key」，方向完全错。
        _soften_llm = lambda: build_llm(cfg)                    # noqa: E731
        parent = JOBS.create(kind, len(items), conc,
                             project_root=pj.root, project_name=os.path.basename(pj.root),
                             provider=chain[0]["provider"], model=chain[0].get("model", ""))

        def go():
            try:
                # 已经出好的资产本身就解除了依赖，所以只对尚未出图的算依赖。
                # 真正的循环会在这里立即报出成员，不会留成「谁都不就绪」的死等。
                pending = [t for t in items if not os.path.isfile(
                    pj.p(*t["output"].split("/")))]
                # 就绪即派：一条的参考图全好了它立刻开跑，不等同层里那条慢的。
                deps = S.asset_deps(pending) if kind == "asset" else {}
                waiting = sum(1 for v in deps.values() if v)
                if waiting:
                    parent.log(kind, f"{len(pending)} 项里有 {waiting} 项要等上游资产 —— "
                                     f"参考图一齐就开跑，不等整层出完")
                r = run_chain(
                        pending, chain=chain, deps_of=deps.get if deps else None,
                        # 分析引擎要传下去：被审核拒绝时靠它改写提示词重发。
                        # 「生产」页单独跑这一步走的是这条路径，漏传的话
                        # 一键跑会自动改写、单独点这一步不会 —— 而两边都不报错。
                        worker_of=lambda p: (
                            S.make_video_worker(pj, p, _soften_llm)
                            if kind == "video"
                            else S.make_image_worker(pj, p, kind, _soften_llm)),
                        job_of=lambda p, n: JOBS.create(
                            kind, n, conc, project_root=pj.root,
                            project_name=os.path.basename(pj.root),
                            provider=p["provider"], model=p.get("model", "")),
                        key_of=lambda t: t["key"],
                        done_of=lambda t: probe.have_output(pj.p(*t["output"].split("/"))),
                        max_retry=retry, log=lambda m: parent.log(kind, m))
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

    if path == "/api/support/bundle":
        """一键打包排错资料。**key 一律不出包**（core/support.redact）。

        「该发哪个文件」用户猜不出来，猜错就是来回好几轮：
        发截图看不出模型回了什么，发产物看不出是哪一步断的。
        """
        import time as _t

        from core import support
        pj = proj_of(body)
        name = f"排错资料_{os.path.basename(pj.root)}_{_t.strftime('%m%d_%H%M')}.zip"
        r = support.bundle(pj.root, cfg, pj.p("07_检查与记录", name))
        return {"ok": True, "name": name, "rel": pj.rel(r["path"]),
                "files": r["files"], "size": r["size"], "missing": r["missing"]}

    if path == "/api/gates":
        """现在有哪几道闸门拦着，各拦了什么，已经授权放行的是哪几道。

        以前拦截文案里写着「去项目设置里显式授权」，但那个地方**根本不存在** ——
        任何一道闸门判定有问题，页面上就走不下去了，只能手改 project.json。
        闸门是我加的，出口忘了开。
        """
        pj = proj_of(body)
        if system_of(pj) != "v34":
            return {"ok": True, "system": "v61", "gates": [], "note": "V6.1 没有这几道闸门"}
        from core import gates_v34 as G
        only = body.get("only_episodes") or None
        auth = ((pj.meta() or {}).get("capability") or {}).get("authorizations") or {}
        blocked = G.check_all(pj, only)
        # 逐道都报：被拦的要给明细，已放行的要显示当初写的理由和时间 ——
        # 放行是会被忘掉的，忘了之后「为什么这一集允许瞬移」就没人答得上。
        out = []
        for gate, label in G.GATES.items():
            # 别在这里再写一份 gate→函数 的表 —— 那份表和 GATES 迟早对不上，
            # 加一道闸门就 KeyError（这条踩过）。唯一来源在 gates_v34.CHECKS。
            problems = G.problems_of(pj, gate, only)
            out.append({"gate": gate, "label": label,
                        "problems": problems,
                        "blocking": gate in blocked,
                        "authorized": auth.get(gate) or None})
        return {"ok": True, "system": "v34", "gates": out,
                "blocking_count": len(blocked)}

    if path == "/api/gates/authorize":
        """显式放行一道闸门，或者撤销放行。

        必须写理由，理由和时间一起写进 meta.capability.authorizations ——
        这是一次改配置的动作，会留在冻结记录里，不是运行时随手点一下跳过。
        """
        pj = proj_of(body)
        from core import gates_v34 as G
        gate = (body.get("gate") or "").strip()
        if body.get("revoke"):
            meta = pj.meta() or {}
            cap = dict(meta.get("capability") or {})
            auth = dict(cap.get("authorizations") or {})
            removed = auth.pop(gate, None)
            cap["authorizations"] = auth
            pj.save_meta(dict(meta, capability=cap))
            return {"ok": True, "revoked": bool(removed)}
        return {"ok": True, "authorized": G.authorize(pj, gate, body.get("why", ""))}

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
        params = params_of(cfg, pj, with_script=False)
        # 手动拼接也要对账「这一集该有几段」—— 缺段的成片不能交付。
        # 段的出处按体系分：电影级看第十环节装的箱子，通用级看环节2 划的段。
        if system_of(pj) == "v34":
            from core import run_v34 as _R
            want = lambda p, ep: [x["seg_id"] for x in _R.segments_of(p, ep)]
        else:
            want = S.segment_ids
        return {"ok": True,
                "result": S.assemble(pj, params, expect_segs=want,
                                     episode=(body.get("episode") or "").strip())}

    if path == "/api/selftest":
        out = {}
        for pid, pc in (cfg.get("providers") or {}).items():
            capabilities = (("image_1k", "image_4k", "video") if pid == "chaomo"
                            else ("image",))
            for capability in capabilities:
                name = f"{pid}/{capability}" if pid == "chaomo" else pid
                key = _provider_api_key(pid, pc, capability)
                if not key:
                    out[name] = {"ok": False,
                                 "msg": f"未配置{_KEY_LABELS[capability]} Key"}
                    continue
                try:
                    models = build_provider(pid, key, pc.get("base_url", "")).list_models()
                    out[name] = {"ok": bool(models), "count": len(models),
                                 "msg": f"{len(models)} 个模型" if models else "拉取失败"}
                except Exception as exc:                 # noqa: BLE001
                    out[name] = {"ok": False, "msg": str(exc)[:150]}
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


def serve(host: str = "127.0.0.1", port: int = 0, tries: int = 20):
    """起服务。端口被占就顺延，不是直接崩。

    两套体系是两个包、要同时开着，各自有默认端口；但用户也可能已经
    开了别的东西占住那个口。崩掉的话人只看到一串 traceback，
    还得自己想到「换个端口」——顺延一个继续跑，并把真实端口打出来就行。

    返回 (srv, 实际端口)：调用方要用真实端口拼 URL，
    否则浏览器会打开一个没人在听的地址。
    """
    port = port or build_info.default_port()
    last = None
    for i in range(max(1, tries)):
        try:
            srv = ThreadingHTTPServer((host, port + i), Handler)
        except OSError as exc:
            last = exc
            continue
        srv.daemon_threads = True
        return srv, port + i
    raise OSError(f"{port}–{port + tries - 1} 都被占了，用 --port 指一个空的") from last
