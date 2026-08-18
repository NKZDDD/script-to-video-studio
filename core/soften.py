# -*- coding: utf-8 -*-
"""被审核拦下时，把提示词交给分析引擎改写一遍再试。

短剧里打人、流血、伤口是常规戏。出图出片这一端的审核比文字端严得多，
同一段剧情，文字环节全程没问题，到出图就被拒 —— 而被拒的后果是**这一段
没有画面**，成片直接缺一块，前面十几个环节全白跑。

人工的做法就是把那段提示词改一改再发。这里把它自动化：拿服务商的原话
（它通常会说踩了哪一类）连同提示词一起给分析引擎，让它**只改呈现方式，
不改剧情**，然后重发。

## 这件事最大的风险不是改不动，是改过头

「优化血腥暴力」和「把这段戏删掉」之间没有天然的界线，而模型偏向后者 ——
它更省事，也更容易过审。用户已经在别处撞过同一个毛病（V6.0 太精简，
把故事板吃了）。改过头的后果是**不报错**：图出来了、任务标 ok、
成片是完整的，只是那一场戏没了。

所以改写完必须**验一遍**再用，见 `_check`。验不过就不用它、照常报原来的错 ——
宁可这一条失败让人自己改，也不能悄悄把戏改没。
"""

from __future__ import annotations

import re
import time
from typing import Callable, Optional

from . import diagnose
from .store import Project, write_text

# 改写几轮。0 = 关掉这个功能。设置页里可以改（「被审核拒绝后改写重试」）。
#
# 每一轮都接着**上一轮改完的那版**继续改，不是每次都从原文重来 ——
# 从原文重来等于把上一轮的进展扔掉，模型多半会给出差不多的东西。
#
# 不设上限：填多少就跑多少。真正兜底的是验收那一关（见 `_check`）——
# 长度和身份映射始终对着**最初的原文**比，所以轮数再多也蚕食不动。
DEFAULT_ROUNDS = 5

# 提示词里的身份映射行 `Image 1 = C001 名称`。**改写后必须原样保留**：
# 少一行或者编号变了，出图那边会把另一个角色的脸套上去，而这不报错。
_IMAGE_MAP = re.compile(r"[Ii]mage\s*(\d+)\s*[=＝:：]\s*([A-Za-z0-9_\-]+)")

# 改写后正文至少要保留**最初原文**的多少。低于这个数说明它在删内容。
_MIN_KEEP = 0.6
# 每一轮相对**上一版**至少要保留多少。多轮跑时这一条更早发现问题：
# 只看对原文的比例，前几轮一点点少是看不出来的 —— 要等崩到底才拦。
_MIN_STEP = 0.85


def is_content_rejection(exc: Exception, prompt: str = "") -> bool:
    """这次失败是被审核拦下的吗。

    用 diagnose 的那套规则判，不自己再写一份关键词表 ——
    两份表迟早对不上，而对不上的表现是「有时候会自动改写、有时候不会」，
    比不做还难查。那套规则**先看服务商给的错误码**，拿不到码才退回读文案。

    **判之前把回显的提示词剔掉。** 不少服务商会把整段提示词原样贴回报错里，
    而剧本里「他违反了约定」「这是一份社区规范」这种句子会命中判词 ——
    于是一个网络错误被当成内容审核：不再重试，还白跑几轮改写。
    我们手上正好有这一段提示词，剔掉是确定性的，不用靠猜。
    """
    return diagnose.code_of(_strip_echo(str(exc), prompt),
                            getattr(exc, "status", 0) or 0,
                            getattr(exc, "err_code", "")) == "CONTENT_REJECTED"


def _strip_echo(msg: str, prompt: str) -> str:
    """报错里回显的提示词段落去掉，只留服务商自己说的话。

    整段直接命中最常见（原样回显）；此外按行剔 —— 有的家会把提示词
    截断或换行重排，整段对不上，但一行一行还是对得上的。
    """
    if not prompt or not msg:
        return msg
    out = msg.replace(prompt.strip(), " ")
    for line in prompt.splitlines():
        line = line.strip()
        if len(line) >= 12 and line in out:      # 太短的行容易误伤，不剔
            out = out.replace(line, " ")
    return out


