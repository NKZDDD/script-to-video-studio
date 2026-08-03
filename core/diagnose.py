# -*- coding: utf-8 -*-
"""错误诊断中枢：把任意异常翻译成「出了什么事 / 去哪改 / 改完怎么接着跑」。

写文案的规矩（很重要，改这个文件时请照做）：
  · 说人话。不写「熔断」「鉴权」「上游」「schema」「轮询」这类词，
    读的人可能第一次接触这些概念，看不懂就等于没提示。
  · 「去哪改」要能照着点：写页面上真实存在的按钮名、真实的文件夹名。
  · 「怎么改」是有先后顺序的动作，一步一句，不是并列的建议。
  · 「怎么接着跑」必须回答「我改完了，然后呢」，并说清会不会重复花钱。

另外两条机制性的要求：
  1. 失败记录写到文件里（07_检查与记录/failures.json），关掉程序也不丢
  2. 每条都标明还能不能接着跑（resumable）、影响范围多大（scope）
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from .store import LOCK, read_json, write_json

# ---------------------------------------------------------------- 错误目录
# title 出了什么事  why 为什么会这样  where 去哪改  fix 怎么改  resume 改完怎么接着跑
# level: error=没做出来  warn=东西做出来了但可能不对，不挡后面的流程
CATALOG = {
    "QUOTA_EXHAUSTED": {
        "title": "这家服务商的账户没钱了",
        "why": "服务商说你的余额或套餐额度不够了。程序已经自己停下来，"
               "后面还没做的任务一个都没发出去，不会继续扣钱。",
        "where": "先去服务商网站充值，再回「设置 → 服务商」看一眼 key 没被改过",
        "fix": ["打开这家服务商的网站，登录进去看余额还剩多少",
                "充值；不想充的话，去「生产」页把这批任务改成用另一家来做",
                "如果用的是别人分给你的 key，可能是这个 key 自己的额度用完了，换一个 key"],
        "resume": "充完钱回「生产」页，点这一行的「开始」。"
                  "已经做好的会自动跳过，只补没做完的，不会重复花钱。",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "AUTH_INVALID": {
        "title": "API Key 不对，服务商不认",
        "why": "服务商拒收了你的 key。可能是填错了、过期了，也可能这个 key 是另一家的。"
               "这批任务已经全部停下。",
        "where": "设置 → 服务商 → 这一家的「API Key」输入框",
        "fix": ["把 key 重新复制粘贴一遍，注意前后别带空格、换行或引号",
                "确认这个 key 是「Base URL」填的那家网站发给你的——不同家的 key 不通用",
                "还不行就去服务商网站重新生成一个，粘过来保存",
                "保存后点「运行自检」，正常的话这家会显示出有多少个模型能用"],
        "resume": "自检能看到模型数量了，就回「生产」页重新点「开始」",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "ACCOUNT_BANNED": {
        "title": "账号用不了，或者这个模型暂时没货",
        "why": "服务商那边说你的账号有问题；也可能是这个模型现在没有可用的线路。",
        "where": "设置 → 服务商；或者「生产」页这一行的模型下拉框",
        "fix": ["找服务商的客服问一下账号状态",
                "换一个模型试试——同一家往往接了好几条线路，这条不通换一条就好",
                "都不行就整体换一家服务商"],
        "resume": "换好之后回「生产」页点「开始」",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "RATE_LIMITED": {
        "title": "发得太快，被服务商拦了",
        "why": "短时间里发的请求太多，超过了这家允许的速度。"
               "程序已经自动等了一会儿又重试过几次，还是没过去。",
        "where": "设置 → 「同时最多跑几个」那一块",
        "fix": ["把这家服务商的上限调小，比如从 6 改成 2",
                "「总上限（所有项目合计）」也跟着调小一点",
                "「生产」页这一行的「并发」改成 1 或 2",
                "然后等几分钟再开始——这种限制一般过几分钟自己就解除了"],
        "resume": "调小之后点「开始」，只会补没做完的那些",
        "resumable": True, "scope": "task", "level": "error",
    },
    "CONTENT_REJECTED": {
        "title": "提示词被平台的内容审核挡下了",
        "why": "这段提示词里写了平台不允许的内容。只有这一条失败，其它的照常在跑。",
        "where": "这一条对应的提示词文件，在项目文件夹的 03_提示词/ 里面",
        "fix": ["打开失败这一条对应的提示词文件（上面「影响范围」里写了是哪一条）",
                "按三步改，哪一步过了就停："
                "① 先只把露骨的词换成中性说法；"
                "② 还不过，就把动作过程整段删掉，只写「动作要开始的样子」和「动作已经结束的样子」，中间直接切；"
                "③ 再不过，就一点动作都不写，改成写造成的结果和旁人的反应",
                "有一条底线不能破：谁做的、对谁做的、造成了什么后果、前后因果——"
                "这四样必须原样保留，只许改「怎么描述」，不许改「发生了什么」",
                "改完保存文件就行，程序直接读硬盘上的文件，不用重新导入"],
        "resume": "改完保存，回「生产」页点「开始」，只会重跑这一条，别的不动",
        "resumable": True, "scope": "task", "level": "error",
    },
    "PROMPT_INVALID": {
        "title": "这次发过去的内容，服务商不收",
        "why": "可能是提示词太长，也可能是尺寸、时长、画面比例填了这个模型不支持的值。",
        "where": "对应的提示词文件；或者「生产」页上的模型和参数",
        "fix": ["看看提示词是不是太长了——有些模型对字数有上限",
                "看看画面比例、时长、分辨率是不是这个模型支持的"
                "（「设置」页每家服务商下面都写了支持哪些）",
                "换一个模型试试"],
        "resume": "改完点「开始」，补这一条",
        "resumable": True, "scope": "task", "level": "error",
    },
    "MODEL_NOT_FOUND": {
        "title": "找不到这个模型",
        "why": "这家服务商没有这个模型，或者你的账号还没开通它。",
        "where": "「生产」页 → 这一行的「模型」下拉框",
        "fix": ["去「设置」页点「运行自检」，能看到这家实际有哪些模型",
                "从查出来的清单里挑一个换上",
                "下拉框里的是程序内置的默认清单，可能已经过时了——以自检查出来的为准"],
        "resume": "换完模型点「开始」",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "REF_MISSING": {
        "title": "要当参考的那张图还没生成",
        "why": "这一步要拿前面做好的资产图或故事板当参考，但那个文件现在不在。"
               "可能是还没做，也可能是被删了。",
        "where": "「生产」页的执行顺序",
        "fix": ["按顺序来：先做资产图，再做故事板，最后做视频",
                "去「产物」页看看缺的是哪一张，把那一步先补上",
                "如果是不小心删掉了，把对应那一步重跑一遍就有了"],
        "resume": "前面的补齐之后，回来点「开始」",
        "resumable": True, "scope": "task", "level": "error",
    },
    "EPISODE_REQUIRED": {
        "title": "得先选一集，这个环节是按集跑的",
        "why": "除了环节1，其它环节都是一集一集处理的。这部剧有好几集，"
               "程序不知道你要跑哪一集，所以停下来问你。",
        "where": "「流程」页最上面「当前操作的集」那个下拉框",
        "fix": ["在「流程」页顶部的下拉框里选一集，再点这个环节的「执行」",
                "想把所有集都跑一遍的话，点这个环节旁边的「全部集依次跑」——"
                "它会从第1集开始挨个跑，已经跑过的自动跳过",
                "如果下拉框里是空的，说明环节1 还没跑；先跑环节1，它会判断这部剧有几集"],
        "resume": "选好集（或点「全部集依次跑」）之后，重新点这个环节的「执行」",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "EPISODE_SPLIT_FAILED": {
        "title": "集没切出来，或者某一集是空的",
        "why": "环节1 要给出每一集正文的第一行原文，程序拿它去剧本里定位、切分。"
               "现在有的行在剧本里找不到，所以那几集切不出内容来。"
               "常见原因：模型没照原文抄，自己改写或翻译了。",
        "where": "「流程」页最上面的「这部剧有几集」，那里列出了具体是哪几集有问题",
        "fix": ["先看「这部剧有几集」里的黄色提示，它写了具体哪一集、什么原因",
                "重跑一次环节1——同一个模型换一次通常就好了",
                "还不行就换个更强的模型再跑环节1（设置 → 分析引擎）",
                "剧本里如果集与集之间完全没有可辨认的分隔（连标题都没有），"
                "那就手动把剧本拆成一集一个文件，在「项目」页分别上传"],
        "resume": "重跑环节1，切出来之后再往下跑",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "PREREQ_MISSING": {
        "title": "前面的环节还没跑完",
        "why": "这个环节要用前面环节做出来的文件，但那些文件还不存在。",
        "where": "「流程」页",
        "fix": ["环节有先后顺序，从小号往大号一个一个跑",
                "具体缺哪个环节，上面的报错里已经写了名字"],
        "resume": "把缺的环节跑完，再回来跑这一个",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "LLM_SCHEMA_FAIL": {
        "title": "拆剧本的模型没按格式回答",
        "why": "程序要求模型按固定格式回答，好让后面的步骤能读。它连着几次都没照做。"
               "一般是这个模型能力不太够，或者一次喂给它的剧本太长了。",
        "where": "设置 → 分析引擎",
        "fix": ["换一个更强的模型（比如 claude-sonnet-5、claude-opus-4-8、gpt-5.5）",
                "剧本特别长的话，先拆成一集一集分开处理",
                "老是失败的话，看一眼日志里模型到底回了什么——"
                "有时候是内容被审核挡了，它回的其实是一句拒绝的话"],
        "resume": "换完模型回「流程」页，重跑这个环节",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "LLM_EMPTY": {
        "title": "拆剧本的模型什么都没回",
        "why": "模型返回了一片空白。可能是剧本内容被它拒绝了，"
               "可能是一次给的字太多超过了它能读的上限，也可能是服务商那边出了问题。",
        "where": "设置 → 分析引擎；或者剧本内容本身",
        "fix": ["先换个模型重试一次",
                "换了还是空的，看看剧本里是不是有大段敏感内容，导致整篇被拒",
                "剧本很长的话，拆成单集再跑"],
        "resume": "回「流程」页重跑这个环节",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "NETWORK": {
        "title": "连不上服务商",
        "why": "请求没能发出去，或者发到一半断了。程序已经自动重试过几次。",
        "where": "设置 → 服务商 → Base URL / 代理",
        "fix": ["先看本机能不能正常上网；用了 VPN 的话检查一下 VPN",
                "确认 Base URL 没写错，复制到浏览器里能打开",
                "如果这家要走代理才能访问，在这家的配置里把 proxy 填上"],
        "resume": "网络恢复之后点「开始」，只补没做完的",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "TIMEOUT": {
        "title": "等太久了还没出结果",
        "why": "程序一直在问服务商「好了没」，问到设定的最长等待时间还是没结果。"
               "这个任务可能还在服务商那边排队。",
        "where": "设置 → 分析引擎 / 服务商 的超时设置",
        "fix": ["视频高峰期确实会很慢，先等几分钟再重试一次",
                "如果经常超时，把等待上限调大（config.json 里的 video_poll_timeout）",
                "或者换一个出得快一些的模型"],
        "resume": "点「开始」重试这一条",
        "resumable": True, "scope": "task", "level": "error",
    },
    "DISK": {
        "title": "文件存不下来，或者读不出来",
        "why": "往硬盘写文件或者读文件的时候出错了。",
        "where": "项目文件夹",
        "fix": ["看看硬盘还有没有剩余空间",
                "看看这个文件是不是正被别的程序占着——最常见的是你正在播放器里预览它",
                "如果项目放在 OneDrive、坚果云这类同步盘里，同步过程也会占用文件，先暂停同步"],
        "resume": "解决之后点「开始」",
        "resumable": True, "scope": "task", "level": "error",
    },
    "WRONG_RATIO": {
        "title": "做出来的画面比例，跟你要的不一样",
        "why": "文件已经出来了，服务商也没报错，但程序量了一下实际尺寸，"
               "跟你选的比例对不上。多半是这家没按你要求的比例出，用了它自己的默认值"
               "（最常见的是：你要竖屏，它给了横屏）。",
        "where": "「生产」页这一行的「画面比例」；或者干脆换一家服务商",
        "fix": ["先去「产物」页点开看一眼，确认是不是真的躺倒了",
                "确实不对的话：在「产物」页把这个文件删掉，换一家服务商重做这一条",
                "如果这一批全都是这个毛病，说明这家不认程序发过去的比例设置，整批换一家",
                "如果你看了觉得这样也能用，那就不用管这条，不影响后面拼接"],
        "resume": "想重做的话，得先在「产物」页把这个文件删掉再点「开始」——"
                  "文件还在的话，程序会认为这条已经做好了，直接跳过。",
        "resumable": True, "scope": "task", "level": "warn",
    },
    "UNKNOWN": {
        "title": "没见过的错误",
        "why": "这条报错没对上任何已知的类型。具体内容看下面的「原始报错」。",
        "where": "—",
        "fix": ["先看下面的「原始报错」，服务商返回的错误里一般会直接写原因",
                "去「设置」页点「运行自检」，先排除掉配置填错的可能",
                "如果看不懂，把「原始报错」整段复制出来——"
                "这类没认出来的报错需要补进识别规则里，下次才能给出准确指引"],
        "resume": "查清楚之后点「开始」重试",
        "resumable": True, "scope": "task", "level": "error",
    },
}

# 原始报错 → 错误码（按顺序匹配，先命中先用）
_PATTERNS = [
    ("QUOTA_EXHAUSTED", r"insufficient|quota|余额|额度|欠费|balance|billing|payment|credit|arrears"),
    ("AUTH_INVALID", r"invalid[_ ]api[_ ]key|incorrect api key|unauthorized|authentication|令牌|token 不正确|无效的?密钥"),
    ("ACCOUNT_BANNED", r"banned|封禁|禁用|账户异常|无可用渠道|no available channel|suspend"),
    ("MODEL_NOT_FOUND", r"model.*not (found|exist)|不存在的?模型|unsupported model|无此模型"),
    ("RATE_LIMITED", r"429|rate limit|too many request|限流|请求过于频繁"),
    ("CONTENT_REJECTED", r"content policy|safety|violat|prohibited|sensitive|审核|违规|敏感"),
    ("PROMPT_INVALID", r"prompt too long|too long|invalid (prompt|param|size|request)|参数错误|不支持的?(尺寸|时长|比例)"),
    ("REF_MISSING", r"参考图文件不存在|固定故事板不存在|no such file|filenotfound"),
    # 这两条要排在 PREREQ_MISSING 前面：它们的文案里也带「请先跑」，
    # 但给的指引更具体（去哪个下拉框选集），别被通用的前置缺失兜走
    ("EPISODE_REQUIRED", r"得指定跑哪一集|没有 EP\d+ 这一集|还没切集"),
    ("EPISODE_SPLIT_FAILED", r"的正文是空的|一集都没切出来|找不到这一行"),
    ("PREREQ_MISSING", r"缺少前置产物|请先跑"),
    ("LLM_SCHEMA_FAIL", r"JSON 输出校验失败|输出缺少必需字段|未找到可解析的 JSON"),
    ("LLM_EMPTY", r"回复内容为空|无 choices"),
    ("TIMEOUT", r"超时|timed? ?out"),
    ("NETWORK", r"网络错误|connection|dns|ssl|proxy|unreachable"),
    ("DISK", r"permission denied|being used by another process|no space|disk"),
]


def code_of(message: str, status: int = 0) -> str:
    low = (message or "").lower()
    if status in (401,):
        return "AUTH_INVALID"
    if status in (402,):
        return "QUOTA_EXHAUSTED"
    if status in (403,):
        return "ACCOUNT_BANNED"
    for code, pat in _PATTERNS:
        if re.search(pat, low, re.I):
            return code
    if status == 429:
        return "RATE_LIMITED"
    if status in (400, 422):
        return "PROMPT_INVALID"
    return "UNKNOWN"


def _entry(code: str, *, stage: str, target: str, provider: str, model: str,
           status: int, raw: str, extra_fix: Optional[list] = None) -> dict:
    c = CATALOG.get(code, CATALOG["UNKNOWN"])
    return {
        "code": code,
        "title": c["title"],
        "why": c["why"],
        "where": c["where"],
        "fix": list(c["fix"]) + list(extra_fix or []),
        "resume": c["resume"],
        "resumable": c["resumable"],
        "scope": c["scope"],
        "level": c.get("level", "error"),
        "stage": stage,
        "target": target,
        "provider": provider,
        "model": model,
        "status": status,
        "raw": raw[:800],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def build(exc: Any, *, stage: str = "", target: str = "", provider: str = "",
          model: str = "", extra_fix: Optional[list] = None) -> dict:
    """异常 → 结构化诊断。这是给人看的那一份。"""
    msg = str(exc)
    status = getattr(exc, "status", 0) or 0
    return _entry(code_of(msg, status), stage=stage, target=target, provider=provider,
                  model=model, status=status, raw=msg, extra_fix=extra_fix)


def warn(code: str, raw: str, *, stage: str = "", target: str = "", provider: str = "",
         model: str = "", extra_fix: Optional[list] = None) -> dict:
    """提醒级：东西做出来了，但可能不对。不算失败，不挡后面的流程。"""
    return _entry(code, stage=stage, target=target, provider=provider, model=model,
                  status=0, raw=raw, extra_fix=extra_fix)


def one_line(d: dict) -> str:
    """给日志和任务卡用的一行摘要。不带错误码——那是给我排查用的，不是给用户看的。"""
    where = d.get("where") or ""
    tail = f"｜去哪改：{where}" if where and where != "—" else ""
    return f"{d['title']}{tail}"


# ---------------------------------------------------------------- 落盘
# 内存里的 job 重启就没了；失败必须落盘，否则「停了之后怎么继续」无从谈起。

def failures_path(project_root: str) -> str:
    return os.path.join(project_root, "07_检查与记录", "failures.json")


def record(project_root: str, diag: dict) -> None:
    """记一条失败。同 stage+target 的旧记录会被覆盖（只保留最新一次）。

    读-改-写整段加锁：多线程同时失败时必须串行，否则会互相覆盖。
    """
    if not project_root:
        return
    with LOCK:
        p = failures_path(project_root)
        items = read_json(p, []) or []
        key = (diag.get("stage"), diag.get("target"))
        items = [x for x in items if (x.get("stage"), x.get("target")) != key]
        items.append(diag)
        write_json(p, items[-300:])


def clear(project_root: str, stage: str = "", target: str = "") -> int:
    """任务成功后清掉对应的失败记录。返回清掉几条。"""
    if not project_root:
        return 0
    with LOCK:
        p = failures_path(project_root)
        if not os.path.isfile(p):
            return 0
        items = read_json(p, []) or []
        before = len(items)
        if not stage and not target:
            items = []
        else:
            items = [x for x in items
                     if not ((not stage or x.get("stage") == stage)
                             and (not target or x.get("target") == target))]
        if len(items) != before:
            write_json(p, items)
        return before - len(items)


def load(project_root: str) -> list:
    return read_json(failures_path(project_root), []) or []


def summary(project_root: str) -> dict:
    """按错误码聚合，给「续跑面板」用。同一个毛病影响 20 条时，只显示一张卡。

    报错先排、提醒后排——真正卡住流程的要先看到。
    """
    items = load(project_root)
    by_code: dict = {}
    for it in items:
        g = by_code.setdefault(it["code"], {
            "code": it["code"], "title": it["title"], "why": it.get("why", ""),
            "where": it["where"], "fix": it["fix"], "resume": it["resume"],
            "scope": it["scope"], "level": it.get("level", "error"),
            "raw": it.get("raw", ""), "targets": [],
        })
        g["targets"].append(it.get("target") or it.get("stage"))
    groups = sorted(by_code.values(), key=lambda g: (g["level"] != "error", g["code"]))
    errors = sum(len(g["targets"]) for g in groups if g["level"] == "error")
    return {"total": len(items), "errors": errors, "warns": len(items) - errors,
            "groups": groups}
