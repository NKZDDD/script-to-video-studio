# -*- coding: utf-8 -*-
"""错误诊断中枢：把任意异常翻译成「问题是什么 / 改哪里 / 改完怎么继续」。

设计目标：
  1. 每条错误都有 code、人话标题、具体修复步骤、修复位置
  2. 失败记录**落盘**（07_检查与记录/failures.json），重启服务不丢
  3. 明确标注 resumable —— 修完之后能不能直接续跑
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from .store import LOCK, read_json, write_json

# ---------------------------------------------------------------- 错误目录
# where: 在页面哪里改   fix: 具体动作（按顺序）   resume: 修完怎么继续
CATALOG = {
    "QUOTA_EXHAUSTED": {
        "title": "服务商余额/额度不足",
        "why": "接口返回余额、配额或计费相关错误，本批已熔断，剩余任务未提交（没有继续烧钱）。",
        "where": "服务商后台充值 → 回到「设置 → 服务商」确认 key 未变",
        "fix": ["登录该服务商后台确认余额/额度",
                "充值，或在「生产」页把这一类任务改用另一家服务商",
                "如果是子账号额度用尽，换一个有额度的 key"],
        "resume": "回「生产」页点同一行的「开始」——已完成的会自动跳过，只补没做的",
        "resumable": True, "scope": "batch",
    },
    "AUTH_INVALID": {
        "title": "API Key 无效或已过期",
        "why": "鉴权被拒（401/无效令牌）。整批已停止。",
        "where": "设置 → 服务商 → 对应那家的 API Key",
        "fix": ["检查 key 有没有多余空格、换行、引号",
                "确认 key 属于当前 Base URL 这家中转站（跨站 key 不通用）",
                "在服务商后台重新生成 key 并粘贴保存",
                "保存后点「运行自检」，该家应显示模型数量"],
        "resume": "自检通过后回「生产」页重新点「开始」",
        "resumable": True, "scope": "batch",
    },
    "ACCOUNT_BANNED": {
        "title": "账号被禁用或无可用渠道",
        "why": "服务商侧账号异常，或该模型没有可用上游渠道。",
        "where": "设置 → 服务商 / 生产页的模型选择",
        "fix": ["联系服务商确认账号状态",
                "换一个模型试试（同一家常有多个上游渠道）",
                "或整体换另一家服务商"],
        "resume": "换好之后回「生产」页点「开始」",
        "resumable": True, "scope": "batch",
    },
    "RATE_LIMITED": {
        "title": "触发限流，重试后仍失败",
        "why": "短时间请求太多。程序已按 Retry-After 退避重试过，仍未通过。",
        "where": "设置 → 并发闸门",
        "fix": ["把该服务商的「配额」调小（例如 6 → 2）",
                "把「全局在途上限」调小",
                "生产页该行的「并发」也调小到 1-2",
                "等几分钟让限流窗口过去"],
        "resume": "调小并发后点「开始」，只补失败的任务",
        "resumable": True, "scope": "task",
    },
    "CONTENT_REJECTED": {
        "title": "内容被审核拦截",
        "why": "提示词触发了平台的内容安全策略。这一个任务失败，其它任务不受影响。",
        "where": "对应的提示词文件（03_提示词/ 下）",
        "fix": ["打开失败任务对应的提示词文件",
                "按 skill 的审核三级适配改写：① 忠实制作 → ② 镜头降敏（动作准备→硬切→动作结果）→ ③ 结果叙事",
                "关键：不要改施动者、动作对象、动作结果和剧情因果",
                "改完保存文件即可（程序读的是磁盘上的 txt）"],
        "resume": "改完提示词后点「开始」，只会重跑这一个失败的段",
        "resumable": True, "scope": "task",
    },
    "PROMPT_INVALID": {
        "title": "提示词或参数不合法",
        "why": "接口拒绝了请求参数（过长、字段不支持、尺寸非法等）。",
        "where": "对应提示词文件 / 生产页的模型与参数",
        "fix": ["检查提示词是否过长（部分模型有字数上限）",
                "检查图片尺寸、时长、画幅是否是该模型支持的值（见设置页服务商能力说明）",
                "换一个模型试试"],
        "resume": "改完后点「开始」补这一个任务",
        "resumable": True, "scope": "task",
    },
    "MODEL_NOT_FOUND": {
        "title": "模型不存在或未开通",
        "why": "该服务商没有这个模型，或你的账号没有权限。",
        "where": "生产页 → 该行的「模型」下拉",
        "fix": ["在设置页点「运行自检」看该家实际有哪些模型",
                "换一个存在的模型",
                "模型清单以服务商 /v1/models 返回为准，下拉里的是内置默认值，可能过时"],
        "resume": "换模型后点「开始」",
        "resumable": True, "scope": "batch",
    },
    "REF_MISSING": {
        "title": "参考图文件不存在",
        "why": "任务要用的资产图或故事板还没生成，或被删了。",
        "where": "生产页的执行顺序",
        "fix": ["按顺序生产：资产图 → 故事板 → 分段视频",
                "先把上一环节缺的补齐（在「产物」页可以看已有哪些）",
                "如果文件被误删，重跑对应环节即可"],
        "resume": "补齐上游后点「开始」",
        "resumable": True, "scope": "task",
    },
    "PREREQ_MISSING": {
        "title": "缺少前置环节的产物",
        "why": "这个环节依赖前面环节的输出，但那些文件还不存在。",
        "where": "流程页",
        "fix": ["按环节编号从小到大依次执行",
                "缺哪个环节，报错里已列出"],
        "resume": "跑完前置环节后回来重跑本环节",
        "resumable": True, "scope": "stage",
    },
    "LLM_SCHEMA_FAIL": {
        "title": "分析引擎输出格式不合规",
        "why": "LLM 连续多次没有产出符合 schema 的 JSON。通常是模型能力不足或剧本过长。",
        "where": "设置 → 分析引擎",
        "fix": ["换一个更强的模型（如 claude-sonnet-5 / claude-opus-4-8 / gpt-5.5）",
                "剧本特别长时，先拆成单集再处理",
                "如果反复失败，看日志里模型实际返回了什么——可能是被审核拦截返回了拒绝语"],
        "resume": "换模型后回「流程」页重跑该环节",
        "resumable": True, "scope": "stage",
    },
    "LLM_EMPTY": {
        "title": "分析引擎返回空内容",
        "why": "模型没有产出任何文本。可能是内容被拒、上下文超限或上游异常。",
        "where": "设置 → 分析引擎 / 剧本内容",
        "fix": ["换模型重试",
                "检查剧本是否含大量敏感内容导致整体被拒",
                "剧本过长时先拆集"],
        "resume": "回「流程」页重跑该环节",
        "resumable": True, "scope": "stage",
    },
    "NETWORK": {
        "title": "网络连接失败",
        "why": "请求没能到达服务商。已按退避重试过。",
        "where": "设置 → 服务商 → Base URL / 代理",
        "fix": ["检查本机网络与 VPN/代理",
                "确认 Base URL 拼写正确、可访问",
                "需要代理时在服务商配置里填 proxy"],
        "resume": "网络恢复后点「开始」，只补未完成的",
        "resumable": True, "scope": "batch",
    },
    "TIMEOUT": {
        "title": "任务超时",
        "why": "轮询到达上限仍未拿到结果。任务可能仍在服务商侧排队。",
        "where": "设置 → 分析引擎/服务商 的超时配置",
        "fix": ["视频生成高峰期可能很慢，先等几分钟再重试",
                "调大 poll_timeout（config.json 里 video_poll_timeout）",
                "或换更快的模型"],
        "resume": "点「开始」重试该任务",
        "resumable": True, "scope": "task",
    },
    "DISK": {
        "title": "文件读写失败",
        "why": "落盘或读取文件出错。",
        "where": "项目目录",
        "fix": ["检查磁盘空间", "确认项目目录未被其它程序占用（如正在预览的视频）",
                "确认路径没有被杀软/同步盘锁定"],
        "resume": "解决后点「开始」",
        "resumable": True, "scope": "task",
    },
    "UNKNOWN": {
        "title": "未分类错误",
        "why": "没有匹配到已知错误模式，请看下方原始报错。",
        "where": "—",
        "fix": ["查看原始报错内容",
                "如果是服务商返回的业务错误，通常报错里会写明原因",
                "可在设置页运行自检排除配置问题"],
        "resume": "排查后点「开始」重试",
        "resumable": True, "scope": "task",
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


def build(exc: Any, *, stage: str = "", target: str = "", provider: str = "",
          model: str = "", extra_fix: Optional[list] = None) -> dict:
    """异常 → 结构化诊断。这是给人看的那一份。"""
    msg = str(exc)
    status = getattr(exc, "status", 0) or 0
    code = code_of(msg, status)
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
        "stage": stage,
        "target": target,
        "provider": provider,
        "model": model,
        "status": status,
        "raw": msg[:800],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def one_line(d: dict) -> str:
    """给日志/任务卡用的一行摘要。"""
    return f"[{d['code']}] {d['title']}｜改这里：{d['where']}"


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
    """按错误码聚合，给「续跑面板」用。"""
    items = load(project_root)
    by_code: dict = {}
    for it in items:
        by_code.setdefault(it["code"], {"code": it["code"], "title": it["title"],
                                        "where": it["where"], "fix": it["fix"],
                                        "resume": it["resume"], "scope": it["scope"],
                                        "targets": []})
        by_code[it["code"]]["targets"].append(it.get("target") or it.get("stage"))
    return {"total": len(items), "groups": list(by_code.values())}