def _looks_like_fragment(origin: str, new: str) -> bool:
    """回来的像是「只有改动的那一段」，而不是完整提示词。

    分得开这两件事很重要：片段是**格式问题**（重问一次就好），
    删内容是**它想绕过审核**（只能人工改）。以前两种都报「把内容删掉了」，
    于是人去查一个不存在的问题。

    判据用身份映射行：`Image N = ID` 是提示词的骨架，一段片段里不会有它们。
    原文本来就没有这几行时（有些资产提示词没有参考图）不敢下结论，
    宁可当成删内容 —— 那一条的处理更保守。
    """
    return bool(_IMAGE_MAP.findall(origin)) and not _IMAGE_MAP.findall(new)


def _check(origin: str, prev: str, new: str) -> str:
    """改写结果能不能用。返回问题描述，空字符串 = 能用。

    每一条都对应一种「不报错的坏结果」：
      · 空 / 没变      → 白花一次调用，还会让人以为改过了
      · 短太多         → 它在删戏，不是在换措辞
      · 身份映射对不上 → 出图会把别人的脸套上去

    **长度和身份映射一律对着最初的原文比，不是上一轮。**
    对着上一轮比的话会被逐轮蚕食：每轮各留 60%，五轮下来只剩 7.8%，
    而每一步单看都合格 —— 这正是「不报错、只是少」最典型的长相。
    「和上一版一样」这一条才对着上一轮比：它问的是「这一轮有没有推进」。
    """
    new = (new or "").strip()
    if not new:
        return "改写回来是空的"
    if new == (prev or origin).strip():
        return "改写回来和上一版一模一样，没有推进"
    if len(new) < len(origin) * _MIN_KEEP:
        # 短得多有**两种**原因，报错必须分开 —— 修法完全不同：
        #   只回了改动的片段 → 是格式没说清，重问一次就好
        #   真把内容删掉了   → 是它想绕过审核，只能人工改
        # 混成一句「把内容删掉了」的话，人会去查一个不存在的问题。
        if _looks_like_fragment(origin, new):
            return (f"改写只回了 {len(new)} 字（原文 {len(origin)} 字），"
                    f"而且身份映射那几行整块不见了 —— 它多半只回了**改动的那一段**，"
                    f"不是完整提示词")
        return (f"改写后只剩 {len(new)} 字，最初的原文 {len(origin)} 字 —— "
                f"少了 {100 - len(new) * 100 // max(1, len(origin))}%，"
                f"这是把内容删掉了，不是换措辞")
    # 每一轮自己也不许缩水。只看「对着原文 60%」的话，前几轮一点点少
    # 是发现不了的 —— 要等崩到底才拦，而那时候已经白跑了几轮。
    if prev and len(new) < len(prev.strip()) * _MIN_STEP:
        return (f"这一版 {len(new)} 字，上一版 {len(prev.strip())} 字 —— "
                f"又少了 {100 - len(new) * 100 // max(1, len(prev.strip()))}%。"
                f"改写是换角度，不是一轮比一轮淡")
    was, now = _IMAGE_MAP.findall(origin), _IMAGE_MAP.findall(new)
    if was != now:
        return (f"身份映射行被改动了：原文是 {was}，改写后是 {now}。"
                f"这几行决定哪张参考图是谁，动了会把别人的脸套上去")
    return ""


def _unfence(text: str) -> str:
    """模型有时会习惯性地套一层 ``` 围栏，去掉。别的一个字不动。"""
    t = (text or "").strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n?```$", t)
    return (m.group(1) if m else t).strip()


def soften(prompt: str, reason: str, *, llm, pj: Project, kind: str, key: str,
           round_no: int, log: Callable = print, origin: str = "") -> str:
    """让分析引擎改一版。改不出能用的就返回空字符串（调用方照常报原错）。

    **要的就是提示词本身，不套 JSON。** 早先是让它回
    `{"prompt": ..., "changed": [...], "kept": ...}`，结果多出一类
    纯粹是格式造成的失败：模型只把改动的那几句填进 `prompt`，
    一整段画面就没了。要纯文本之后这一类不存在了 —— 它回什么就是提示词。

    `prompt` 是这一轮要改的那版（第二轮起就是上一轮改完的结果）；
    `origin` 是最初的原文，只用来验收，见 `_check`。
    """
    from .stages import load_prompt, render
    origin = origin or prompt
    user = render(load_prompt("_soften", pj), {
        "REJECT_REASON": reason.strip()[:1500] or "（服务商没说具体原因）",
        "PROMPT": prompt,
    })
    try:
        new = _unfence(llm.chat(
            "你是提示词编辑。只输出提示词正文本身。", user,
            log=lambda m: log(f"    改写: {m}")))
    except Exception as exc:                                # noqa: BLE001
        log(f"    ⚠️ 提示词改写失败（{exc}）—— 按原来的错误处理")
        return ""
    bad = _check(origin, prompt, new)
    if bad:
        # **不能用就是不能用。** 硬用的代价是悄悄少一场戏 ——
        # 那比这一条失败严重得多，因为失败看得见。
        log(f"    ⚠️ 改写结果不能用：{bad}。按原来的错误处理，请人工改这一条")
        return ""
    _keep(pj, kind, key, round_no, prompt, new, reason, log)
    return new


def _keep(pj: Project, kind: str, key: str, round_no: int, old: str, new: str,
          reason: str, log: Callable) -> None:
    """把改写过程落盘，并在失败清单里留一条提醒。

    **自动改过的提示词必须看得见。** 不留痕的话，成片里某一场戏
    和剧本对不上时，没有任何线索指向「这段被自动改写过」。
    """
    path = pj.p("03_提示词", "自动改写", f"{key}_第{round_no}版.txt")
    head = (f"{kind}　{key}　第 {round_no} 次改写\n"
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"服务商拒绝的原话：{reason.strip()[:500]}\n"
            f"字数：{len(old)} → {len(new)}\n"
            + "=" * 60 + "\n【改写后 · 实际发出去的】\n" + new
            + "\n" + "=" * 60 + "\n【原文】\n" + old + "\n")
    write_text(path, head)
    log(f"    提示词已改写（第 {round_no} 版），原文和改动记在 {path}")
    diagnose.record(pj.root, diagnose.warn(
        "PROMPT_SOFTENED",
        f"{key} 的提示词被审核拒了，已自动改写第 {round_no} 版后重发。"
        f"服务商原话：{reason.strip()[:200]}",
        stage=kind, target=key,
        extra_fix=[f"改写前后的全文都在：{path}",
                   "对照一遍：该有的动作、人物、结果还在不在 —— "
                   "自动改写只允许换呈现方式，但这一条值得你自己看一眼"]))


def run_with_softening(gen: Callable, prompt: str, *, pj: Project, llm,
                       kind: str, key: str, rounds: int = DEFAULT_ROUNDS,
                       log: Callable = print):
    """`gen(prompt)` 出图/出片；被审核拦下就改写提示词重来。

    **每一轮接着上一轮改完的那版继续改**（`used`），不是每次都从原文重来 ——
    从原文重来等于把上一轮的进展扔掉，模型多半给回差不多的东西，
    白花一轮。而验收始终对着最初的 `prompt`（见 `_check`）。

    返回 gen 的结果。改写不成、或者不是审核问题，一律照常把原异常抛出去。
    """
    rounds = clamp_rounds(rounds)
    used = prompt
    for attempt in range(1, rounds + 2):
        try:
            return gen(used)
        except Exception as exc:                            # noqa: BLE001
            # 把这一轮实际发出去的提示词交给判定：报错里回显了它的话要先剔掉
            if (attempt > rounds or llm is None
                    or not is_content_rejection(exc, used)):
                raise
            log(f"  被审核拒了：{str(exc)[:200]}")
            log(f"  交给分析引擎优化提示词（第 {attempt}/{rounds} 轮）"
                + ("" if attempt == 1 else "，接着上一版继续"))
            new = soften(used, str(exc), llm=llm, pj=pj, kind=kind, key=key,
                         round_no=attempt, log=log, origin=prompt)
            if not new:
                raise
            used = new
    raise AssertionError("到不了这里")   # pragma: no cover


def clamp_rounds(v) -> int:
    """轮数。填多少是多少，只保证不是负数、不是垃圾值。

    **不设上限** —— 填 8 就跑 8 轮。防「越改越淡」靠的是验收那一关，
    不是限制次数。
    """
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return DEFAULT_ROUNDS
